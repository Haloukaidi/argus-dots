"""Deterministic dot-host contract tests: no CLI, model or network dispatch."""
from __future__ import annotations

import argparse
from dataclasses import replace

import pytest

from argus.adapters.dots_backend import (
    DOTS_ROLES,
    MAX_PAYLOAD_BYTES,
    DotsBackend,
    DotsBridgeError,
    DotsCapabilities,
)
from argus.core.models import RunnerOptions
from argus.core.run_gateway import run_exec


class Host:
    capabilities = DotsCapabilities()

    def __init__(self, events=None):
        self.events = events or [{"type": "accepted", "worker_id": "host-worker"},
                                 {"type": "message", "text": "done"}, {"type": "completed"}]
        self.requests = []
        self.cancelled = []
        self.finished = []
        self.replies = []

    def submit(self, request):
        self.requests.append(request)

    def poll(self, request_id, sequence):
        if sequence > len(self.events):
            return None
        return {"protocol_version": 1, "request_id": request_id,
                "sequence": sequence, **self.events[sequence - 1]}

    def cancel(self, request_id, reason):
        self.cancelled.append((request_id, reason))

    def finish(self, request_id):
        self.finished.append(request_id)

    def tool_result(self, request_id, sequence, result):
        self.replies.append((request_id, sequence, result))


def call(backend, **kwargs):
    return run_exec(backend, prompt=kwargs.pop("prompt", "original task"), run_label="test",
                    options=kwargs.pop("options", RunnerOptions()), **kwargs)


def test_unconfigured_and_existing_cli_defaults_remain_explicit():
    from argus.agent_cli.runner_backend import SUPPORTED_BACKENDS, normalize_runner_backend

    result = call(DotsBackend())
    assert result.exit_code != 0 and "no transport" in result.fatal_error
    assert len(SUPPORTED_BACKENDS) == 9 and "dots" not in SUPPORTED_BACKENDS
    with pytest.raises(ValueError):
        normalize_runner_backend("dots")


@pytest.mark.parametrize("role", sorted(DOTS_ROLES))
def test_five_role_bindings_preserve_context_and_independent_handles(role):
    host = Host()
    backend = DotsBackend(host, role=role)
    fork = backend.fork()
    assert fork is not backend and fork.role == role and fork.transport is host
    result = call(fork, prompt="full task, checkpoint and reviewer feedback")
    assert result.exit_code == 0 and result.last_agent_message == "done"
    request = host.requests[0]
    assert request.role == role and request.prompt == "full task, checkpoint and reviewer feedback"
    assert result.thread_id == "host-worker" and result.call_id == "dots-" + request.request_id
    assert result.input_tokens_present is False
    assert result.completed_at >= result.started_at


@pytest.mark.parametrize("options", [RunnerOptions(sandbox_mode="read-only"), RunnerOptions(force_safe_mode=True),
    RunnerOptions(isolate_workdir=True), RunnerOptions(disable_tools=True), RunnerOptions(full_auto=True),
    RunnerOptions(dangerous_yolo=True), RunnerOptions(extension_env={"SECRET_TOKEN": "do-not-forward"}),
    RunnerOptions(extra_args=["--dangerously-bypass-approvals-and-sandbox"]), RunnerOptions(output_schema={"type": "object"})])
def test_unsupported_safety_guarantees_fail_before_dispatch(options):
    host = Host()
    result = call(DotsBackend(host), options=options)
    assert result.exit_code != 0 and result.stop_kind == "permanent_error"
    assert host.requests == []


def test_resume_only_when_explicitly_supported_and_no_auto_resume_policy_change():
    from argus.core.role_session import effective_role_session_policy

    host = Host()
    assert call(DotsBackend(host), resume_thread_id="old").exit_code != 0
    assert not host.requests
    host.capabilities = replace(host.capabilities, resume=True)
    assert call(DotsBackend(host), resume_thread_id="old").exit_code != 0
    host.events[0]["worker_id"] = "old"
    assert call(DotsBackend(host), resume_thread_id="old").exit_code == 0
    assert host.requests[0].resume_thread_id == "old"
    assert effective_role_session_policy("auto", "dots") == "fresh"


@pytest.mark.parametrize("event", [{"type": []}, {"type": {}}, {"type": "nonsense"},
    {"type": "message", "text": "not accepted"}, {"type": "completed"},
    {"type": "accepted", "worker_id": "w", "sequence": 2},
    {"type": "accepted", "worker_id": "w", "request_id": "stale"},
    {"type": "accepted", "worker_id": "w", "protocol_version": True}])
