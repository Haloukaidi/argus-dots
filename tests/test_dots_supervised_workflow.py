"""Real upstream supervisor/state with deterministic supervised provider responses.

This is a scripted port fixture, not native execution or a file-host lease test.
The file-host admission/cancellation protocol is covered by its separate suite.
"""
from __future__ import annotations

import argparse
import json
import time

import pytest

from argus.adapters.dots_backend import DotsBackend, DotsCapabilities
from argus.apps._runtime_construction import build_dots_life_runner
from argus.core.dots_profile import SupervisedDotsProfile, SupervisedDotsTransport
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from argus.skills.vertical_select import persist_vertical
from tests.test_workflow_dots_lifecycle import EngineeringFixtureHost, RecordingSink


@pytest.mark.parametrize("concurrent_event", [False, True])
def test_supervised_original_lifecycle_detects_concurrent_state_drift(tmp_path, monkeypatch, concurrent_event):
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "dots")
    monkeypatch.setenv("ARGUS_SKILL_SAFE_MODE", "1")
    monkeypatch.setenv("ARGUS_SKILL_WIKI", "0")
    monkeypatch.setenv("ARGUS_SKILL_AUTO_INIT_WIKI", "0")
    project, state, skills = (tmp_path / name for name in ("project", "state", "skills"))
    project.mkdir()
    skills.mkdir()
    state.mkdir()
    # Match the real detached daemon's alias-then-log-open ordering. The
    # native trial exposed this ordinary host alias before Reviewer admission.
    from argus.daemon.state import _daemon_log_path, _point_active_daemon_log

    daemon_log = _daemon_log_path(state, boot_id="supervised-workflow-fixture")
    _point_active_daemon_log(state, daemon_log)
    daemon_log.parent.mkdir()
    daemon_log.write_text("fixture daemon started\n")
    persist_vertical(project, "software", workflow_mode="direct")
    results = []
    original_exec = DotsBackend.run_exec

    def observed_exec(self, **kwargs):
        result = original_exec(self, **kwargs)
        results.append((self.role, result))
        return result

    monkeypatch.setattr(DotsBackend, "run_exec", observed_exec)
    # The Reviewer validates read-only; bytecode would be a genuine mutation.
    import subprocess
    original_run = subprocess.run

    def no_bytecode(argv, **kwargs):
        if len(argv) > 1 and argv[1] == "-m":
            argv = [argv[0], "-B", *argv[1:]]
        return original_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", no_bytecode)

    class Host(EngineeringFixtureHost, SupervisedDotsTransport):
        capabilities = DotsCapabilities(resume=True, role_tools=True)

        def __init__(self):
            super().__init__(project)
            self.project_root = str(project)
            self.session_expires_at = time.time() + 600
            self.execution_profile = SupervisedDotsProfile(
                model_efforts=(("fixture", ("low", "high", "xhigh")), ("", ("low", "high", "xhigh"))),
                read_roots=(str(tmp_path),), report_roots=(str(tmp_path),),
            )

        def assert_ready(self, project_root=None):
            assert project_root is None or str(project_root) == self.project_root
            return {"deterministic_fixture": True, "session_expires_at": self.session_expires_at}

        def submit(self, request):
            if request.role == "engineer":
                self.requests.append(request)
                (project / "sample.py").write_text("def mean(values):\n    return sum(values) / len(values) if values else 0.0\n")
                (project / "test_sample.py").write_text(
                    "import unittest\nfrom sample import mean\n"
                    "class Checks(unittest.TestCase):\n"
                    "    def test_regular(self): self.assertEqual(mean([2, 4]), 3)\n"
                    "    def test_empty(self): self.assertEqual(mean([]), 0)\n"
                )
                self.events[request.request_id] = [
                    {"type": "accepted", "worker_id": "fixture-engineer"},
                    {"type": "message", "text": "Implemented both required cases and tests."},
                    {"type": "completed"},
                ]
                return
            if request.run_label == "manager-supervision":
                self.requests.append(request)
                self.events[request.request_id] = [
                    {"type": "accepted", "worker_id": "fixture-manager-supervision"},
                    {"type": "message", "text": json.dumps({
                        "action": "continue", "reason": "Continue the bounded fixture repair.",
                        "evidence_refs": ["mission-view.json"],
                    })},
                    {"type": "completed"},
                ]
                return
            super().submit(request)
            if concurrent_event and request.role == "reviewer":
                with (state / "events.jsonl").open("a") as stream:
                    stream.write('{"type":"fixture.concurrent.write"}\n')

    host = Host()
    args = argparse.Namespace(
        backend="dots", dots_execution_profile=host.execution_profile,
        workdir=str(project), skills_dir=str(skills), engineer_model="fixture", reviewer_model="fixture",
        max_rounds=1 if concurrent_event else 3, project_state_dir=str(state), manager_session_root=str(state),
    )
    runner = build_dots_life_runner(args, transport=host)
    memory = LifeMemory.open(state)
    supervisor = LifeSupervisor(memory=memory, runner=runner, sink=RecordingSink(state),
                                config=LifeSupervisorConfig(project_worktree=project, artifact_root=project),
                                engineer_model="fixture", reviewer_model="fixture")
    item = memory.backlog.add(BacklogItem.new(
        title="Verify sample mean", objective="Implement mean with zero for empty input and verify both cases.",
        manager_decision={"routed": True, "vertical": "software"},
        tags=["review:required", "scope:bounded"], iterate=False,
    ))
    outcome = supervisor.tick()
    review_results = [result for role, result in results if role == "reviewer"]
    assert review_results
    stored = next(row for row in LifeMemory.open(state).backlog.all() if row.id == item.id)
    if concurrent_event:
        assert any(result.exit_code != 0 and "protected review evidence changed" in str(result.fatal_error)
                   for result in review_results)
        assert not outcome.get("success") and stored.status != "done"
    else:
        assert outcome["success"] is True and stored.status == "done", (outcome, [(role, result.fatal_error) for role, result in results])
        assert [r.role for r in host.requests if r.role in {"engineer", "reviewer"}] == [
            "engineer", "reviewer",
        ]
        assert [r.exit_code for r in review_results] == [0]
        assert [v["exit_code"] for v in host.validations] == [0]
        assert [reply[2]["recorded"] for reply in host.replies] == ["approve_review"]
        assert outcome["cost_usd"] is None
        assert outcome["pricing_status"] == "empty"
        assert all(result.cost_usd is None and not result.input_tokens_present
                   and not result.output_tokens_present for _, result in results)
