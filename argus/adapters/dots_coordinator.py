"""Bounded, explicit host coordination for the opt-in dot file bridge.

This journal never invokes agents. An authorized native host consumes actions,
uses its real platform tools, then records observations. Claims prevent replay;
unknown spawn outcomes require reconciliation, never an automatic retry.
"""
from __future__ import annotations

import hashlib
import math
import re
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from .dots_backend import DotsBridgeError, DotsRequest, encode_payload, validate_request_id
from .dots_file_transport import FileDotsTransport

_VERSION = 1
_MAX_TASKS = 64
_MAX_CONCURRENCY = 5
_TASK_PATH = re.compile(r"/root(?:/[a-z0-9_]+)*\Z")


def _path(value: str) -> str:
    if not isinstance(value, str) or not _TASK_PATH.fullmatch(value):
        raise DotsBridgeError("expected a canonical native task path under /root")
    return value


def _text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DotsBridgeError(label + " must be nonempty text")
    return value


def _digest(request: DotsRequest) -> str:
    return hashlib.sha256(encode_payload(request.to_dict())).hexdigest()


def _supported(request: DotsRequest) -> None:
    # These are limits of this host workflow, not capabilities it can manufacture.
    if request.options or request.tools or request.resume_thread_id is not None:
        raise DotsBridgeError("native coordinator only admits plain text requests; execution controls, role tools and role-session resume are unsupported")
    if request.model is not None or request.reasoning_effort is not None:
        raise DotsBridgeError("native coordinator requires host-default model and effort")


