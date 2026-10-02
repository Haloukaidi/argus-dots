"""Real Argus lifecycle with explicitly controlled, offline backend fixtures.

No model/provider or native isolation is exercised here. The fixture Engineer
writes only the generated sample project; fixture Reviewer runs its real tests
before submitting actions through the original dispatcher.
"""
from __future__ import annotations

import argparse
import errno
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from argus.adapters.dots_backend import DotsBackend, DotsCapabilities, DotsRequest
from argus.adapters.dots_file_transport import FileDotsTransport
from argus.apps._runtime_construction import build_dots_life_runner
from argus.core.models import RunnerOptions
from argus.core.run_gateway import run_exec
from argus.life.event_log import JsonlEventSink
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from argus.planner import PlannerConfig
from argus.skills.vertical_select import persist_vertical


class EngineeringFixtureHost:
    capabilities = DotsCapabilities(
        resume=True, role_tools=True,
        options=frozenset({"working_dir", "add_dirs", "skill_paths", "full_auto", "live_search",
                           "force_safe_mode", "sandbox_mode:read-only", "sandbox_mode:workspace-write"}),
    )

    def __init__(self, project: Path):
        self.project = project
        self.requests = []
        self.events = {}
        self.replies = []
        self.validations = []
        self.engineer_turns = 0

    def submit(self, request):
        self.requests.append(request)
        events = [{"type": "accepted", "worker_id": "fixture-" + request.role}]
        if request.role == "engineer":
            self.engineer_turns += 1
            implementation = "def mean(values):\n    return sum(values) / len(values)\n"
            if self.engineer_turns > 1:
                assert "empty-input" in request.prompt
                implementation = "def mean(values):\n    return sum(values) / len(values) if values else 0\n"
            (self.project / "sample.py").write_text(implementation, encoding="utf-8")
            (self.project / "test_sample.py").write_text(
                "import unittest\nfrom sample import mean\n"
                "class Checks(unittest.TestCase):\n"
                "    def test_regular(self): self.assertEqual(mean([2, 4]), 3)\n"
                "    def test_empty(self): self.assertEqual(mean([]), 0)\n",
                encoding="utf-8",
            )
            events.append({"type": "message", "text": "Sample implementation and tests written."})
        elif request.role == "reviewer":
            result = subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-v"],
                cwd=self.project, capture_output=True, text=True, timeout=10,
            )
            self.validations.append({"exit_code": result.returncode, "output": result.stdout + result.stderr})
            action = "revise_review" if result.returncode else "approve_review"
            review = "Fix the empty-input failure." if result.returncode else "Both real sample tests pass."
            events.append({"type": "tool_call", "name": action, "arguments": {"review": review}})
        elif request.role == "manager" and request.run_label == "manager-stage":
            assert self.validations[-1]["exit_code"] == 0
            events.append({"type": "message", "text":
                           "ACTION=complete\nTARGET_STAGE=delivery\nREASON=The sample and both cases passed independent review."})
        elif request.role == "planner":
            events.append({"type": "message", "text": "\n".join([
                "PROJECT_DONE=false", "REASON=Implement and independently verify the sample",
                "TASK_KEY=sample", "TASK_TITLE=Verify sample mean",
                "TASK_OBJECTIVE=Implement mean with zero for empty input and verify both cases.",
                "TASK_REQUIRE_INDEPENDENT_REVIEW=true",
            ])})
        elif request.role == "manager" and request.run_label == "reflection":
            events.append({"type": "message", "text": "WROTE: nothing"})
        else:
            raise AssertionError("Unexpected fixture role: " + request.role)
        events.append({"type": "completed"})
        self.events[request.request_id] = events

    def poll(self, request_id, sequence):
        return {"protocol_version": 1, "request_id": request_id, "sequence": sequence,
                **self.events[request_id][sequence - 1]}

    def tool_result(self, request_id, sequence, result):
        self.replies.append((request_id, sequence, result))

    def finish(self, request_id):
        pass

    def cancel(self, request_id, reason):
        raise AssertionError("Unexpected fixture cancellation: " + reason)


class RecordingSink:
    def __init__(self, life_dir):
        self.events = []
        self.persisted = JsonlEventSink(None, life_dir=life_dir, verbosity="full")

    def handle_event(self, event):
        self.events.append(event)
        self.persisted.handle_event(event)