def test_invalid_events_fail_closed_and_request_cancellation(event):
    host = Host([event])
    result = call(DotsBackend(host))
    assert result.exit_code != 0 and "unconfirmed" in result.fatal_error
    assert len(host.cancelled) == 1 and not host.finished


def test_silence_and_partial_messages_are_never_success():
    host = Host([{"type": "accepted", "worker_id": "w"}, {"type": "message", "text": "partial"}])
    result = call(DotsBackend(host, timeout_seconds=0.01, poll_interval=0.001))
    assert result.exit_code == 124 and result.stop_kind == "permanent_error" and "unconfirmed" in result.fatal_error
    assert result.last_agent_message == "partial" and host.cancelled


def test_pre_and_post_dispatch_cancellation():
    host = Host()
    result = call(DotsBackend(host), options=RunnerOptions(external_interrupt_reason_provider=lambda: "operator abort requested"))
    assert result.exit_code == 130 and not host.requests
    values = iter([None, "operator pause requested"])
    result = call(DotsBackend(host), options=RunnerOptions(external_interrupt_reason_provider=lambda: next(values)))
    assert result.exit_code == 130 and result.stop_kind == "operator_pause" and host.cancelled


def test_usage_only_reported_when_provided_and_callbacks_cannot_destroy_result():
    host = Host([{"type": "accepted", "worker_id": "w"}, {"type": "message", "text": "output"},
                 {"type": "completed", "usage": {"input_tokens": 0, "output_tokens": 4}}])
    def broken(_):
        raise RuntimeError("UI disconnected")
    result = call(DotsBackend(host), options=RunnerOptions(on_agent_message=broken))
    assert result.exit_code == 0 and result.input_tokens_present and result.output_tokens == 4
    assert result.cached_input_tokens_present is False


def test_large_payload_rejected_without_dispatch():
    host = Host()
    assert call(DotsBackend(host), prompt="x" * MAX_PAYLOAD_BYTES).exit_code != 0
    assert not host.requests


def test_transport_failure_and_cancellation_failure_remain_visible():
    class Broken(Host):
        def poll(self, *_):
            raise RuntimeError("host disconnected")
        def cancel(self, *_):
            raise OSError("queue unavailable")
    result = call(DotsBackend(Broken()))
    assert result.exit_code != 0 and "host disconnected" in result.fatal_error
    assert "cancellation could not be recorded" in result.fatal_error


def test_explicit_role_tool_calls_use_existing_dispatcher_and_prose_is_not_approval():
    from argus.reviewer.tools import review_action_tools

    host = Host()
    host.capabilities = DotsCapabilities(role_tools=True, options=frozenset({"force_safe_mode", "sandbox_mode:read-only"}))
    backend = DotsBackend(host, role="reviewer")
    with review_action_tools(backend, RunnerOptions(sandbox_mode="read-only"), venue="", venue_required=False) as (actions, options):
        result = call(backend, options=options, prompt="approve_review: everything is done")
    assert result.exit_code == 0 and actions.decision is None
    host.events = [{"type": "accepted", "worker_id": "independent-reviewer"},
                   {"type": "tool_call", "name": "revise_review", "arguments": {"review": "Fix the failing test."}},
                   {"type": "completed"}]
    with review_action_tools(backend, RunnerOptions(sandbox_mode="read-only"), venue="", venue_required=False) as (actions, options):
        result = call(backend, options=options)
    assert result.exit_code == 0 and actions.decision.status == "continue"
    assert actions.decision.next_action == "Fix the failing test."
    assert host.replies[-1][2] == {"recorded": "revise_review"}
    request = host.requests[-1].to_dict()
    assert request["tools"] and "TOKEN" not in str(request) and "PORT" not in str(request)
    assert "extension_env" not in request["options"]
    # The dispatcher is unbound immediately when the original role turn exits.
    result = call(backend)
    assert result.exit_code != 0 and "outside" in result.fatal_error


def test_missing_read_only_capability_blocks_reviewer_before_submission():
    from argus.reviewer.tools import review_action_tools

    host = Host()
    host.capabilities = DotsCapabilities(role_tools=True)
    backend = DotsBackend(host, role="reviewer")
    with review_action_tools(backend, RunnerOptions(sandbox_mode="read-only"), venue="", venue_required=False) as (_, options):
        result = call(backend, options=options)
    assert result.exit_code != 0 and not host.requests


