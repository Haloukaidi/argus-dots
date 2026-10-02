"""Offline reviewer workflow probes: real orchestration, controlled role replies.

These fixtures do not enforce a native filesystem sandbox or contact a provider.
Validation probes below stub the Docker client boundary; no Docker is started.
"""
from __future__ import annotations

import json
import socket
import subprocess
import threading
from pathlib import Path

import pytest

from argus.adapters.agent_cli_backend import AgentCliBackend
from argus.adapters.memory_backend import CannedResponse, MemoryBackend
from argus.core.models import RunnerResult
from argus.core.pipeline_state import read_pipeline_state
from argus.core.run_gateway import run_interrupt_scope
from argus.reviewer import Reviewer, ReviewerConfig
from argus.reviewer._core import _parallel_final_review_passes
from argus.skills.vertical_select import persist_vertical


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    original_connect = socket.socket.connect

    def local_only(sock, address):
        if isinstance(address, tuple) and address[0] == "127.0.0.1":
            return original_connect(sock, address)
        raise AssertionError("Reviewer workflow probes cannot use an external network")

    def forbidden(*args, **kwargs):
        raise AssertionError("Reviewer workflow probes cannot call a real provider")

    monkeypatch.setattr(socket.socket, "connect", local_only)
    monkeypatch.setattr(AgentCliBackend, "run_exec", forbidden)


def evaluate(backend, tmp_path, **kwargs):
    return Reviewer(backend).evaluate(
        objective="Review the current local result", round_index=1,
        session_id=None, main_summary="The controlled local check finished.",
        main_error=None,
        config=ReviewerConfig(active_vertical="software", working_dir=str(tmp_path)),
        **kwargs,
    )


def research_config(tmp_path):
    workdir = tmp_path / "workdir"
    state = tmp_path / "state"
    workdir.mkdir()
    persist_vertical(state, "research")
    payload = read_pipeline_state(state)
    payload["current_stage"] = "review"
    (state / ".argus" / "PIPELINE_STATE.json").write_text(json.dumps(payload))
    return ReviewerConfig(
        active_vertical="research", working_dir=str(workdir),
        artifact_root=str(workdir), vertical_state_root=str(state),
    )


@pytest.mark.parametrize("parallel", [False, True], ids=["integrated", "specialists"])
@pytest.mark.parametrize("reason", [
    "operator abort requested: cancel this review",
    "daemon stop requested",
])
def test_pending_request_stop_never_starts_reviewer_calls(tmp_path, parallel, reason):
    backend = MemoryBackend(default=CannedResponse(message="Fixture assessment"))
    with run_interrupt_scope(lambda: reason, retain_first_reason=True):
        decision = (
            _parallel_final_review_passes(backend, research_config(tmp_path))
            if parallel else evaluate(backend, tmp_path)
        )
    assert backend.history == [], "A pending request stop must prevent every provider call"
    assert decision is not None and decision.status == "blocked"
    assert decision.backend_unavailable
    assert reason in decision.backend_fatal_error


def test_inflight_specialist_calls_inherit_request_stop_callback(tmp_path):
    barrier = threading.Barrier(3)
    stopped = threading.Event()
    observations = []

    class Backend:
        def fork(self):
            return Backend()

        def run_exec(self, **kwargs):
            barrier.wait(timeout=2)
            stopped.set()
            callback = kwargs["options"].external_interrupt_reason_provider
            reason = callback() if callback else None
            observations.append(reason)
            return RunnerResult(
                exit_code=130 if reason else 0,
                fatal_error=f"External interrupt: {reason}" if reason else None,
                stop_kind="operator_abort" if reason else None,
                agent_messages=[] if reason else ["Fixture assessment"],
            )

    reason = "operator abort requested: stop during review"
    with run_interrupt_scope(lambda: reason if stopped.is_set() else None, retain_first_reason=True):
        decision = _parallel_final_review_passes(Backend(), research_config(tmp_path))
    assert observations == [reason] * 3
    assert decision.status == "blocked" and decision.backend_stop_kind == "operator_abort"