@pytest.mark.parametrize(("project_name", "planned"), [
    ("project", False), ("项目 with spaces", False),
    ("nested/" * 38 + "project", False), ("planner project", True),
])
def test_actual_runtime_supervisor_repairs_sample_and_persists_terminal_state(tmp_path, monkeypatch, project_name, planned):
    monkeypatch.setenv("ARGUS_SKILL_SAFE_MODE", "1")
    monkeypatch.setenv("ARGUS_SKILL_WIKI", "0")
    monkeypatch.setenv("ARGUS_SKILL_AUTO_INIT_WIKI", "0")
    project = tmp_path / project_name
    project.mkdir(parents=True)
    skills = tmp_path / "skills"
    skills.mkdir()
    life = tmp_path / "life"
    persist_vertical(project, "software", workflow_mode="direct")
    host = EngineeringFixtureHost(project)
    args = argparse.Namespace(
        backend="codex", workdir=str(project), skills_dir=str(skills),
        engineer_model="fixture", reviewer_model="fixture", max_rounds=3,
        project_state_dir=str(life), manager_session_root=str(life),
    )
    runner = build_dots_life_runner(args, transport=host)
    memory = LifeMemory.open(life)
    sink = RecordingSink(life)
    supervisor = LifeSupervisor(memory=memory, runner=runner, sink=sink,
        config=LifeSupervisorConfig(project_worktree=project, artifact_root=project),
        planner_runner=runner.planner_backend if planned else None)
    if planned:
        supervisor._vertical_resolved = True
        supervisor._planner_config = lambda: PlannerConfig(working_dir=str(project), open_ended=True)
        assert supervisor._plan_next_work() is True
        pending = memory.backlog.pending()
        assert len(pending) == 1 and "review:required" in pending[0].tags
        item = pending[0]
    else:
        item = memory.backlog.add(BacklogItem.new(
            title="Verify sample mean", objective="Implement mean with zero for empty input and verify both cases.",
            manager_decision={"routed": True, "vertical": "software"},
            tags=["review:required", "scope:bounded"], iterate=False,
        ))
    outcome = supervisor.tick()
    assert outcome is not None and outcome["status"] == "done", (outcome, host.requests)
    assert outcome["success"] is True
    expected_roles = (["planner"] if planned else []) + ["engineer", "reviewer", "engineer", "reviewer"]
    expected_roles += ["manager"] if planned else ["manager", "manager"]
    assert [r.role for r in host.requests] == expected_roles
    # Planner-authored intermediate tasks preserve the original stage-authority
    # rule; ordinary direct missions ask Manager to close the delivery stage.
    assert [r.run_label for r in host.requests if r.role == "manager"] == (
        ["reflection"] if planned else ["manager-stage", "reflection"]
    )
    assert [v["exit_code"] for v in host.validations] == [1, 0]
    assert "ZeroDivisionError" in host.validations[0]["output"]
    assert "Ran 2 tests" in host.validations[1]["output"]
    assert [reply[2]["recorded"] for reply in host.replies] == ["revise_review", "approve_review"]
    reopened = LifeMemory.open(life)
    stored = next(row for row in reopened.backlog.all() if row.id == item.id)
    assert stored.status == "done"
    persisted = [json.loads(line) for line in (life / "events.jsonl").read_text().splitlines()]
    assert [e["status"] for e in persisted if e.get("type") == "round.review.completed"] == ["continue", "done"]
    assert [e for e in persisted if e.get("type") == "life.mission.completed"]
    before = len(host.requests)
    assert supervisor.tick() is None
    assert len(host.requests) == before


@pytest.mark.parametrize("error", [errno.ENOSPC, errno.EIO, errno.EROFS, errno.EACCES])
def test_queue_write_failure_never_returns_success_or_leaves_partial_record(tmp_path, monkeypatch, error):
    queue = FileDotsTransport(tmp_path / "queue")
    original = os.fsync

    def fail_fsync(_fd):
        raise OSError(error, os.strerror(error))

    monkeypatch.setattr(os, "fsync", fail_fsync)
    result = run_exec(DotsBackend(queue, timeout_seconds=0.1, cancellation_grace_seconds=0.01),
                      prompt="Only this disk failure fixture", run_label="fault", options=RunnerOptions())
    assert result.exit_code != 0 and result.stop_kind == "permanent_error"
    assert os.strerror(error) in result.fatal_error
    assert "unconfirmed" in result.fatal_error
    monkeypatch.setattr(os, "fsync", original)
    assert not list(queue.root.rglob(".pending-*"))
    assert not list(queue.root.glob("*/request.json"))
    assert not list(queue.root.glob("*/event-*.json"))


def test_large_real_response_survives_restart_and_rejects_over_limit(tmp_path):
    queue = FileDotsTransport(tmp_path / "queue")
    req = DotsRequest("a" * 32, "fixture", "large-log", None, None, time.time(), time.time() + 60)
    queue.submit(req)
    queue.emit(req.request_id, "accepted", worker_id="fixture-large-output")
    chunk = "数" * 150_000
    for _ in range(16):
        queue.emit(req.request_id, "message", text=chunk)
    restarted = FileDotsTransport(queue.root)
    assert len(restarted.inspect(req.request_id)["events"]) == 17
    restarted.emit(req.request_id, "message", text=chunk)
    restarted.emit(req.request_id, "message", text=chunk)
    from argus.adapters.dots_backend import DotsBridgeError
    with pytest.raises(DotsBridgeError, match="8 MiB"):
        restarted.emit(req.request_id, "message", text=chunk)
    restarted.emit(req.request_id, "completed")
    restarted.finish(req.request_id)
    assert restarted.inspect(req.request_id)["closed"] == {"status": "consumed"}
