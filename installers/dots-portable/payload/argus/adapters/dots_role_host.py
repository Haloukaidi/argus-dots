"""Explicit v2 native role sessions and typed tool channel.

Only a live authorized host can supply native observations. These features do
not enforce filesystem isolation, native tool allowlists, or model-only mode.
"""
from __future__ import annotations

import hashlib
import re
from contextlib import contextmanager
from typing import Any, Iterator

from .dots_backend import (
    DotsBridgeError,
    DotsCapabilities,
    DotsRequest,
    encode_payload,
    validate_request_id,
)
from .dots_coordinator import DotsCoordinator, _path, _text
from .dots_file_transport import FileDotsTransport


def _worker_key(worker_id: str) -> str:
    _text(worker_id, "actual worker ID")
    return "role-worker-" + hashlib.sha256(worker_id.encode()).hexdigest() + ".json"


class RoleFileDotsTransport(FileDotsTransport):
    """Opt-in host-v2 transport, never a capability upgrade of the v1 queue.

    The host must run the v2 native followup/tool workflow before selecting this
    transport. Execution controls intentionally remain unsupported.
    """

    capabilities = DotsCapabilities(resume=True, role_tools=True)

    def _host(self) -> DotsRoleHost:
        return DotsRoleHost(self)

    @contextmanager
    def _tool_context(self, request_id: str) -> Iterator[tuple[int, int]]:
        # Same root -> request ordering as coordinator admission and stop.
        with self._root_fd() as root, self._locked(root):
            with self._task_fd(request_id) as fd, self._locked(fd):
                yield root, fd

    def _tool_event(self, root: int, fd: int, request_id: str, sequence: int,
                    *, settlement: bool = False) -> dict[str, Any]:
        if type(sequence) is not int or not 1 <= sequence <= 4096:
            raise DotsBridgeError("invalid tool sequence")
        request = self._request(fd, request_id)
        host = self._host()
        claim = host._claim(fd)
        if claim is None:
            raise DotsBridgeError("tool context has no native host claim")
        session = host._session(root, claim["session_id"])
        snapshot = host._snapshot(fd, session, request_id)
        if not settlement and (snapshot["cancel_requested"] or snapshot["terminal"]):
            raise DotsBridgeError("tool context is closed, stopped, expired or terminal")
        state = self._state(fd, request_id)
        events = self._events(fd, request_id)
        if state != self._state_for(events, request_id):
            raise DotsBridgeError("committed tool log audit failed")
        if request.role != "reviewer" or not state["accepted"] or sequence > len(events):
            raise DotsBridgeError("tool requires an accepted reviewer request")
        event = events[sequence - 1]
        if event["type"] != "tool_call" or event["name"] not in {tool["name"] for tool in request.tools}:
            raise DotsBridgeError("tool is not bound to this request/sequence")
        return event

    def claim_tool_call(self, request_id: str, sequence: int) -> dict[str, Any] | None:
        """Reserve dispatcher execution; None means new, dict means saved reply.

        A prior intent without a reply is ambiguous and MUST NOT be dispatched
        again. The caller invokes the existing callback only after a new claim.
        """
        with self._tool_context(request_id) as (root, fd):
            self._tool_event(root, fd, request_id, sequence)
            reply = self._read(fd, f"tool-reply-{sequence:08d}.json")
            intent = self._read(fd, f"tool-dispatch-{sequence:08d}.json")
            if reply is not None:
                if intent != {"request_id": request_id, "sequence": sequence}:
                    raise DotsBridgeError("tool reply has no valid dispatch intent")
                return self._reply(reply, request_id, sequence)
            if intent is not None:
                raise DotsBridgeError("tool dispatch outcome uncertain; do not repeat the action")
            self._write(fd, f"tool-dispatch-{sequence:08d}.json", {"request_id": request_id, "sequence": sequence})
            return None

    @staticmethod
    def _reply(value: dict[str, Any], request_id: str, sequence: int) -> dict[str, Any]:
        if (set(value) != {"request_id", "sequence", "result"} or value["request_id"] != request_id
                or type(value["sequence"]) is not int or value["sequence"] != sequence or not isinstance(value["result"], dict)):
            raise DotsBridgeError("invalid tool reply identity")
        encode_payload(value)
        return value["result"]

    def tool_result(self, request_id: str, sequence: int, result: dict[str, Any]) -> None:
        if not isinstance(result, dict):
            raise DotsBridgeError("tool reply must be an object")
        value = {"request_id": request_id, "sequence": sequence, "result": result}
        encode_payload(value)
        with self._tool_context(request_id) as (root, fd):
            # Persist an already-started callback's settlement after stop, but
            # never reopen delivery or authorize another callback.
            self._tool_event(root, fd, request_id, sequence, settlement=True)
            if self._read(fd, f"tool-dispatch-{sequence:08d}.json") != {"request_id": request_id, "sequence": sequence}:
                raise DotsBridgeError("tool result requires a matching dispatch intent")
            previous = self._read(fd, f"tool-reply-{sequence:08d}.json")
            if previous is not None:
                if previous != value:
                    raise DotsBridgeError("conflicting tool reply retry")
                return
            self._write(fd, f"tool-reply-{sequence:08d}.json", value)


