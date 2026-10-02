"""Opt-in dot host bridge, not a provider CLI or a public dot service API.

A transport carries requests and events; an independently authorized, online
host must review each request and dispatch it. Nothing in this module grants
that host additional authority or discovers credentials.
"""
from __future__ import annotations

import json
import logging
import math
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Callable, Iterator, Protocol

from ..core.models import RunnerOptions, RunnerResult
from ..core.role_tool_bridge import ToolBridgeBusy
from ..core.stop_kinds import stop_kind_from_external_interrupt

log = logging.getLogger(__name__)
PROTOCOL_VERSION = 1
MAX_PAYLOAD_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 8 * MAX_PAYLOAD_BYTES
TERMINAL_EVENTS = frozenset({"completed", "failed", "cancelled"})
USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens")


DOTS_ROLES = frozenset({"manager", "engineer", "reviewer", "planner", "curator"})
EXECUTION_FIELDS = frozenset({"working_dir", "add_dirs", "skill_paths", "review_output", "live_search",
                              "sandbox_mode", "force_safe_mode", "disable_tools", "output_schema",
                              "isolate_workdir", "full_auto"})


@dataclass(frozen=True)
class DotsCapabilities:
    """Host-enforced guarantees, not prompt requests or inferred permissions.

    Only a transport whose receiving host really enforces a capability may
    advertise it. The supervised file bridge advertises none of these.
    """

    roles: frozenset[str] = DOTS_ROLES
    options: frozenset[str] = frozenset()
    resume: bool = False
    role_tools: bool = False


class DotsBridgeError(ValueError):
    """Invalid or unavailable bridge input; never fall back to another agent."""


@dataclass(frozen=True)
class DotsRequest:
    request_id: str
    prompt: str
    run_label: str
    model: str | None
    reasoning_effort: str | None
    created_at: float
    expires_at: float
    role: str = "manager"
    mission_id: str | None = None
    resume_thread_id: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    tools: list[dict[str, Any]] = field(default_factory=list)
    protocol_version: int = PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DotsTransport(Protocol):
    """Nonblocking transport. Methods must return promptly and never dispatch.

    ``poll`` returns exactly the requested sequence, or None while pending.
    ``cancel`` closes admission/results and asks the host to stop its worker;
    it does NOT assert that an off-process worker has actually stopped.
    An optional ``poll_cancellation(request_id)`` may return only an actual
    ``cancelled`` event after closure, or None while acknowledgement is pending.
    It has the same nonblocking contract; transports without it remain supported.
    """

    capabilities: DotsCapabilities

    def tool_result(self, request_id: str, sequence: int, result: dict[str, Any]) -> None: ...
    def submit(self, request: DotsRequest) -> None: ...
    def poll(self, request_id: str, sequence: int) -> dict[str, Any] | None: ...
    def cancel(self, request_id: str, reason: str) -> None: ...
    def finish(self, request_id: str) -> None: ...