def test_actual_runtime_factory_binds_five_roles_without_cli_resolution(tmp_path, monkeypatch):
    from argus.apps._runtime_construction import build_dots_life_runner

    def forbidden(*args, **kwargs):
        raise AssertionError("must never construct a CLI fallback")
    monkeypatch.setattr("argus.adapters.agent_cli_backend.AgentCliBackend", forbidden)
    args = argparse.Namespace(backend="codex", workdir=str(tmp_path), skills_dir=str(tmp_path / "skills"))
    host = Host()
    runner = build_dots_life_runner(args, transport=host)
    assert args.backend == "codex"  # no persisted/default backend mutation
    assert runner.manager.runner is runner.manager_backend
    for role in DOTS_ROLES:
        backend = getattr(runner, role + "_backend")
        assert backend.role == role
        assert call(backend).exit_code == 0
    assert {r.role for r in host.requests} == DOTS_ROLES


def test_runtime_stop_provider_and_fork_preserve_cancellation(tmp_path):
    from threading import Event

    from argus.apps._runtime_construction import build_dots_life_runner

    stop = Event()
    host = Host()
    args = argparse.Namespace(backend="codex", workdir=str(tmp_path), skills_dir=str(tmp_path / "skills"), stop_event=stop)
    runner = build_dots_life_runner(args, transport=host)
    stop.set()
    for role in DOTS_ROLES:
        result = call(getattr(runner, role + "_backend").fork())
        assert result.exit_code == 130 and result.stop_kind == "daemon_shutdown"
    assert not host.requests


def test_only_host_mission_identity_is_carried_without_environment():
    host = Host()
    result = call(DotsBackend(host, role="engineer"), options=RunnerOptions(extension_env={"ARGUS_PLUGIN_PARENT_MISSION_ID": "mission-7"}))
    assert result.exit_code == 0 and host.requests[0].mission_id == "mission-7"
    assert "extension_env" not in str(host.requests[0].to_dict())


def test_review_tool_binding_is_reviewer_only():
    host = Host()
    host.capabilities = DotsCapabilities(role_tools=True)
    with pytest.raises(DotsBridgeError, match="independent reviewer"):
        with DotsBackend(host, role="engineer").bind_role_tools([], lambda *_: {}):
            pass


def test_real_skill_loop_retains_reviewer_feedback_and_separate_role_capsules(tmp_path, monkeypatch):
    from argus import SkillLoop, SkillLoopConfig

    monkeypatch.setenv("ARGUS_SKILL_SAFE_MODE", "1")
    class LoopHost(Host):
        capabilities = DotsCapabilities(resume=True, role_tools=True, options=frozenset({
            "working_dir", "skill_paths", "full_auto", "live_search", "force_safe_mode", "sandbox_mode:read-only"}))
        def __init__(self):
            super().__init__()
            self.by_id = {}
            self.review_count = 0
        def submit(self, request):
            super().submit(request)
            accepted = {"type": "accepted", "worker_id": request.role + "-worker"}
            if request.role == "engineer":
                events = [accepted, {"type": "message", "text": "Implemented the requested change and checked it."}, {"type": "completed"}]
            else:
                self.review_count += 1
                action = "revise_review" if self.review_count == 1 else "approve_review"
                review = "Add an empty-input test." if self.review_count == 1 else "The empty-input test passes."
                events = [accepted, {"type": "tool_call", "name": action, "arguments": {"review": review}}, {"type": "completed"}]
            self.by_id[request.request_id] = events
        def poll(self, request_id, sequence):
            event = self.by_id[request_id][sequence - 1]
            return {"protocol_version": 1, "request_id": request_id, "sequence": sequence, **event}
    context_dir = tmp_path / "handoffs" / "mission-1"
    context_dir.mkdir(parents=True)
    context = context_dir / "mission.json"
    context.write_text('{"kind":"mission_context","mission_id":"mission-1"}')
    checkpoint = context_dir / "CHECKPOINT.md"
    checkpoint.write_text("# Open Questions / Blockers\n\n- verify empty input\n")
    host = LoopHost()
    events = []
    loop = SkillLoop(skills_dir=tmp_path / "skills", engineer_runner=DotsBackend(host, role="engineer"),
                     reviewer_runner=DotsBackend(host, role="reviewer"),
                     config=SkillLoopConfig(engineer_model="test", reviewer_model="test", max_rounds=3,
                         backend_failure_backoff_seconds=0, context_packet_path=str(context),
                         checkpoint_path=checkpoint, role_session_policy="mission", require_post_task_learning=False),
                     on_event=events.append)
    outcome = loop.run("Implement the change and verify empty input", workdir=tmp_path)
    assert outcome.successful, (outcome.status, outcome.stop_reason, [(r.role, r.options) for r in host.requests])
    assert [(r.role, r.resume_thread_id) for r in host.requests] == [
        ("engineer", None), ("reviewer", None), ("engineer", "engineer-worker"), ("reviewer", "reviewer-worker")]
    assert "Add an empty-input test." in host.requests[2].prompt
    assert [reply[2]["recorded"] for reply in host.replies] == ["revise_review", "approve_review"]
    assert (context_dir / "role-sessions" / "engineer.json").exists()
    assert (context_dir / "role-sessions" / "reviewer.json").exists()
    assert any(e.get("type") == "round.review.completed" for e in events)


