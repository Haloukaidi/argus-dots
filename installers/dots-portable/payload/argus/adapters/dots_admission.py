"""Opt-in bounded producer admission for a live native role host.

Argus still chooses roles, prompts, transitions and review decisions. This is
only a scoped submit channel into the existing native execution journal. It
does not call native tools, authenticate same-user processes, or wake a host.
"""
from __future__ import annotations

import math
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .dots_backend import (
    DOTS_ROLES,
    DotsBridgeError,
    DotsRequest,
    validate_request,
    validate_request_id,
)
from .dots_coordinator import _MAX_CONCURRENCY, _MAX_TASKS, _digest, _path, _text
from .dots_role_host import DotsRoleHost, RoleFileDotsTransport


def _project(value: str | Path) -> str:
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise DotsBridgeError("project_root must be an explicit absolute directory")
    path = path.resolve(strict=True)
    if not path.is_dir():
        raise DotsBridgeError("project_root must be a directory")
    return str(path)


class BoundedRoleFileDotsTransport(RoleFileDotsTransport):
    """Explicit producer binding; inherits v2 capabilities without adding any.

    An unbound instance is for host commands only and cannot submit work.
    The project identity is transport scope, not a filesystem sandbox.
    """

    def __init__(self, root: Path | str, *, session_id: str | None = None,
                 producer_id: str | None = None, project_root: Path | str | None = None) -> None:
        super().__init__(root)
        supplied = (session_id is not None, producer_id is not None, project_root is not None)
        if any(supplied) and not all(supplied):
            raise DotsBridgeError("producer binding requires session_id, producer_id and project_root")
        self.session_id = validate_request_id(session_id) if session_id is not None else None
        self.producer_id = producer_id
        self.project_root = _project(project_root) if project_root is not None else None

    def _host(self) -> BoundedDotsRoleHost:
        return BoundedDotsRoleHost(self)

    def _check_bound_request(self, request_id: str) -> None:
        if self.session_id is None:
            return  # The unbound native host retains its explicit host APIs.
        host = self._host()
        with self._root_fd() as root:
            # Scope fields and reservations are immutable. Reads are atomic;
            # do not acquire a second root lock inside typed-tool operations.
            session = host._session(root, self.session_id)
            host._producer(session, self.producer_id, self.project_root)
            owner = self._read(root, host._admission_name(request_id))
            digest = owner.get("request_digest") if isinstance(owner, dict) else None
            if (not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
                    or owner != host._admission_owner(session, request_id, digest)
                    or session["requests"].get(request_id, digest) != digest):
                raise DotsBridgeError("request is outside this bound producer admission")

    def poll(self, request_id: str, sequence: int) -> dict[str, Any] | None:
        self._check_bound_request(request_id)
        return super().poll(request_id, sequence)

    def cancel(self, request_id: str, reason: str) -> None:
        self._check_bound_request(request_id)
        super().cancel(request_id, reason)

    def poll_cancellation(self, request_id: str) -> dict[str, Any] | None:
        self._check_bound_request(request_id)
        return super().poll_cancellation(request_id)

    def finish(self, request_id: str) -> None:
        self._check_bound_request(request_id)
        super().finish(request_id)

    def inspect(self, request_id: str) -> dict[str, Any]:
        self._check_bound_request(request_id)
        return super().inspect(request_id)

    def emit(self, request_id: str, kind: str, **kwargs: Any) -> dict[str, Any]:
        if self.session_id is not None:
            raise DotsBridgeError("bound producer transport cannot emit native host events")
        return super().emit(request_id, kind, **kwargs)

    @contextmanager
    def _tool_context(self, request_id: str) -> Iterator[tuple[int, int]]:
        self._check_bound_request(request_id)
        with super()._tool_context(request_id) as context:
            yield context

    def submit(self, request: DotsRequest) -> None:
        if self.session_id is None:
            raise DotsBridgeError("bounded transport has no explicit producer binding")
        self._host().submit(self.session_id, request, producer_id=self.producer_id,
                            project_root=self.project_root)

    def close_producer(self) -> dict[str, Any]:
        if self.session_id is None:
            raise DotsBridgeError("bounded transport has no explicit producer binding")
        return self._host().close_producer(self.session_id, producer_id=self.producer_id,
                                           project_root=self.project_root)