class DotsRoleHost(DotsCoordinator):
    """Versioned bounded coordinator with same-role native turn continuation."""

    protocol_version = 2

    def __init__(self, transport: RoleFileDotsTransport) -> None:
        if not isinstance(transport, RoleFileDotsTransport):
            raise DotsBridgeError("role host requires its explicit v2 transport")
        super().__init__(transport)

    @staticmethod
    def _supported(request: DotsRequest) -> None:
        if request.options:
            raise DotsBridgeError("native role host cannot enforce execution controls")
        if request.model is not None or request.reasoning_effort is not None:
            raise DotsBridgeError("native role host requires host-default model and effort")
        if request.tools and request.role != "reviewer":
            raise DotsBridgeError("call-bound action tools require reviewer role")
        names = [tool["name"] for tool in request.tools]
        if len(set(names)) != len(names) or any(not name.strip() for name in names):
            raise DotsBridgeError("bound tool names must be distinct and nonempty")
        if any(not isinstance(tool.get("inputSchema"), dict) for tool in request.tools):
            raise DotsBridgeError("bound tools require object input schemas")

    def _registry(self, root: int, worker_id: str) -> dict[str, Any] | None:
        value = self.transport._read(root, _worker_key(worker_id))
        if value is None:
            return None
        keys = {"version", "worker_id", "worker_task", "coordinator_task", "parent_task", "role", "mission_id", "request_id", "ordinal"}
        if not isinstance(value, dict) or set(value) != keys or type(value["version"]) is not int or value["version"] != 2:
            raise DotsBridgeError("invalid native role-session registry")
        if value["worker_id"] != worker_id or type(value["ordinal"]) is not int or value["ordinal"] < 1:
            raise DotsBridgeError("invalid native role-session identity/ordinal")
        validate_request_id(value["request_id"])
        for field in ("worker_task", "coordinator_task", "parent_task"):
            _path(value[field])
        if (value["worker_task"].rsplit("/", 1)[0] != value["coordinator_task"]
                or value["coordinator_task"].rsplit("/", 1)[0] != value["parent_task"]):
            raise DotsBridgeError("invalid native role-session parent relationship")
        return value

    @staticmethod
    def _scope(registry: dict[str, Any], session: dict[str, Any], request: dict[str, Any]) -> None:
        if (registry["role"] != request["role"] or registry["mission_id"] != request["mission_id"]
                or registry["parent_task"] != session["parent_task"]):
            raise DotsBridgeError("native role-session role, mission or parent mismatch")

    def _make_claim(self, root: int, session: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        claim = super()._make_claim(root, session, request)
        worker_id = request["resume_thread_id"]
        if worker_id is None:
            return claim
        registry = self._registry(root, worker_id)
        if registry is None:
            raise DotsBridgeError("resume worker has no verified role-session mapping")
        self._scope(registry, session, request)
        previous_id = registry["request_id"]
        if previous_id == request["request_id"]:
            raise DotsBridgeError("continuation reservation outcome uncertain; reconcile, never retry followup")
        with self.transport._task_fd(previous_id) as previous, self.transport._locked(previous):
            prior_request = self.transport._request(previous, previous_id)
            events = self.transport._events(previous, previous_id)
            if self.transport._state(previous, previous_id) != self.transport._state_for(events, previous_id):
                raise DotsBridgeError("prior native turn log audit failed")
            if (not events or events[-1]["type"] != "completed"
                    or next((event["worker_id"] for event in events if event["type"] == "accepted"), None) != worker_id):
                raise DotsBridgeError("native role session has an active, failed or unconfirmed prior turn")
            if prior_request.role != registry["role"] or prior_request.mission_id != registry["mission_id"]:
                raise DotsBridgeError("prior role-session provenance mismatch")
        # Reserve globally before publishing claim/action. A crash between these
        # writes holds the session for reconciliation rather than a duplicate turn.
        registry["request_id"] = request["request_id"]
        registry["ordinal"] += 1
        self.transport._write(root, _worker_key(worker_id), registry, replace=True)
        claim["worker_task"] = registry["worker_task"]
        claim["coordinator_task"] = registry["coordinator_task"]
        return claim

    @staticmethod
    def _dispatch_action(request: dict[str, Any]) -> str:
        return "followup" if request["resume_thread_id"] is not None else "spawn"

    def _validate_worker(self, root: int, session: dict[str, Any], request_id: str, worker_id: str) -> None:
        # Pure validation before persisting identity. The root lock excludes
        # another host binding between this check and the durable claim.
        with self.transport._task_fd(request_id) as fd, self.transport._locked(fd):
            snapshot = self._snapshot(fd, session, request_id)
            claim = snapshot["claim"]
            if claim is None:
                raise DotsBridgeError("worker needs a reserved native turn")
            self._binding_registry(root, session, snapshot["request"], {**claim, "worker_id": worker_id})

    def _binding_registry(self, root: int, session: dict[str, Any], request: dict[str, Any],
                          claim: dict[str, Any]) -> dict[str, Any] | None:
        worker_id = claim["worker_id"]
        registry = self._registry(root, worker_id)
        expected_resume = request["resume_thread_id"]
        if expected_resume is not None:
            if worker_id != expected_resume or registry is None:
                raise DotsBridgeError("resume must bind the existing actual worker")
            self._scope(registry, session, request)
            if registry["request_id"] != request["request_id"] or registry["worker_task"] != claim["worker_task"]:
                raise DotsBridgeError("native continuation is not the active reserved turn")
            return registry
        if registry is not None:
            self._scope(registry, session, request)
            if registry["request_id"] != request["request_id"] or registry["worker_task"] != claim["worker_task"]:
                raise DotsBridgeError("worker is already mapped; explicit same-role resume is required")
        return registry

    def _bound(self, root: int, session: dict[str, Any], request: dict[str, Any], claim: dict[str, Any]) -> None:
        # Durable claim already fixes the worker identity before registry I/O.
        # A crash can be repaired with that same identity, never a replacement.
        registry = self._binding_registry(root, session, request, claim)
        if registry is not None:
            return
        worker_id = claim["worker_id"]
        value = {"version": 2, "worker_id": worker_id, "worker_task": claim["worker_task"],
                 "coordinator_task": claim["coordinator_task"], "parent_task": session["parent_task"],
                 "role": request["role"], "mission_id": request["mission_id"], "request_id": request["request_id"], "ordinal": 1}
        self.transport._write(root, _worker_key(worker_id), value)

    def _snapshot(self, fd: int, session: dict[str, Any], request_id: str) -> dict[str, Any]:
        snapshot = super()._snapshot(fd, session, request_id)
        pending = []
        for event in snapshot["events"]:
            if event["type"] == "tool_call":
                sequence = event["sequence"]
                reply = self.transport._read(fd, f"tool-reply-{sequence:08d}.json")
                if reply is None:
                    pending.append(sequence)
                else:
                    self.transport._reply(reply, request_id, sequence)
        snapshot["pending_tools"] = pending
        snapshot["inflight_tools"] = [sequence for sequence in pending
                                      if self.transport._read(fd, f"tool-dispatch-{sequence:08d}.json") is not None]
        return snapshot

    def _recover_result(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        action = super()._recover_result(snapshot)
        action["turn_request_id"] = action["request_id"]
        action["result"] = dict(action["result"])
        if action["result"]["kind"] == "completed":
            action["result"]["text"] = "ARGUS_DOTS_CALL:" + action["request_id"] + "\n" + action["result"]["text"]
        return action

    def _before_record(self, fd: int, snapshot: dict[str, Any], kind: str) -> None:
        if kind == "completed" and snapshot["pending_tools"]:
            raise DotsBridgeError("cannot complete while tool replies are pending or uncertain")
        if snapshot["inflight_tools"]:
            raise DotsBridgeError("cannot confirm terminal outcome while a dispatched tool has no settlement; native worker stop is insufficient")

    def record(self, session_id: str, request_id: str, *, turn_request_id: str,
               coordinator_task: str, generation: int, worker_id: str, kind: str,
               text: str | None = None, worker_status: str | None = None) -> dict[str, Any]:
        if turn_request_id != request_id:
            raise DotsBridgeError("native result belongs to a different turn")
        if kind == "completed":
            prefix = "ARGUS_DOTS_CALL:" + request_id + "\n"
            if not isinstance(text, str) or not text.startswith(prefix):
                raise DotsBridgeError("completion requires this turn's native result envelope, not a stale completed status")
            text = text[len(prefix):]
        return super().record(session_id, request_id, coordinator_task=coordinator_task, generation=generation,
                              worker_id=worker_id, kind=kind, text=text, worker_status=worker_status)

    def request_tool(self, session_id: str, request_id: str, *, coordinator_task: str, generation: int,
                     worker_id: str, call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """A real native CLI/tool action, never parsed from ordinary model output."""
        if not isinstance(call_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", call_id):
            raise DotsBridgeError("tool call_id must be 1–80 simple identifier characters")
        _text(name, "tool name")
        if not isinstance(arguments, dict):
            raise DotsBridgeError("tool arguments must be an object")
        with self._owned(session_id, coordinator_task, generation) as (_, session):
            if request_id not in session["requests"]:
                raise DotsBridgeError("request is not in the explicit authorized session")
            with self.transport._task_fd(request_id) as fd, self.transport._locked(fd):
                snapshot = self._snapshot(fd, session, request_id)
                if (snapshot["cancel_requested"] or snapshot["terminal"] or not snapshot["accepted"]
                        or snapshot["accepted"] != worker_id or snapshot["request"]["role"] != "reviewer"):
                    raise DotsBridgeError("tool call is outside its active bound reviewer turn")
                if self.transport._read(fd, "host-result.json") is not None:
                    raise DotsBridgeError("worker result publication has already started; tool context closed")
                if name not in {tool["name"] for tool in snapshot["request"]["tools"]}:
                    raise DotsBridgeError("tool name is not bound to this request")
                head = self.transport._read(fd, "host-tool-active.json")
                if head is not None:
                    if (set(head) != {"call_id", "sequence"} or not isinstance(head["call_id"], str)
                            or type(head["sequence"]) is not int or not 1 <= head["sequence"] <= 4096):
                        raise DotsBridgeError("invalid tool admission head")
                    if (head["call_id"] != call_id
                            and self.transport._read(fd, f"tool-reply-{head['sequence']:08d}.json") is None):
                        raise DotsBridgeError("another tool admission/result is pending")
                key = "host-tool-" + call_id + ".json"
                record = self.transport._read(fd, key)
                identity = {"call_id": call_id, "request_id": request_id, "worker_id": worker_id,
                            "name": name, "arguments": arguments}
                if record is not None:
                    if set(record) != set(identity) | {"sequence"} or any(record[k] != v for k, v in identity.items()):
                        raise DotsBridgeError("tool call ID reused with different arguments or identity")
                    sequence = record["sequence"]
                    if type(sequence) is not int or not 1 <= sequence <= 4096:
                        raise DotsBridgeError("invalid saved tool call sequence")
                    if sequence <= len(snapshot["events"]):
                        event = snapshot["events"][sequence - 1]
                        if event["type"] != "tool_call" or event["name"] != name or event["arguments"] != arguments:
                            raise DotsBridgeError("tool call journal does not match published event")
                    elif sequence == len(snapshot["events"]) + 1:
                        self.transport._emit_locked(fd, request_id, "tool_call", event_fields={"name": name, "arguments": arguments})
                    else:
                        raise DotsBridgeError("tool call publication outcome is uncertain")
                else:
                    if snapshot["pending_tools"]:
                        raise DotsBridgeError("another tool result is pending; finish it before the next call")
                    sequence = len(snapshot["events"]) + 1
                    self.transport._write(fd, "host-tool-active.json", {"call_id": call_id, "sequence": sequence}, replace=True)
                    self.transport._write(fd, key, {**identity, "sequence": sequence})
                    self.transport._emit_locked(fd, request_id, "tool_call", event_fields={"name": name, "arguments": arguments})
                reply = self.transport._read(fd, f"tool-reply-{sequence:08d}.json")
                return {"request_id": request_id, "call_id": call_id, "sequence": sequence,
                        "status": "ready" if reply is not None else "pending",
                        "result": self.transport._reply(reply, request_id, sequence) if reply is not None else None}