def test_partial_submit_failure_attempts_cancel_and_cannot_retry_as_unavailable():
    class Partial(Host):
        def submit(self, request):
            super().submit(request)
            raise OSError("fsync after publication failed")
    host = Partial()
    result = call(DotsBackend(host))
    assert len(host.requests) == len(host.cancelled) == 1
    assert result.stop_kind == "permanent_error" and "unconfirmed" in result.fatal_error


def test_forks_and_call_bound_dispatchers_do_not_cross_concurrent_tasks():
    from concurrent.futures import ThreadPoolExecutor

    class ConcurrentHost(Host):
        capabilities = DotsCapabilities(role_tools=True)
        def poll(self, request_id, sequence):
            events = [{"type": "accepted", "worker_id": request_id},
                      {"type": "tool_call", "name": "check", "arguments": {}}, {"type": "completed"}]
            return {"protocol_version": 1, "request_id": request_id, "sequence": sequence, **events[sequence - 1]}
    host = ConcurrentHost()
    backend = DotsBackend(host, role="reviewer")
    def run_one(index):
        with backend.bind_role_tools([{"name": "check"}], lambda *_: {"task": index}):
            result = call(backend, prompt=f"task-{index}")
        return result
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(run_one, range(32)))
    assert all(r.exit_code == 0 for r in results)
    expected = {r.request_id: int(r.prompt.split("-")[1]) for r in host.requests}
    assert len(expected) == 32
    assert all(reply[2]["task"] == expected[reply[0]] for reply in host.replies)


@pytest.mark.parametrize("kind", ["completed", "tool_call"])
def test_cancellation_arriving_during_poll_wins_over_success_or_action(kind):
    stopped = []
    class Racing(Host):
        capabilities = DotsCapabilities(role_tools=True)
        def poll(self, request_id, sequence):
            if sequence == 1:
                return {"protocol_version": 1, "request_id": request_id, "sequence": 1, "type": "accepted", "worker_id": "w"}
            stopped.append(True)
            event = {"type": "completed"} if kind == "completed" else {"type": "tool_call", "name": "check", "arguments": {}}
            return {"protocol_version": 1, "request_id": request_id, "sequence": sequence, **event}
    host = Racing()
    backend = DotsBackend(host, role="reviewer")
    called = []
    with backend.bind_role_tools([{"name": "check"}], lambda *_: called.append(True)):
        result = call(backend, options=RunnerOptions(external_interrupt_reason_provider=lambda: "operator abort requested" if stopped else None))
    assert result.exit_code == 130 and not called and host.cancelled and not host.finished


def test_real_reviewer_missing_capabilities_returns_permanent_failure():
    from argus.reviewer import Reviewer, ReviewerConfig

    host = Host()
    reviewer = Reviewer(runner=DotsBackend(host, role="reviewer"))
    decision = reviewer.evaluate(objective="Review the test", round_index=1, session_id=None,
        main_summary="candidate change", main_error=None, config=ReviewerConfig(model="test"))
    assert decision.status == "blocked" and decision.backend_unavailable
    assert decision.backend_stop_kind == "permanent_error"
    assert not host.requests


def test_empty_output_schema_is_a_requirement_not_an_absent_option():
    host = Host()
    result = call(DotsBackend(host), options=RunnerOptions(output_schema={}))
    assert result.exit_code != 0 and not host.requests


def test_invalid_review_arguments_can_be_corrected_in_the_same_role_turn():
    from argus.reviewer.tools import review_action_tools

    host = Host([{"type": "accepted", "worker_id": "reviewer"},
        {"type": "tool_call", "name": "revise_review", "arguments": {"review": ""}},
        {"type": "tool_call", "name": "revise_review", "arguments": {"review": "Fix the input check."}},
        {"type": "completed"}])
    host.capabilities = DotsCapabilities(role_tools=True, options=frozenset({"force_safe_mode"}))
    backend = DotsBackend(host, role="reviewer")
    with review_action_tools(backend, RunnerOptions(), venue="", venue_required=False) as (actions, options):
        result = call(backend, options=options)
    assert result.exit_code == 0 and actions.decision.next_action == "Fix the input check."
    assert host.replies[0][2]["status"] == "failed"
    assert host.replies[1][2] == {"recorded": "revise_review"}