class DotsCoordinator:
    """One explicitly enrolled finite task list, owned by one native coordinator.

    The root lock serializes admission across sessions. Per-request claims bind
    immutable request digests, parent/coordinator identities, and actual worker
    identities. All journal access reuses the bridge's private no-follow I/O.
    Identity fields are authorized-host assertions, not platform authentication.
    """

    protocol_version = _VERSION

    @staticmethod
    def _supported(request: DotsRequest) -> None:
        _supported(request)

    def __init__(self, transport: FileDotsTransport) -> None:
        self.transport = transport

    @staticmethod
    def _name(session_id: str) -> str:
        return "coordinator-" + validate_request_id(session_id) + ".json"

    def create(self, request_ids: list[str], *, parent_task: str, coordinator_task: str,
               max_concurrency: int = 1, lifetime_seconds: float = 600) -> dict[str, Any]:
        """Enroll exactly these already-authorized requests, never scan a directory."""
        _path(parent_task)
        _path(coordinator_task)
        if coordinator_task.rsplit("/", 1)[0] != parent_task:
            raise DotsBridgeError("coordinator must be a direct child of the authorizing parent")
        if not 1 <= len(request_ids) <= _MAX_TASKS or len(set(request_ids)) != len(request_ids):
            raise DotsBridgeError("enroll 1 to 64 distinct explicit request IDs")
        if type(max_concurrency) is not int or not 1 <= max_concurrency <= _MAX_CONCURRENCY:
            raise DotsBridgeError("max_concurrency must be an integer from 1 to 5; reserve native slots for the parent and coordinator")
        if (type(lifetime_seconds) not in (int, float) or not math.isfinite(lifetime_seconds)
                or not 0 < lifetime_seconds <= 3600):
            raise DotsBridgeError("session lifetime must be positive and at most 3600 seconds")
        session_id = uuid.uuid4().hex
        with self.transport._root_fd() as root, self.transport._locked(root):
            requests = {}
            for request_id in request_ids:
                with self.transport._task_fd(request_id) as fd, self.transport._locked(fd):
                    request = self.transport._request(fd, request_id)
                    self._supported(request)
                    if (self.transport._closed(fd) or self.transport._events(fd, request_id)
                            or request.expires_at <= time.time() or self._claim(fd) is not None):
                        raise DotsBridgeError("request already claimed, handled, closed or expired: " + request_id)
                    requests[request_id] = _digest(request)
            now = time.time()
            session = {"version": self.protocol_version, "session_id": session_id, "parent_task": parent_task,
                       "coordinator_task": coordinator_task, "generation": 1, "created_at": now,
                       "expires_at": now + lifetime_seconds, "max_concurrency": max_concurrency,
                       "requests": requests, "stopping": False}
            self.transport._write(root, self._name(session_id), session)
            return session

    def _session(self, root: int, session_id: str) -> dict[str, Any]:
        value = self.transport._read(root, self._name(session_id))
        keys = {"version", "session_id", "parent_task", "coordinator_task", "generation", "created_at",
                "expires_at", "max_concurrency", "requests", "stopping"}
        if not isinstance(value, dict) or set(value) != keys or type(value["version"]) is not int or value["version"] != self.protocol_version or value["session_id"] != session_id:
            raise DotsBridgeError("invalid or missing coordinator session")
        _path(value["parent_task"])
        _path(value["coordinator_task"])
        if value["coordinator_task"].rsplit("/", 1)[0] != value["parent_task"]:
            raise DotsBridgeError("invalid coordinator parent relationship")
        if (type(value["generation"]) is not int or value["generation"] < 1
                or type(value["max_concurrency"]) is not int or not 1 <= value["max_concurrency"] <= _MAX_CONCURRENCY
                or type(value["stopping"]) is not bool or not isinstance(value["requests"], dict)
                or not 1 <= len(value["requests"]) <= _MAX_TASKS):
            raise DotsBridgeError("invalid coordinator session fields")
        for field in ("created_at", "expires_at"):
            if type(value[field]) not in (int, float) or not math.isfinite(value[field]):
                raise DotsBridgeError("invalid coordinator deadline")
        if not 0 < value["expires_at"] - value["created_at"] <= 3600:
            raise DotsBridgeError("invalid coordinator lifetime")
        for request_id, digest in value["requests"].items():
            validate_request_id(request_id)
            if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise DotsBridgeError("invalid enrolled request digest")
        return value

    @contextmanager
    def _owned(self, session_id: str, coordinator_task: str, generation: int) -> Iterator[tuple[int, dict[str, Any]]]:
        with self.transport._root_fd() as root, self.transport._locked(root):
            session = self._session(root, session_id)
            if coordinator_task != session["coordinator_task"] or type(generation) is not int or generation != session["generation"]:
                raise DotsBridgeError("stale or wrong coordinator owner/generation")
            yield root, session

    def _claim(self, fd: int) -> dict[str, Any] | None:
        claim = self.transport._read(fd, "host-claim.json")
        if claim is None:
            return None
        keys = {"version", "session_id", "request_id", "request_digest", "parent_task", "coordinator_task",
                "generation", "worker_task", "worker_id"}
        if not isinstance(claim, dict) or set(claim) != keys or type(claim["version"]) is not int or claim["version"] != self.protocol_version:
            raise DotsBridgeError("invalid host claim")
        validate_request_id(claim["session_id"])
        validate_request_id(claim["request_id"])
        for key in ("parent_task", "coordinator_task", "worker_task"):
            _path(claim[key])
        if (claim["coordinator_task"].rsplit("/", 1)[0] != claim["parent_task"]
                or claim["worker_task"].rsplit("/", 1)[0] != claim["coordinator_task"]
                or type(claim["generation"]) is not int or claim["generation"] < 1
                or not isinstance(claim["request_digest"], str) or not re.fullmatch(r"[a-f0-9]{64}", claim["request_digest"])
                or (claim["worker_id"] is not None and (not isinstance(claim["worker_id"], str) or not claim["worker_id"].strip()))):
            raise DotsBridgeError("invalid host claim fields")
        return claim

    def _snapshot(self, fd: int, session: dict[str, Any], request_id: str) -> dict[str, Any]:
        if request_id not in session["requests"]:
            raise DotsBridgeError("request is not in the explicit authorized session")
        request = self.transport._request(fd, request_id)
        if _digest(request) != session["requests"][request_id]:
            raise DotsBridgeError("enrolled request changed; authorization no longer matches")
        self._supported(request)
        events = self.transport._events(fd, request_id)
        if self.transport._state(fd, request_id) != self.transport._state_for(events, request_id):
            raise DotsBridgeError("committed log audit failed")
        claim = self._claim(fd)
        if claim and (claim["request_id"] != request_id or claim["request_digest"] != _digest(request)
                      or claim["session_id"] != session["session_id"] or claim["parent_task"] != session["parent_task"]):
            raise DotsBridgeError("request is claimed by a different session or identity")
        accepted = next((e["worker_id"] for e in events if e["type"] == "accepted"), None)
        if accepted and (not claim or accepted != claim["worker_id"]):
            raise DotsBridgeError("accepted worker does not match the host claim")
        terminal = events[-1]["type"] if events and events[-1]["type"] in {"completed", "failed", "cancelled"} else None
        closed = self.transport._closed(fd)
        saved = self.transport._read(fd, "host-result.json")
        if saved is not None:
            if (set(saved) != {"kind", "text", "worker_id", "worker_status"}
                    or saved["kind"] not in {"completed", "failed", "cancelled"}
                    or not isinstance(saved["text"], str) or not saved["text"].strip()
                    or saved["worker_status"] not in {"completed", "failed", "interrupted", "idle"}
                    or not claim or saved["worker_id"] != claim["worker_id"]
                    or (saved["kind"] == "completed" and saved["worker_status"] != "completed")):
                raise DotsBridgeError("invalid pending host result journal")
        cancel = session["stopping"] or time.time() >= min(session["expires_at"], request.expires_at) or bool(closed)
        return {"request": request.to_dict(), "claim": claim, "accepted": accepted, "terminal": terminal,
                "cancel_requested": cancel, "closed": closed, "events": events,
                "pending_result": saved if not terminal else None}

    def status(self, session_id: str) -> dict[str, Any]:
        with self.transport._root_fd() as root, self.transport._locked(root):
            session = self._session(root, session_id)
            tasks = {}
            for request_id in session["requests"]:
                with self.transport._task_fd(request_id) as fd, self.transport._locked(fd):
                    snapshot = self._snapshot(fd, session, request_id)
                    snapshot.pop("request")
                    tasks[request_id] = snapshot
            return {"session": session, "tasks": tasks}

    def next(self, session_id: str, *, coordinator_task: str, generation: int) -> dict[str, Any]:
        """Return one action; persist dispatch intent BEFORE returning a spawn action.

        A lost response is uncertain: subsequent calls return reconcile, never
        spawn again. No lease expiry or retry can reset a dispatch intent.
        """
        with self._owned(session_id, coordinator_task, generation) as (root, session):
            pending = []
            active = []
            terminal_count = 0
            for request_id in session["requests"]:
                with self.transport._task_fd(request_id) as fd, self.transport._locked(fd):
                    snapshot = self._snapshot(fd, session, request_id)
                    claim = snapshot["claim"]
                    if snapshot["terminal"]:
                        terminal_count += 1
                        continue
                    if snapshot["cancel_requested"]:
                        if not claim:
                            self.transport._emit_locked(fd, request_id, "cancelled", text="Authorized session closed before dispatch; no worker was launched")
                            terminal_count += 1
                            continue
                        if claim["worker_id"] is not None:
                            return {"action": "cancel", "request_id": request_id, "claim": claim,
                                    "reason": "request cancelled, expired, or coordinator stopped"}
                        return {"action": "reconcile", "request_id": request_id, "claim": claim,
                                "reason": "dispatch outcome unknown; locate worker and stop it before acknowledgement"}
                    if claim:
                        active.append(snapshot)
                    else:
                        pending.append(request_id)
            for snapshot in active:
                if not snapshot["accepted"]:
                    return {"action": "reconcile", "request_id": snapshot["request"]["request_id"],
                            "claim": snapshot["claim"], "reason": "dispatch outcome or acceptance publication needs reconciliation; never respawn"}
                if snapshot["pending_result"] is not None:
                    return self._recover_result(snapshot)
            if pending and len(active) < session["max_concurrency"]:
                request_id = pending[0]
                with self.transport._task_fd(request_id) as fd, self.transport._locked(fd):
                    snapshot = self._snapshot(fd, session, request_id)
                    # The producer can cancel while other requests are inspected.
                    if snapshot["cancel_requested"]:
                        return {"action": "wait", "reason": "admission changed; call next again"}
                    claim = self._make_claim(root, session, snapshot["request"])
                    self.transport._write(fd, "host-claim.json", claim)
                    return {"action": self._dispatch_action(snapshot["request"]), "request_id": request_id,
                            "claim": claim, "request": snapshot["request"]}
            if terminal_count == len(session["requests"]):
                return {"action": "done", "terminal_count": terminal_count}
            return {"action": "wait", "active_count": len(active), "pending_count": len(pending)}

    def _recover_result(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        return {"action": "recover_result", "request_id": snapshot["request"]["request_id"],
                "claim": snapshot["claim"], "result": snapshot["pending_result"],
                "reason": "re-publish the exact validated host observation; never start a new native turn"}

    def _make_claim(self, root: int, session: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        request_id = request["request_id"]
        return {"version": self.protocol_version, "session_id": session["session_id"], "request_id": request_id,
                "request_digest": session["requests"][request_id], "parent_task": session["parent_task"],
                "coordinator_task": session["coordinator_task"], "generation": session["generation"],
                "worker_task": session["coordinator_task"] + "/execute_dots_" + request_id, "worker_id": None}

    @staticmethod
    def _dispatch_action(request: dict[str, Any]) -> str:
        return "spawn"

    def _validate_worker(self, root: int, session: dict[str, Any], request_id: str, worker_id: str) -> None:
        for other_id in session["requests"]:
            if other_id == request_id:
                continue
            with self.transport._task_fd(other_id) as other_fd, self.transport._locked(other_fd):
                other = self._claim(other_fd)
                if other and other["worker_id"] == worker_id:
                    raise DotsBridgeError("worker identity already belongs to another request; role-session reuse is unsupported")

    def _bound(self, root: int, session: dict[str, Any], request: dict[str, Any], claim: dict[str, Any]) -> None:
        pass

    def bind(self, session_id: str, request_id: str, *, coordinator_task: str, generation: int,
             worker_id: str, worker_task: str) -> dict[str, Any]:
        """Record an actual spawn/list result, including recovery of a known worker."""
        _text(worker_id, "actual worker ID")
        _path(worker_task)
        with self._owned(session_id, coordinator_task, generation) as (root, session):
            if request_id not in session["requests"]:
                raise DotsBridgeError("request is not in the explicit authorized session")
            self._validate_worker(root, session, request_id, worker_id)
            with self.transport._task_fd(request_id) as fd, self.transport._locked(fd):
                snapshot = self._snapshot(fd, session, request_id)
                claim = snapshot["claim"]
                if not claim or worker_task != claim["worker_task"]:
                    raise DotsBridgeError("worker must match the reserved direct-child task path")
                if claim["worker_id"] not in (None, worker_id):
                    raise DotsBridgeError("claim already bound to another worker")
                if snapshot["terminal"]:
                    raise DotsBridgeError("request already terminal")
                # Mapping is durable first. A crash before accepted is repaired
                # by binding this SAME observed worker; never another spawn.
                claim["worker_id"] = worker_id
                self.transport._write(fd, "host-claim.json", claim, replace=True)
                self._bound(root, session, snapshot["request"], claim)
                if snapshot["cancel_requested"]:
                    return {"action": "cancel", "request_id": request_id, "claim": claim}
                if not snapshot["accepted"]:
                    self.transport._emit_locked(fd, request_id, "accepted", worker_id=worker_id)
                return {"action": "bound", "request_id": request_id, "claim": claim}

    def _before_record(self, fd: int, snapshot: dict[str, Any], kind: str) -> None:
        pass

    def record(self, session_id: str, request_id: str, *, coordinator_task: str, generation: int,
               worker_id: str, kind: str, text: str | None = None,
               worker_status: str | None = None) -> dict[str, Any]:
        """Record a real worker observation, never interpret approval prose.

        Completion writes the final message and terminal marker in one locked
        operation. Exact duplicates are idempotent; conflicting retries fail.
        """
        if kind not in {"completed", "failed", "cancelled"}:
            raise DotsBridgeError("host result must be completed, failed or cancelled")
        _text(text, "actual worker result")
        if worker_status not in {"completed", "failed", "interrupted", "idle"}:
            raise DotsBridgeError("a terminal/idle status observed with native tools is required")
        if kind == "completed" and worker_status != "completed":
            raise DotsBridgeError("completion requires a completed native worker")
        with self._owned(session_id, coordinator_task, generation) as (_, session):
            if request_id not in session["requests"]:
                raise DotsBridgeError("request is not in the explicit authorized session")
            with self.transport._task_fd(request_id) as fd, self.transport._locked(fd):
                snapshot = self._snapshot(fd, session, request_id)
                claim = snapshot["claim"]
                if not claim or not claim["worker_id"] or claim["worker_id"] != worker_id:
                    raise DotsBridgeError("result must identify the actual bound worker")
                self._before_record(fd, snapshot, kind)
                expected = {"kind": kind, "text": text, "worker_id": worker_id, "worker_status": worker_status}
                saved = self.transport._read(fd, "host-result.json")
                replace_pending = saved is not None and not snapshot["terminal"] and kind == "cancelled" and snapshot["cancel_requested"]
                if saved is not None and saved != expected and not replace_pending:
                    raise DotsBridgeError("conflicting host result retry")
                if snapshot["terminal"]:
                    if saved == expected and snapshot["terminal"] == kind:
                        return {"action": "recorded", "request_id": request_id, "terminal": kind}
                    raise DotsBridgeError("request already has a different terminal result")
                if snapshot["cancel_requested"] and kind != "cancelled":
                    raise DotsBridgeError("late result rejected; observe worker stopped and acknowledge cancellation")
                if kind != "cancelled" and not snapshot["accepted"]:
                    raise DotsBridgeError("bind acceptance before recording output")
                if saved is None or replace_pending:
                    self.transport._write(fd, "host-result.json", expected, replace=replace_pending)
                if kind == "completed":
                    messages = [event["text"] for event in snapshot["events"] if event["type"] == "message"]
                    if messages and messages != [text]:
                        raise DotsBridgeError("unexpected host output during result recovery")
                    if not messages:
                        self.transport._emit_locked(fd, request_id, "message", text=text)
                    self.transport._emit_locked(fd, request_id, "completed")
                else:
                    self.transport._emit_locked(fd, request_id, kind, text=text)
                return {"action": "recorded", "request_id": request_id, "terminal": kind}

    def abandon(self, session_id: str, request_id: str, *, coordinator_task: str, generation: int,
                spawn_status: str, text: str) -> dict[str, Any]:
        """Terminate, never requeue, a dispatch confirmed by native tools as not created.

        Mere absence from a list, a timeout, or a lost tool response is not this
        evidence. Those remain uncertain and require parent reconciliation.
        """
        if spawn_status != "not_created":
            raise DotsBridgeError("only a confirmed not-created spawn can be abandoned")
        _text(text, "native tool failure evidence")
        with self._owned(session_id, coordinator_task, generation) as (_, session):
            if request_id not in session["requests"]:
                raise DotsBridgeError("request is not in the explicit authorized session")
            with self.transport._task_fd(request_id) as fd, self.transport._locked(fd):
                snapshot = self._snapshot(fd, session, request_id)
                claim = snapshot["claim"]
                if not claim or claim["worker_id"] or snapshot["accepted"] or snapshot["terminal"]:
                    raise DotsBridgeError("abandon requires an unbound, nonterminal dispatch intent")
                kind = "cancelled" if snapshot["cancel_requested"] else "failed"
                self.transport._emit_locked(fd, request_id, kind, text=text)
                return {"action": "recorded", "request_id": request_id, "terminal": kind}

    def stop(self, session_id: str, *, coordinator_task: str, generation: int) -> dict[str, Any]:
        """Close admission. next still returns stop/reconcile actions until drained."""
        with self._owned(session_id, coordinator_task, generation) as (root, session):
            session["stopping"] = True
            self.transport._write(root, self._name(session_id), session, replace=True)
            return session

    def handoff(self, session_id: str, *, parent_task: str, previous_generation: int,
                coordinator_task: str) -> dict[str, Any]:
        """Explicit parent recovery only, after the former coordinator is stopped.

        Old worker mappings remain unchanged. A new coordinator must reconcile
        them with native tools (ask its parent if cross-branch access is absent).
        This resumes host bookkeeping, never an Argus role/model session.
        """
        _path(coordinator_task)
        with self.transport._root_fd() as root, self.transport._locked(root):
            session = self._session(root, session_id)
            if (parent_task != session["parent_task"] or type(previous_generation) is not int
                    or previous_generation != session["generation"]):
                raise DotsBridgeError("only the recorded parent with the current generation can hand off")
            if coordinator_task.rsplit("/", 1)[0] != parent_task or coordinator_task == session["coordinator_task"]:
                raise DotsBridgeError("replacement coordinator must be a different direct child of the same parent")
            session["coordinator_task"] = coordinator_task
            session["generation"] += 1
            self.transport._write(root, self._name(session_id), session, replace=True)
            return session