@pytest.mark.parametrize("first", [
    CannedResponse(message="STATUS=done\nAll checks passed"),
    CannedResponse(
        review_action=("approve_review", {"review": "An interrupted judgment"}),
        exit_code=1, fatal_error="Controlled transport failure",
    ),
    CannedResponse(
        review_action=("request_review_decision", {"review": "A choice is needed", "question": " "}),
    ),
], ids=["no-native-action", "approval-then-failure", "invalid-question"])
def test_fresh_reviewer_can_recover_without_reusing_failed_action(tmp_path, first):
    backend = MemoryBackend()
    backend.queue("reviewer", first, CannedResponse(
        review_action=("request_review_decision", {
            "review": "The remaining choice belongs to the operator.",
            "question": "Which licensed fixture may be used?",
        }), thread_id="new-fixture-session",
    ))
    failed = evaluate(backend, tmp_path)
    recovered = evaluate(backend, tmp_path)
    assert failed.backend_unavailable and failed.status == "blocked"
    assert recovered.status == "blocked" and not recovered.backend_unavailable
    assert recovered.operator_question == "Which licensed fixture may be used?"
    assert recovered.thread_id == "new-fixture-session"
    assert len(backend.history) == 2


def test_recreated_reviewer_resumes_matching_context_but_requires_new_action(tmp_path):
    backend = MemoryBackend()
    backend.queue("reviewer", CannedResponse(
        review_action=("defer_review", {"review": "Await existing work."}),
        thread_id="fixture-review-session",
    ), CannedResponse(message="The previous approval still stands."), CannedResponse(
        review_action=("approve_review", {"review": "The new evidence was checked."}),
        thread_id="fixture-review-session",
    ))
    first = evaluate(backend, tmp_path)
    request = {
        "resume_thread_id": first.thread_id,
        "prior_static_fingerprint": first.static_fingerprint,
    }
    missing = evaluate(backend, tmp_path, **request)
    approved = evaluate(backend, tmp_path, **request)
    assert first.status == "continue" and not first.backend_unavailable
    assert missing.status == "blocked" and missing.backend_unavailable
    assert missing.session_resumed and approved.session_resumed
    assert approved.status == "done" and not approved.backend_unavailable
    assert backend.resume_history == [
        ("reviewer", None), ("reviewer", "fixture-review-session"),
        ("reviewer", "fixture-review-session"),
    ]
    assert "## Reviewer role" not in backend.history[-1][1]


def test_parallel_calls_copy_separate_contexts_without_sharing_tool_binding(tmp_path, monkeypatch):
    from contextvars import ContextVar

    from argus.adapters.dots_backend import DotsBackend

    marker = ContextVar("review-workflow-probe", default="unset")
    marker.set("parent")
    barrier = threading.Barrier(3)
    observations = []

    def controlled_run(self, **kwargs):
        inherited = marker.get()
        label = kwargs["run_label"]
        marker.set(label)
        barrier.wait(timeout=2)
        observations.append((inherited, marker.get(), self._tools.get()))
        return RunnerResult(exit_code=0, agent_messages=["Fixture assessment"])

    monkeypatch.setattr(DotsBackend, "run_exec", controlled_run)
    parent = DotsBackend(role="reviewer")
    with parent.bind_role_tools([{"name": "parent-only"}], lambda *_: {}):
        decision = _parallel_final_review_passes(parent, research_config(tmp_path))
        assert parent._tools.get()[0] == [{"name": "parent-only"}]
    assert parent._tools.get() is None
    assert marker.get() == "parent"
    assert len(observations) == 3
    assert {label for _, label, _ in observations} == {
        "reviewer-scientific", "reviewer-visual", "reviewer-language",
    }
    assert all(inherited == "parent" and binding is None for inherited, _, binding in observations)
    assert decision.status == "continue" and not decision.backend_unavailable


@pytest.mark.parametrize("action,extra", [
    ("approve_review", {}),
    ("revise_review", {}),
    ("defer_review", {}),
    ("request_review_decision", {"question": "Which licensed fixture?"}),
    ("replan_review", {"authority_impact": "technical"}),
])
def test_native_actions_reject_empty_reason_before_checkpoint_hook(action, extra):
    from argus.reviewer.tools import ReviewActions

    actions = ReviewActions()
    for review in ("", " \n "):
        with pytest.raises(ValueError):
            actions.dispatch(action, {"review": review, **extra})
        assert actions.decision is None
    with pytest.raises(ValueError):
        actions.dispatch(action, {"review": "Useful assessment", "checkpoint_recommended": True, **extra})
    actions.dispatch(action, {"review": "Useful assessment", **extra})
    assert actions.decision.reason and not actions.decision.checkpoint_recommended