def validate_request_id(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
        raise DotsBridgeError("request_id must be 32 lowercase hexadecimal characters")
    return value


def encode_payload(value: dict[str, Any]) -> bytes:
    data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(data) > MAX_PAYLOAD_BYTES:
        raise DotsBridgeError("bridge payload exceeds the 1 MiB safety limit")
    return data


def validate_request(value: Any) -> DotsRequest:
    if not isinstance(value, dict) or set(value) != {f.name for f in fields(DotsRequest)}:
        raise DotsBridgeError("invalid request fields")
    validate_request_id(value["request_id"])
    if not isinstance(value["role"], str) or value["role"] not in DOTS_ROLES:
        raise DotsBridgeError("unknown dots role")
    if value["mission_id"] is not None and (not isinstance(value["mission_id"], str) or not value["mission_id"].strip()):
        raise DotsBridgeError("mission_id must be nonempty text or null")
    if value["resume_thread_id"] is not None and not isinstance(value["resume_thread_id"], str):
        raise DotsBridgeError("resume_thread_id must be text or null")
    if not isinstance(value["options"], dict) or not isinstance(value["tools"], list):
        raise DotsBridgeError("invalid role options or tools")
    if not set(value["options"]) <= EXECUTION_FIELDS:
        raise DotsBridgeError("unsupported serialized execution fields")
    if any(not isinstance(tool, dict) or not isinstance(tool.get("name"), str) for tool in value["tools"]):
        raise DotsBridgeError("invalid bound tool schema")
    if type(value["protocol_version"]) is not int or value["protocol_version"] != PROTOCOL_VERSION:
        raise DotsBridgeError("unsupported dots protocol version")
    for name in ("prompt", "run_label"):
        if not isinstance(value[name], str) or not value[name].strip():
            raise DotsBridgeError(f"{name} must be nonempty text")
    for name in ("model", "reasoning_effort"):
        if value[name] is not None and (not isinstance(value[name], str) or not value[name].strip()):
            raise DotsBridgeError(f"{name} must be nonempty text or null")
    for name in ("created_at", "expires_at"):
        if type(value[name]) not in (int, float) or not math.isfinite(value[name]):
            raise DotsBridgeError(f"{name} must be a finite timestamp")
    if value["expires_at"] <= value["created_at"]:
        raise DotsBridgeError("request deadline must be after creation")
    encode_payload(value)
    return DotsRequest(**value)


def validate_event(value: Any, request_id: str, sequence: int) -> dict[str, Any]:
    base = {"protocol_version", "request_id", "sequence", "type"}
    if not isinstance(value, dict) or not base <= value.keys():
        raise DotsBridgeError("invalid event envelope")
    if type(value["protocol_version"]) is not int or value["protocol_version"] != PROTOCOL_VERSION:
        raise DotsBridgeError("unsupported dots protocol version")
    if value["request_id"] != request_id or type(value["sequence"]) is not int or value["sequence"] != sequence:
        raise DotsBridgeError("event request identity or sequence mismatch")
    kind = value["type"]
    if not isinstance(kind, str):
        raise DotsBridgeError("event type must be text")
    if kind == "accepted":
        extra = {"worker_id"}
        if not isinstance(value.get("worker_id"), str) or not value["worker_id"].strip():
            raise DotsBridgeError("accepted requires the actual host worker_id")
    elif kind in {"message", "failed", "cancelled"}:
        extra = {"text"}
        if not isinstance(value.get("text"), str) or not value["text"].strip():
            raise DotsBridgeError(f"{kind} requires nonempty text")
    elif kind == "tool_call":
        extra = {"name", "arguments"}
        if not isinstance(value.get("name"), str) or not isinstance(value.get("arguments"), dict):
            raise DotsBridgeError("tool_call requires a name and object arguments")
    elif kind == "completed":
        extra = {"usage"} if "usage" in value else set()
        if "usage" in value:
            usage = value["usage"]
            if not isinstance(usage, dict) or not set(usage) <= set(USAGE_FIELDS):
                raise DotsBridgeError("invalid usage fields")
            if any(type(n) is not int or n < 0 for n in usage.values()):
                raise DotsBridgeError("usage must contain nonnegative integer counts")
    else:
        raise DotsBridgeError("unknown dots event type")
    if set(value) != base | extra:
        raise DotsBridgeError("unexpected dots event fields")
    encode_payload(value)
    return value


class DotsBackend:
    """Experimental text-task RunnerBackend with explicit host dispatch.

    No host execution guarantees are assumed. Resume, sandbox controls and
    bound tools require explicit transport capabilities; CLI arguments,
    arbitrary environments and automatic privilege expansion are never passed.
    Unsupported options fail before publication. Missing usage stays unknown.
    """

    backend = "dots"
    tool_activity_observation_supported = False

    def __init__(self, transport: DotsTransport | None = None, *, role: str = "manager",
                 timeout_seconds: float = 300, poll_interval: float = 0.1,
                 cancellation_grace_seconds: float = 0.2,
                 default_interrupt_reason_provider: Callable[[], str | None] | None = None) -> None:
        if role not in DOTS_ROLES:
            raise ValueError("unknown dots role: " + role)
        self.role = role
        self._default_interrupt = default_interrupt_reason_provider
        self._tools: ContextVar[Any] = ContextVar("dots_call_bound_tools", default=None)
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        if not math.isfinite(poll_interval) or poll_interval <= 0:
            raise ValueError("poll_interval must be positive and finite")
        if (type(cancellation_grace_seconds) not in (int, float)
                or not 0 < cancellation_grace_seconds <= 5
                or not math.isfinite(cancellation_grace_seconds)):
            raise ValueError("cancellation_grace_seconds must be finite and greater than 0, at most 5 seconds")
        self.transport = transport
        self.timeout_seconds = timeout_seconds
        self.poll_interval = poll_interval
        self.cancellation_grace_seconds = cancellation_grace_seconds

    def set_default_interrupt_reason_provider(self, provider: Callable[[], str | None]) -> None:
        self._default_interrupt = provider

    def fork(self, *, interrupt_reason_provider: Callable[[], str | None] | None = None) -> DotsBackend:
        return DotsBackend(self.transport, role=self.role, timeout_seconds=self.timeout_seconds,
                           poll_interval=self.poll_interval,
                           cancellation_grace_seconds=self.cancellation_grace_seconds,
                           default_interrupt_reason_provider=interrupt_reason_provider or self._default_interrupt)

    def capability_report(self) -> dict[str, Any]:
        caps = getattr(self.transport, "capabilities", DotsCapabilities(roles=frozenset()))
        return {"backend": "dots", "role": self.role, "configured": self.transport is not None,
                "roles": sorted(caps.roles), "supported_options": sorted(caps.options),
                "resume": caps.resume, "role_tools": caps.role_tools,
                "automatic_dispatch": False}

    @contextmanager
    def bind_role_tools(self, tools: list[dict[str, Any]],
                        dispatch: Callable[[str, dict[str, Any]], dict[str, Any]]) -> Iterator[None]:
        """Keep a role's existing dispatcher call-bound and inside Argus.

        The request carries tool schemas only. No bearer token, port,
        environment, or Python callable is transmitted to the host.
        """
        if self.role != "reviewer":
            raise DotsBridgeError("review tools can only bind to the independent reviewer role")
        token = self._tools.set((tools, dispatch))
        try:
            yield
        finally:
            self._tools.reset(token)

    def run_exec(self, *, prompt: str, options: RunnerOptions, run_label: str,
                 resume_thread_id: str | None = None) -> RunnerResult:
        started = time.time()
        result = RunnerResult(exit_code=1, started_at=started)
        request: DotsRequest | None = None
        submitted = False
        sequence = 1

        def fail(message: str, *, code: int = 1, stop_kind: str = "backend_unavailable") -> RunnerResult:
            result.exit_code = code
            result.fatal_error = message
            result.stop_kind = stop_kind  # type: ignore[assignment]
            return result

        def cancel(reason: str) -> str:
            if not submitted or request is None or self.transport is None:
                return ""
            unconfirmed = "; host worker cancellation is unconfirmed; the host must stop/acknowledge it"
            try:
                self.transport.cancel(request.request_id, reason)
            except (Exception, KeyboardInterrupt) as exc:
                return unconfirmed + "; cancellation could not be recorded: " + (str(exc) or type(exc).__name__)
            try:
                observe = getattr(self.transport, "poll_cancellation", None)
                if observe is None:
                    return unconfirmed
                if not callable(observe):
                    raise DotsBridgeError("poll_cancellation must be callable")
                grace_deadline = time.monotonic() + self.cancellation_grace_seconds
                while time.monotonic() < grace_deadline:
                    acknowledgement = observe(request.request_id)
                    # A slow transport must not make an acknowledgement arriving
                    # after the grace deadline look timely, or accept late success.
                    if time.monotonic() >= grace_deadline:
                        break
                    if acknowledgement is not None:
                        ack_sequence = acknowledgement.get("sequence") if isinstance(acknowledgement, dict) else None
                        if type(ack_sequence) is not int or not sequence <= ack_sequence <= 4096:
                            raise DotsBridgeError("invalid cancellation acknowledgement sequence")
                        validate_event(acknowledgement, request.request_id, ack_sequence)
                        if acknowledgement["type"] != "cancelled":
                            raise DotsBridgeError("cancellation observation requires a cancelled event")
                        if time.monotonic() < grace_deadline:
                            return "; host confirmed cancellation: " + acknowledgement["text"]
                        break
                    time.sleep(min(self.poll_interval, max(0, grace_deadline - time.monotonic())))
            except KeyboardInterrupt:
                return unconfirmed + "; cancellation acknowledgement wait interrupted"
            except Exception as exc:
                return unconfirmed + "; cancellation acknowledgement failed: " + str(exc)
            return unconfirmed

        try:
            if self.transport is None:
                return fail("dots unavailable: no transport configured; an authorized online host is required")
            caps = getattr(self.transport, "capabilities", DotsCapabilities(roles=frozenset()))
            if self.role not in caps.roles:
                return fail("dots host does not support role: " + self.role, stop_kind="permanent_error")
            if resume_thread_id is not None and not caps.resume:
                return fail("dots host does not support session resume", stop_kind="permanent_error")
            # Compare against the existing dataclass defaults so newly introduced
            # execution controls also fail closed until intentionally supported.
            supported = {"model", "reasoning_effort", "external_interrupt_reason_provider", "on_agent_message",
                         "skip_git_repo_check", "watchdog_hard_idle_seconds", "extension_env"}
            if not isinstance(options, RunnerOptions):
                return fail("dots requires RunnerOptions", stop_kind="permanent_error")
            defaults = RunnerOptions()
            mission_id = None
            if options.extension_env:
                if set(options.extension_env) != {"ARGUS_PLUGIN_PARENT_MISSION_ID"}:
                    return fail("dots never forwards extension environments or credentials", stop_kind="permanent_error")
                mission_id = options.extension_env["ARGUS_PLUGIN_PARENT_MISSION_ID"]
            serializable = {}
            unsupported = []
            # CLI flags/environment/extensions are never forwarded, even when
            # a host advertises a similarly named capability.
            forbidden = {"dangerous_yolo", "extra_args", "extension_env", "trusted_extensions", "trusted_tool_names",
                         "inactivity_callback", "watchdog_soft_idle_seconds", "watchdog_stalled_idle_seconds"}
            for f in fields(RunnerOptions):
                value = getattr(options, f.name)
                empty_list = f.name in {"add_dirs", "skill_paths", "extra_args", "trusted_extensions", "trusted_tool_names"} and value == []
                if f.name in supported or value == getattr(defaults, f.name) or empty_list:
                    continue
                capability = f"sandbox_mode:{value}" if f.name == "sandbox_mode" else f.name
                if f.name in forbidden or capability not in caps.options:
                    unsupported.append(f.name)
                else:
                    serializable[f.name] = value
            if unsupported:
                return fail("dots host lacks required execution capabilities: " + ", ".join(unsupported),
                            stop_kind="permanent_error")
            hard_idle = options.watchdog_hard_idle_seconds
            if hard_idle is not None and (type(hard_idle) is not int or hard_idle < 0):
                return fail("hard idle timeout must be a nonnegative integer", stop_kind="permanent_error")
            tools_binding = self._tools.get()
            if tools_binding and not caps.role_tools:
                return fail("dots host cannot enforce call-bound role tools", stop_kind="permanent_error")
            def interrupt() -> str | None:
                default = self._default_interrupt() if self._default_interrupt else None
                original = options.external_interrupt_reason_provider
                return default or (original() if original else None)
            reason = interrupt()
            if reason:
                return fail(f"External interrupt: {reason}", code=130,
                            stop_kind=stop_kind_from_external_interrupt(reason) or "operator_abort")
            request = validate_request(DotsRequest(
                uuid.uuid4().hex, prompt, run_label,
                None if options.model == "" else options.model, options.reasoning_effort,
                started, started + self.timeout_seconds,
                role=self.role, mission_id=mission_id, resume_thread_id=resume_thread_id, options=serializable,
                tools=tools_binding[0] if tools_binding else [],
            ).to_dict())
            result.call_id = "dots-" + request.request_id
            submitted = True  # submit may publish before its final fsync/ack fails
            self.transport.submit(request)
            deadline = time.monotonic() + max(0, request.expires_at - time.time())
            sequence, response_bytes = 1, 0
            last_activity = time.monotonic()
            accepted = False
            while True:
                reason = interrupt() if interrupt else None
                if reason:
                    return fail(f"External interrupt: {reason}" + cancel(reason), code=130,
                                stop_kind=stop_kind_from_external_interrupt(reason) or "operator_abort")
                if time.monotonic() >= deadline:
                    return fail("dots bridge timed out" + cancel("request timed out"), code=124, stop_kind="permanent_error")
                if hard_idle and time.monotonic() - last_activity >= hard_idle:
                    return fail("dots bridge hard idle timeout" + cancel("hard idle timeout"),
                                code=124, stop_kind="permanent_error")
                event = self.transport.poll(request.request_id, sequence)
                if event is None:
                    time.sleep(min(self.poll_interval, max(0, deadline - time.monotonic())))
                    continue
                # Cancellation/deadline can arrive during a transport read.
                # Never turn that race into approval, another tool, or success.
                reason = interrupt()
                if reason:
                    return fail(f"External interrupt: {reason}" + cancel(reason), code=130,
                                stop_kind=stop_kind_from_external_interrupt(reason) or "operator_abort")
                if time.monotonic() >= deadline:
                    return fail("dots bridge timed out" + cancel("request timed out"),
                                code=124, stop_kind="permanent_error")
                validate_event(event, request.request_id, sequence)
                last_activity = time.monotonic()
                response_bytes += len(encode_payload(event))
                if response_bytes > MAX_RESPONSE_BYTES:
                    raise DotsBridgeError("response exceeds the 8 MiB safety limit")
                sequence += 1
                kind = event["type"]
                if kind == "accepted":
                    if accepted:
                        raise DotsBridgeError("duplicate accepted event")
                    if resume_thread_id is not None and event["worker_id"] != resume_thread_id:
                        raise DotsBridgeError("host returned a different worker for a resumed role session")
                    accepted = True
                    result.thread_id = event["worker_id"]
                elif kind == "tool_call":
                    if not accepted or not tools_binding:
                        raise DotsBridgeError("role tool call outside an accepted, bound role turn")
                    tool_schemas, dispatch = tools_binding
                    if event["name"] not in {tool["name"] for tool in tool_schemas}:
                        raise DotsBridgeError("role tool is not bound to this call")
                    result.tool_activity_observed = True
                    claim_call = getattr(self.transport, "claim_tool_call", None)
                    reply = claim_call(request.request_id, sequence - 1) if callable(claim_call) else None
                    if reply is not None and not isinstance(reply, dict):
                        raise DotsBridgeError("invalid saved tool reply")
                    if reply is None:
                        try:
                            reply = dispatch(event["name"], event["arguments"])
                        except ToolBridgeBusy as exc:
                            reply = {"status": "busy", "error": str(exc)}
                        except (KeyError, OSError, TypeError, ValueError) as exc:
                            # Match the existing CallBoundBridge: an allowed tool's
                            # rejected input is feedback, not a fabricated verdict.
                            reply = {"status": "failed", "error": str(exc)}
                        except Exception:
                            log.exception("dots role tool request failed")
                            reply = {"status": "failed", "error": "role tool request failed"}
                    encode_payload(reply)
                    self.transport.tool_result(request.request_id, sequence - 1, reply)
                elif kind in {"message", "completed"}:
                    if not accepted:
                        raise DotsBridgeError(f"{kind} arrived before host acceptance")
                    if kind == "message":
                        result.agent_messages.append(event["text"])
                        if options.on_agent_message:
                            try:
                                options.on_agent_message(event["text"])
                            except Exception:
                                log.exception("dots on_agent_message callback failed")
                    else:
                        self.transport.finish(request.request_id)
                        for name, value in event.get("usage", {}).items():
                            setattr(result, name, value)
                            setattr(result, name + "_present", True)
                        result.exit_code = 0
                        result.usage_model = options.model or ""
                        return result
                elif kind == "failed":
                    self.transport.finish(request.request_id)
                    return fail("dots host failed: " + event["text"], stop_kind="permanent_error")
                elif kind == "cancelled":
                    self.transport.finish(request.request_id)
                    return fail("dots host confirmed cancellation: " + event["text"], code=130,
                                stop_kind="operator_abort")
        except Exception as exc:  # transport boundary: report failure and attempt cleanup
            detail = cancel("bridge error: " + str(exc))
            return fail("dots bridge error: " + str(exc) + detail, stop_kind="permanent_error")
        except KeyboardInterrupt:
            detail = cancel("keyboard interrupt")
            return fail("External interrupt: keyboard interrupt" + detail, code=130,
                        stop_kind="operator_abort")
        finally:
            result.completed_at = time.time()
            result.duration_ms = max(0, round((result.completed_at - started) * 1000))