class BoundedDotsRoleHost(DotsRoleHost):
    """Protocol 3: append exact producer submissions to one bounded session.

    Existing claim/bind/record/cancellation/resume/tool semantics are inherited.
    No role ordering or task-selection policy is added here.
    """

    protocol_version = 3
    _minimum_requests = 0
    _session_extra_fields = frozenset({"producer_id", "project_root", "mission_id",
                                      "allowed_roles", "max_requests", "producer_closed"})

    def __init__(self, transport: BoundedRoleFileDotsTransport) -> None:
        if not isinstance(transport, BoundedRoleFileDotsTransport):
            raise DotsBridgeError("bounded host requires its explicit protocol-3 transport")
        super().__init__(transport)

    def _validate_session_extension(self, session: dict[str, Any]) -> None:
        producer = session["producer_id"]
        if not isinstance(producer, str) or not producer.strip() or len(producer) > 128:
            raise DotsBridgeError("producer_id must be nonempty text of at most 128 characters")
        _text(session["mission_id"], "mission_id")
        project = session["project_root"]
        if not isinstance(project, str) or not Path(project).is_absolute() or ".." in Path(project).parts:
            raise DotsBridgeError("invalid bounded project identity")
        roles = session["allowed_roles"]
        if (not isinstance(roles, list) or not roles
                or any(not isinstance(role, str) or role not in DOTS_ROLES for role in roles)
                or roles != sorted(set(roles))):
            raise DotsBridgeError("allowed_roles must be a nonempty unique sorted role list")
        maximum = session["max_requests"]
        if type(maximum) is not int or not 1 <= maximum <= _MAX_TASKS or len(session["requests"]) > maximum:
            raise DotsBridgeError("invalid bounded request limit")
        if type(session["producer_closed"]) is not bool:
            raise DotsBridgeError("invalid producer closure")

    def create(self, *, parent_task: str, coordinator_task: str, producer_id: str,
               project_root: Path | str, mission_id: str, allowed_roles: list[str],
               max_requests: int, max_concurrency: int = 1,
               lifetime_seconds: float = 600) -> dict[str, Any]:
        """Parent grants a finite submit scope once; no queue discovery occurs."""
        _path(parent_task)
        _path(coordinator_task)
        if coordinator_task.rsplit("/", 1)[0] != parent_task:
            raise DotsBridgeError("coordinator must be a direct child of the authorizing parent")
        if type(max_concurrency) is not int or not 1 <= max_concurrency <= _MAX_CONCURRENCY:
            raise DotsBridgeError("max_concurrency must be an integer from 1 to 5")
        if (type(lifetime_seconds) not in (int, float) or not math.isfinite(lifetime_seconds)
                or not 0 < lifetime_seconds <= 3600):
            raise DotsBridgeError("session lifetime must be positive and at most 3600 seconds")
        if not isinstance(allowed_roles, list) or any(not isinstance(role, str) for role in allowed_roles):
            raise DotsBridgeError("allowed_roles must be an explicit list")
        if len(allowed_roles) != len(set(allowed_roles)):
            raise DotsBridgeError("allowed_roles must not contain duplicates")
        now = time.time()
        session = {"version": 3, "session_id": uuid.uuid4().hex, "parent_task": parent_task,
                   "coordinator_task": coordinator_task, "generation": 1, "created_at": now,
                   "expires_at": now + lifetime_seconds, "max_concurrency": max_concurrency,
                   "requests": {}, "stopping": False, "producer_id": producer_id,
                   "project_root": _project(project_root), "mission_id": mission_id,
                   "allowed_roles": sorted(allowed_roles), "max_requests": max_requests,
                   "producer_closed": False}
        self._validate_session_extension(session)
        with self.transport._root_fd() as root, self.transport._locked(root):
            self.transport._write(root, self._name(session["session_id"]), session)
        return session

    @staticmethod
    def _producer(session: dict[str, Any], producer_id: str, project_root: str) -> None:
        if producer_id != session["producer_id"] or project_root != session["project_root"]:
            raise DotsBridgeError("producer or project does not match the authorized binding")

    @staticmethod
    def _admission_name(request_id: str) -> str:
        return "admission-" + validate_request_id(request_id) + ".json"

    @staticmethod
    def _admission_owner(session: dict[str, Any], request_id: str, digest: str) -> dict[str, Any]:
        return {"version": 3, "session_id": session["session_id"], "request_id": request_id,
                "request_digest": digest, "producer_id": session["producer_id"],
                "project_root": session["project_root"], "mission_id": session["mission_id"]}

    def _check_request_admission(self, root: int, request_id: str,
                                 session: dict[str, Any] | None = None) -> None:
        if session is None:
            raise DotsBridgeError("bounded producer requests require an explicit session")
        expected = self._admission_owner(session, request_id, session["requests"][request_id])
        if self.transport._read(root, self._admission_name(request_id)) != expected:
            raise DotsBridgeError("request has no matching durable producer admission owner")

    def _resume_scope(self, root: int, session: dict[str, Any], request: DotsRequest) -> None:
        worker = request.resume_thread_id
        if worker is None:
            return
        registry = self._registry(root, worker)
        if registry is None or registry["request_id"] not in session["requests"]:
            raise DotsBridgeError("resume requires a worker from this bounded producer session")
        self._scope(registry, session, request.to_dict())
        for request_id in session["requests"]:
            with self.transport._task_fd(request_id) as fd, self.transport._locked(fd):
                prior = self._snapshot(fd, session, request_id)
                if (request_id == registry["request_id"]
                        and (prior["terminal"] != "completed" or prior["accepted"] != worker)):
                    raise DotsBridgeError("resume requires a confirmed completed prior turn")
                if (not prior["terminal"] and
                        (prior["request"]["resume_thread_id"] == worker
                         or (prior["claim"] and prior["claim"]["worker_id"] == worker))):
                    raise DotsBridgeError("worker already has an admitted active turn")

    def submit(self, session_id: str, request: DotsRequest, *, producer_id: str,
               project_root: str) -> dict[str, Any]:
        """Publish and enroll one exact producer call; an exact retry is a receipt.

        A crash before enrollment leaves unclaimed input, never executable work.
        A retry can adopt only the exact intact, unhandled request whose durable
        admission reservation names this session. Incomplete identity or
        conflicting bytes/ownership fail closed.
        """
        validate_request(request.to_dict())
        self._supported(request)
        digest = _digest(request)
        with self.transport._root_fd() as root, self.transport._locked(root):
            session = self._session(root, session_id)
            self._producer(session, producer_id, project_root)
            if request.mission_id != session["mission_id"] or request.role not in session["allowed_roles"]:
                raise DotsBridgeError("request mission or role is outside the authorized producer scope")
            owner = self._admission_owner(session, request.request_id, digest)
            reservation = self.transport._read(root, self._admission_name(request.request_id))
            if reservation is not None and reservation != owner:
                raise DotsBridgeError("request is reserved by a different producer session or digest")
            if request.request_id in session["requests"]:
                if session["requests"][request.request_id] != digest:
                    raise DotsBridgeError("conflicting producer retry for an enrolled request")
                with self.transport._task_fd(request.request_id) as fd, self.transport._locked(fd):
                    self._snapshot(fd, session, request.request_id)
                return self._receipt(session, request.request_id)
            if session["stopping"] or session["producer_closed"] or time.time() >= session["expires_at"]:
                raise DotsBridgeError("bounded producer admission is closed or expired")
            if len(session["requests"]) >= session["max_requests"]:
                raise DotsBridgeError("bounded producer request limit reached")
            if not session["created_at"] <= request.created_at <= time.time() or request.expires_at <= time.time():
                raise DotsBridgeError("request must be created within the authorized live session")
            self._resume_scope(root, session, request)
            if reservation is None:
                try:
                    with self.transport._task_fd(request.request_id):
                        raise DotsBridgeError("unowned existing request cannot be adopted by a producer")
                except FileNotFoundError:
                    pass
                # This reservation precedes request publication and survives a
                # lost response. Another session can never adopt the orphan.
                self.transport._write(root, self._admission_name(request.request_id), owner)
            try:
                # Deliberately bypass the bound transport's submit override.
                RoleFileDotsTransport.submit(self.transport, request)
            except FileExistsError:
                if reservation is None:
                    raise DotsBridgeError("unowned request publication collided; explicit recovery is required") from None
                with self.transport._task_fd(request.request_id) as fd, self.transport._locked(fd):
                    if _digest(self.transport._request(fd, request.request_id)) != digest:
                        raise DotsBridgeError("existing request conflicts with the producer submission") from None
                    if self._claim(fd) or self.transport._closed(fd) or self.transport._events(fd, request.request_id):
                        raise DotsBridgeError("existing request was already handled outside this producer admission") from None
                    if self.transport._state(fd, request.request_id) != self.transport._state_for([], request.request_id):
                        raise DotsBridgeError("invalid unpublished request head") from None
            if time.time() >= min(session["expires_at"], request.expires_at):
                raise DotsBridgeError("producer admission expired during request publication")
            session["requests"][request.request_id] = digest
            self.transport._write(root, self._name(session_id), session, replace=True)
            return self._receipt(session, request.request_id)

    @staticmethod
    def _receipt(session: dict[str, Any], request_id: str) -> dict[str, Any]:
        return {"session_id": session["session_id"], "request_id": request_id,
                "request_digest": session["requests"][request_id],
                "ordinal": list(session["requests"]).index(request_id) + 1}

    def close_producer(self, session_id: str, *, producer_id: str,
                       project_root: str) -> dict[str, Any]:
        """Seal new admission after upstream finishes; do not cancel accepted work."""
        with self.transport._root_fd() as root, self.transport._locked(root):
            session = self._session(root, session_id)
            self._producer(session, producer_id, project_root)
            if not session["producer_closed"]:
                session["producer_closed"] = True
                self.transport._write(root, self._name(session_id), session, replace=True)
            return session

    def _done_action(self, session: dict[str, Any], terminal_count: int) -> dict[str, Any]:
        if session["producer_closed"] or session["stopping"] or time.time() >= session["expires_at"]:
            return super()._done_action(session, terminal_count)
        return {"action": "wait", "active_count": 0, "pending_count": 0,
                "reason": "authorized producer is still open; an empty queue is not completion"}