@pytest.mark.parametrize("mode", ["success", "nonzero", "timeout", "cancel", "cleanup-failure"])
def test_validation_tools_through_reviewer_record_truth_and_close_work(tmp_path, monkeypatch, mode):
    from argus.core.call_bound_execution import ExecutionCancelled
    from argus.core.role_tool_bridge import bridge_request
    from argus.reviewer import validation
    from argus.reviewer.tools import PREFIX

    monkeypatch.setenv(validation.IMAGE_ENV, "fixture:already-local")
    monkeypatch.setenv("DOCKER_HOST", "unix:///fixture-only-not-opened.sock")
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setattr(validation.shutil, "which", lambda _: "/fixture-only/docker")
    monkeypatch.setattr(validation, "WAIT_SECONDS", 0.01)
    calls, receipts = [], []
    started = threading.Event()

    def controlled_process(argv, **kwargs):
        # Only the protocol is exercised: no subprocess, provider, or Docker.
        calls.append(argv)
        command = argv[5]
        if command == "image":
            return subprocess.CompletedProcess(argv, 0, "sha256:fixture", "")
        if command == "create":
            assert argv[argv.index("--network") + 1] == "none"
            assert "--read-only" in argv and "--cap-drop" in argv
            assert any(value.endswith(",readonly") for value in argv)
            return subprocess.CompletedProcess(argv, 0, "fixture-container", "")
        if command == "start":
            started.set()
            if mode == "cancel":
                assert kwargs["cancelled"].wait(2), "Test failed to request cancellation"
                raise ExecutionCancelled
            if mode == "timeout":
                raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
            kwargs["stdout"].write(b"controlled validation evidence\n")
            return subprocess.CompletedProcess(argv, 7 if mode == "nonzero" else 0, "", "")
        assert command == "rm"
        return subprocess.CompletedProcess(argv, 1 if mode == "cleanup-failure" else 0, "", "fixture cleanup failed" if mode == "cleanup-failure" else "")

    monkeypatch.setattr(validation, "run_process", controlled_process)

    def reviewer_reply(_prompt, options):
        reply = bridge_request(PREFIX, validation.COMMAND_TOOL, {
            "argv": ["/fixture-only/check", "{scratch}"], "timeout_seconds": 0.1,
        }, env=options.extension_env)
        if mode == "cancel":
            assert reply["status"] == "running"
            assert started.wait(2), "Controlled validation did not reach execution"
            reply = bridge_request(PREFIX, validation.CANCEL_TOOL, {
                "command_id": reply["command_id"],
            }, env=options.extension_env)
        for _ in range(100):
            if reply["status"] != "running":
                break
            reply = bridge_request(PREFIX, validation.RESULT_TOOL, {
                "command_id": reply["command_id"],
            }, env=options.extension_env)
        assert reply["status"] != "running"
        receipts.append(reply)
        bridge_request(PREFIX, "defer_review" if mode in {"timeout", "cancel"} else "approve_review", {
            "review": "Explicit fixture judgment after inspecting execution evidence.",
        }, env=options.extension_env)
        return "The fixture submitted its explicit action."

    backend = MemoryBackend()
    backend.queue("reviewer", CannedResponse(message_factory=reviewer_reply))
    decision = evaluate(backend, tmp_path)
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["status"] == {"timeout": "timed_out", "cancel": "cancelled"}.get(mode, "completed")
    assert receipt["exit_code"] == (None if mode in {"timeout", "cancel"} else 7 if mode == "nonzero" else 0)
    persisted = json.loads(Path(receipt["receipt_path"]).read_text())
    assert persisted["status"] == receipt["status"]
    assert len([argv for argv in calls if argv[5] == "create"]) == 1
    assert len([argv for argv in calls if argv[5] == "rm"]) == 1
    if mode == "cleanup-failure":
        assert decision.backend_unavailable and decision.status == "blocked"
        assert persisted["cleanup_status"] == "failed"
    else:
        assert not decision.backend_unavailable
        assert decision.status == ("continue" if mode in {"timeout", "cancel"} else "done")
        assert persisted["cleanup_status"] == "removed"
