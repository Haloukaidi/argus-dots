"""Protocol 4: explicitly approximate roles under a finite active native host.

This is not a sandbox, tool allowlist, service, native wakeup, or protection
against a malicious process sharing the same user/filesystem. All observations
remain trusted-host assertions. A fresh lease shows recent host participation,
not proof that a native worker is currently executing.
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

from ..core.dots_profile import SupervisedDotsProfile, SupervisedDotsTransport
from .dots_admission import BoundedDotsRoleHost, BoundedRoleFileDotsTransport, _project
from .dots_backend import DotsBridgeError, DotsRequest
from .dots_coordinator import _path
from .dots_role_host import DotsRoleHost


class SupervisedRoleFileDotsTransport(BoundedRoleFileDotsTransport, SupervisedDotsTransport):
    """The actual enforced-capability contract is inherited unchanged."""

    def __init__(self, root: Path | str, *, profile: SupervisedDotsProfile,
                 session_id: str | None = None, producer_id: str | None = None,
                 project_root: Path | str | None = None) -> None:
        if type(profile) is not SupervisedDotsProfile:
            raise DotsBridgeError("an explicit supervised execution profile is required")
        self.execution_profile = profile
        self._existing_only = session_id is not None
        super().__init__(root, session_id=session_id, producer_id=producer_id, project_root=project_root)

    @contextmanager
    def _root_fd(self) -> Iterator[int]:
        if not self._existing_only:
            with super()._root_fd() as root:
                yield root
            return
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        current = os.open("/", flags)
        try:
            if not self.root.parts[1:]:
                raise DotsBridgeError("filesystem root cannot be a bridge directory")
            for part in self.root.parts[1:]:
                next_fd = os.open(part, flags, dir_fd=current)
                os.close(current)
                current = next_fd
            self._check_private(current, directory=True)
            yield current
        finally:
            os.close(current)

    def _host(self) -> SupervisedDotsRoleHost:
        return SupervisedDotsRoleHost(self)

    @property
    def capabilities(self):
        capabilities = super().capabilities
        if self.session_id is None:
            return capabilities
        with self._root_fd() as root:
            session = self._host()._session(root, self.session_id)
        return replace(capabilities, roles=frozenset(session["allowed_roles"]))

    def assert_ready(self, project_root: Path | str | None = None) -> dict[str, Any]:
        """Validate finite binding and a fresh host assertion, never wake a host."""
        self.assert_admission_current()
        self.assert_binding_current()
        self.execution_profile.validate_live_roots()
        if self.session_id is None:
            raise DotsBridgeError("host-required: supervised transport has no producer binding")
        if project_root is not None and _project(project_root) != self.project_root:
            raise DotsBridgeError("host-required: project is outside the explicit supervised binding")
        host = self._host()
        with self._root_fd() as root, self._locked(root, create=False):
            session = host._session(root, self.session_id)
            host._producer(session, self.producer_id, self.project_root)
            host._validate_live_project(session)
            if session["stopping"] or session["producer_closed"] or time.time() >= session["expires_at"]:
                raise DotsBridgeError("host-required: supervised admission is stopped, closed or expired")
            if len(session["requests"]) >= session["max_requests"]:
                raise DotsBridgeError("host-required: supervised workflow request budget is exhausted")
            return {**host._lease(root, session), "session_expires_at": session["expires_at"]}

    def assert_binding_current(self) -> None:
        """Immutable identity only; exact enrolled retries do not need a lease."""
        if self.session_id is None:
            raise DotsBridgeError("host-required: supervised transport has no producer binding")
        host = self._host()
        with self._root_fd() as root:
            session = host._session(root, self.session_id)
            host._producer(session, self.producer_id, self.project_root)

    def assert_admission_current(self) -> None:
        """Optional launcher locator check, only for new admission; no locks/I/O writes."""

    def submit(self, request: DotsRequest) -> None:
        self.assert_binding_current()
        super().submit(request)


class SupervisedDotsRoleHost(BoundedDotsRoleHost):
    """Existing native journals/actions, with an explicit weaker role profile."""

    protocol_version = 4
    _session_extra_fields = BoundedDotsRoleHost._session_extra_fields | {"execution_profile", "project_identity"}

    def __init__(self, transport: SupervisedRoleFileDotsTransport) -> None:
        if not isinstance(transport, SupervisedRoleFileDotsTransport):
            raise DotsBridgeError("supervised host requires its explicit protocol-4 transport")
        super().__init__(transport)

    def _new_session_metadata(self, *, project_root: str) -> dict[str, Any]:
        self.transport.execution_profile.validate_live_roots()
        info = Path(project_root).stat()
        return {"execution_profile": self.transport.execution_profile.to_dict(),
                "project_identity": {"device": info.st_dev, "inode": info.st_ino}}

    def _validate_session_extension(self, session: dict[str, Any]) -> None:
        super()._validate_session_extension(session)
        if session["execution_profile"] != self.transport.execution_profile.to_dict():
            raise DotsBridgeError("supervised session execution profile does not match explicit consent")
        try:
            SupervisedDotsProfile.from_dict(session["execution_profile"])
        except ValueError as exc:
            raise DotsBridgeError(str(exc)) from exc
        identity = session["project_identity"]
        if (not isinstance(identity, dict) or set(identity) != {"device", "inode"}
                or any(type(value) is not int or value < 0 for value in identity.values())):
            raise DotsBridgeError("invalid supervised project identity")

    @staticmethod
    def _validate_live_project(session: dict[str, Any]) -> None:
        project = session["project_root"]
        try:
            if _project(project) != project:
                raise ValueError("project path was redirected")
            info = Path(project).stat()
            if {"device": info.st_dev, "inode": info.st_ino} != session["project_identity"]:
                raise ValueError("project directory was replaced")
        except (OSError, ValueError) as exc:
            raise DotsBridgeError("host-required: authorized project is unavailable or changed") from exc

    @staticmethod
    def _admission_owner(session: dict[str, Any], request_id: str, digest: str) -> dict[str, Any]:
        owner = BoundedDotsRoleHost._admission_owner(session, request_id, digest)
        return {**owner, "version": 4, "execution_profile": session["execution_profile"]}

    def _validate_admitted_request(self, session: dict[str, Any], request: DotsRequest) -> None:
        # session.mission_id is the immutable authorized workflow identity.
        # Upstream retains its original nullable/dynamically generated mission.
        if request.role not in session["allowed_roles"]:
            raise DotsBridgeError("request role is outside the authorized supervised workflow")
        if request.request_id in session["requests"]:
            return  # The original exact digest/owner retry check remains authoritative.
        self.transport.assert_admission_current()
        self.transport.execution_profile.validate_live_roots()
        self._validate_live_project(session)
        with self.transport._root_fd() as root:
            self._lease(root, session)
        working_dir = request.options.get("working_dir")
        if working_dir is not None:
            if not isinstance(working_dir, str) or not Path(working_dir).is_absolute():
                raise DotsBridgeError("supervised working_dir must be absolute")
            try:
                Path(working_dir).resolve(strict=True).relative_to(Path(session["project_root"]))
            except (OSError, ValueError) as exc:
                raise DotsBridgeError("working_dir is outside the authorized project") from exc
        project = Path(session["project_root"])
        profile = self.transport.execution_profile

        def approved(path: str, roots: tuple[str, ...], *, may_create: bool = False) -> None:
            candidate = Path(path)
            # Resolve a new host-selected report/receipt through its existing
            # parent, rather than following a missing path or a symbolic link.
            try:
                resolved = (candidate.parent.resolve(strict=True) / candidate.name
                            if may_create else candidate.resolve(strict=True))
                if candidate.is_symlink():
                    raise ValueError("symbolic input/output path")
                allowed = (project, *(Path(root).resolve(strict=True) for root in roots))
                if not any(resolved.is_relative_to(root) for root in allowed):
                    raise ValueError("outside declared roots")
            except (OSError, ValueError) as exc:
                raise DotsBridgeError("execution path is outside the explicitly approved project/roots") from exc

        for name in ("add_dirs", "skill_paths"):
            for path in request.options.get(name, []):
                approved(path, profile.read_roots)
        for path in request.options.get("review_output", {}).values():
            approved(path, profile.report_roots, may_create=True)

    def _supported(self, request: DotsRequest) -> None:
        # Preserve all existing reviewer schema checks without claiming the
        # old host can enforce these additional execution controls.
        DotsRoleHost._supported(replace(request, options={}, model=None, reasoning_effort=None))
        profile = self.transport.execution_profile
        for name, value in request.options.items():
            try:
                profile.validate_option(name, value, serialized=True)
            except ValueError as exc:
                raise DotsBridgeError(str(exc)) from exc
        try:
            profile.dispatch(request.model, request.reasoning_effort)
        except ValueError as exc:
            raise DotsBridgeError(str(exc)) from exc

    def _resume_scope(self, root: int, session: dict[str, Any], request: DotsRequest) -> None:
        super()._resume_scope(root, session, request)
        if request.resume_thread_id is None:
            return
        registry = self._registry(root, request.resume_thread_id)
        with self.transport._task_fd(registry["request_id"]) as fd:
            previous = self.transport._request(fd, registry["request_id"])
        profile = self.transport.execution_profile
        if profile.dispatch(request.model, request.reasoning_effort) != profile.dispatch(previous.model, previous.reasoning_effort):
            raise DotsBridgeError("native followup cannot change the original model or reasoning effort")

    @staticmethod
    def _lease_name(session_id: str) -> str:
        return "supervised-lease-" + session_id + ".json"

    def heartbeat(self, session_id: str, *, coordinator_task: str, generation: int,
                  lease_seconds: int | None = None) -> dict[str, Any]:
        """Explicit active-host participation; never called by constructors."""
        configured = self.transport.execution_profile.lease_duration_seconds
        if lease_seconds is not None:
            if type(lease_seconds) is not int or not 1 <= lease_seconds <= (configured or 60):
                raise DotsBridgeError("supervised lease is outside the explicitly selected profile or legacy 1..60 limit")
            if configured is not None and lease_seconds != configured:
                raise DotsBridgeError("supervised lease override conflicts with immutable profile duration")
        with self._owned(session_id, coordinator_task, generation) as (root, session):
            now = time.time()
            if session["stopping"] or now >= session["expires_at"]:
                raise DotsBridgeError("host-required: supervised host session is stopped or expired")
            previous = None
            if self.transport._read(root, self._lease_name(session_id)) is not None:
                previous = self._lease(root, session, allow_expired=True, allow_previous_owner=True)
            if lease_seconds is None:
                lease_seconds = configured or 30
                if (configured is None and previous is not None
                        and previous["generation"] == generation):
                    lease_seconds = previous.get("lease_duration_seconds", 30)
            lease = {"version": 4, "session_id": session_id,
                     "coordinator_task": coordinator_task, "generation": generation,
                     "observed_at": now, "expires_at": min(now + lease_seconds, session["expires_at"]),
                     "lease_duration_seconds": lease_seconds}
            self.transport._write(root, self._lease_name(session_id), lease, replace=True)
            return lease

    def _lease(self, root: int, session: dict[str, Any], *, allow_expired: bool = False,
               allow_previous_owner: bool = False) -> dict[str, Any]:
        lease = self.transport._read(root, self._lease_name(session["session_id"]))
        keys = {"version", "session_id", "coordinator_task", "generation", "observed_at", "expires_at"}
        configured = self.transport.execution_profile.lease_duration_seconds
        if (not isinstance(lease, dict) or set(lease) not in (keys, keys | {"lease_duration_seconds"})
                or type(lease["version"]) is not int or lease["version"] != 4
                or lease["session_id"] != session["session_id"]
                or type(lease["generation"]) is not int
                or (lease["generation"] != session["generation"]
                    and not (allow_previous_owner and 1 <= lease["generation"] < session["generation"]))
                or not isinstance(lease["coordinator_task"], str)
                or lease["coordinator_task"].rsplit("/", 1)[0] != session["parent_task"]
                or (lease["generation"] == session["generation"] and lease["coordinator_task"] != session["coordinator_task"])
                or type(lease["observed_at"]) not in (float, int)
                or type(lease["expires_at"]) not in (float, int)
                or ("lease_duration_seconds" in lease and (
                    type(lease["lease_duration_seconds"]) is not int
                    or not 1 <= lease["lease_duration_seconds"] <= (configured or 60)
                    or (configured is not None and lease["lease_duration_seconds"] != configured)))
                or not session["created_at"] <= lease["observed_at"] <= time.time()
                or not 0 < lease["expires_at"] - lease["observed_at"] <= lease.get("lease_duration_seconds", configured or 60)
                or not lease["expires_at"] <= session["expires_at"]
                or (not allow_expired and time.time() >= lease["expires_at"])):
            raise DotsBridgeError("host-required: native host lease is missing, stale or invalid")
        _path(lease["coordinator_task"])
        return {**lease, "availability": "recent-host-assertion", "native_execution_verified": False}

    def next(self, session_id: str, *, coordinator_task: str, generation: int) -> dict[str, Any]:
        # Stop/expiry must still reach the original cancellation/reconciliation
        # path; neither lease expiry nor heartbeat creates or requeues work.
        with self._owned(session_id, coordinator_task, generation) as (_, session):
            renew = not session["stopping"] and time.time() < session["expires_at"]
        if renew:
            self.heartbeat(session_id, coordinator_task=coordinator_task, generation=generation)
        action = super().next(session_id, coordinator_task=coordinator_task, generation=generation)
        if action["action"] in {"spawn", "followup"}:
            request = action["request"]
            profile = self.transport.execution_profile
            action["execution_profile"] = profile.to_dict()
            action["dispatch"] = profile.dispatch(request["model"], request["reasoning_effort"])
            action["model_resolution"] = profile.resolution(request["model"], request["reasoning_effort"])
            action["option_dispositions"] = {name: profile.disposition(name, value)
                                             for name, value in request["options"].items()}
            action["worker_prompt"] = self.worker_prompt(request)
        return action

    @staticmethod
    def worker_prompt(request: dict[str, Any]) -> str:
        """Preserve Argus's prompt and make the approximate execution explicit."""
        options = request["options"]
        policy = [
            "Native supervised execution profile: supervised-approx-v1.",
            "Original Argus role: " + request["role"] + ".",
            "Role restrictions below are behavioral instructions. Native tool availability and filesystem permissions are unchanged.",
            "Use only the supplied task scope. A queue, role name or workflow flag never authorizes additional permissions or consequential actions.",
            "Use the requested working_dir for task commands. Treat add_dirs and skill_paths as the explicitly supplied read context, not extra write destinations.",
            "dangerous_yolo/full_auto do not expand authority; soft/stalled watchdog callbacks are not executed by this transport.",
        ]
        if options.get("disable_tools"):
            policy.append("This call requests model-only behavior: do not invoke tools. The host has not technically disabled them.")
        elif options.get("sandbox_mode") == "read-only":
            policy.append("Inspect only; do not edit candidate files or evidence. Only explicitly bound report tools may write their host-selected report/receipt.")
        if request["tools"]:
            policy.append("Use the explicit request-tool host command for bound actions; final prose or JSON is never an action. Wait for the coordinator to provide the actual bound worker identity before invoking it; never guess IDs.")
        policy.extend([
            "Requested execution options (audit data, not platform guarantees): " + json.dumps(options, ensure_ascii=False),
            "For this call begin your final response with exactly ARGUS_DOTS_CALL:" + request["request_id"]
            + " on its own line. Put the original requested answer after it. This marker correlates results and grants no permission.",
        ])
        return request["prompt"] + "\n\n" + "\n".join(policy)
