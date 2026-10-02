"""Read-only source diagnosis. Real runtime + files, controlled offline role host.
These tests deliberately do not prove native isolation or provider behavior.
"""

import argparse
import hashlib
import json
import os
import re
import socket
from pathlib import Path

import pytest

from argus.adapters.agent_cli_backend import AgentCliBackend
from argus.adapters.dots_backend import DotsBackend, DotsCapabilities
from argus.adapters.dots_role_host import RoleFileDotsTransport
from argus.apps._runtime_construction import build_dots_life_runner
from argus.life.event_log import JsonlEventSink
from argus.life.memory import BacklogItem, LifeMemory
from argus.life.supervisor import LifeSupervisor, LifeSupervisorConfig
from argus.reviewer import Reviewer, ReviewerConfig
from argus.skills.vertical_select import persist_vertical


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No provider or external network allowed in handoff diagnosis")

    monkeypatch.setattr(AgentCliBackend, "run_exec", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setenv("ARGUS_SKILL_SAFE_MODE", "1")
    monkeypatch.setenv("ARGUS_SKILL_WIKI", "0")
    monkeypatch.setenv("ARGUS_SKILL_AUTO_INIT_WIKI", "0")


class Host:
    # These are test-host capabilities, not a claim about native host isolation.
    capabilities = DotsCapabilities(
        resume=True,
        role_tools=True,
        options=frozenset(
            {
                "working_dir",
                "add_dirs",
                "skill_paths",
                "full_auto",
                "live_search",
                "force_safe_mode",
                "sandbox_mode:read-only",
                "sandbox_mode:workspace-write",
            }
        ),
    )

    def __init__(self, project, case):
        self.project, self.case = project, case
        self.requests, self.events, self.replies, self.reads = [], {}, [], []
        self.engineer_turns = 0
        self.held = None
        self.path = None
        self.expected = None
        self.completion_observations = []

    def submit(self, req):
        self.requests.append(req)
        events = [{"type": "accepted", "worker_id": "fixture-" + req.role}]
        if req.role == "engineer":
            self.engineer_turns += 1
            cwd = Path(req.options["working_dir"])
            assert cwd.resolve() == self.project.resolve()
            self.path = cwd / (
                "a1b2c3d4" * 4 + ".json" if self.case == "hex-filename" else "产物 with spaces.json"
            )
            if self.case == "hex-directory":
                self.path = cwd / ("a1b2c3d4" * 4) / "result.json"
                self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(
                {"marker": "current", "turn": self.engineer_turns}, ensure_ascii=False
            ).encode()
            self.expected = hashlib.sha256(payload).hexdigest()
            if self.case == "only-path":
                pass
            elif self.case == "late-write":
                self.pending = payload
            elif self.case == "unflushed":
                self.held = self.path.open("wb")
                self.held.write(payload)
            elif self.case == "wrong-cwd":
                wrong = cwd.parent / "other worktree"
                wrong.mkdir(exist_ok=True)
                (wrong / self.path.name).write_bytes(payload)
            else:
                self.path.write_bytes(payload)
                if self.case == "unreadable":
                    self.path.chmod(0)
            path_hint = (
                str(self.path) if self.case == "absolute" else str(self.path.relative_to(cwd))
            )
            self.completion_observations.append(
                {
                    "exists": self.path.exists(),
                    "size": self.path.stat().st_size if self.path.exists() else None,
                }
            )
            events.append(
                {
                    "type": "message",
                    "text": f"ARTIFACT_PATH={path_hint}\nEngineer work is complete.",
                }
            )
        elif req.role == "reviewer":
            assert req.options["sandbox_mode"] == "read-only"
            cwd = Path(req.options["working_dir"])
            assert cwd.resolve() == self.project.resolve()
            hint = re.search(r"ARTIFACT_PATH=(.*)", req.prompt).group(1)
            expected = (
                self.expected
            )  # host-only independent transport evidence, never prompt authority
            p = Path(hint)
            actual_path = p if p.is_absolute() else cwd / p
            row = {
                "cwd": str(cwd),
                "path": str(actual_path),
                "request_id": req.request_id,
                "expected_sha256": expected,
                "exists": actual_path.exists(),
            }
            try:
                content = actual_path.read_bytes()
                row.update(size=len(content), sha256=hashlib.sha256(content).hexdigest())
                try:
                    content_value = json.loads(content)
                except ValueError:
                    content_value = None
                readable = content_value == {"marker": "current", "turn": self.engineer_turns}
            except OSError as e:
                row.update(error=type(e).__name__)
                readable = False
            if self.case == "hex-state-root":
                event_log = Path(re.search(r"The event log is at `([^`]+)`", req.prompt).group(1))
                event_content = event_log.read_text(encoding="utf-8")
                assert '"round.review.started"' in event_content
                row["event_log_path"] = str(event_log)
                row["event_log_readable"] = True
            self.reads.append(row)
            if self.case == "late-write":
                # Deterministic race: producer completed first, verifier read first,
                # then publication arrives. No timer or inferred process status.
                self.path.write_bytes(self.pending)
            if self.case == "unflushed":
                self.held.close()
                self.held = None
            if readable:
                revise = self.case == "two-rounds" and self.engineer_turns == 1
                action = "revise_review" if revise else "approve_review"
                args = {
                    "review": "Real fixture bytes inspected; next version required."
                    if revise
                    else "Real fixture bytes were read and contain the current expected content."
                }
            else:
                action = "request_review_decision"
                args = {
                    "review": "The artifact is absent, unreadable, or not the completed version; no approval.",
                    "question": "Where is the published readable artifact for this round?",
                }
            events.append({"type": "tool_call", "name": action, "arguments": args})
        elif req.role == "manager" and req.run_label == "manager-stage":
            events.append(
                {
                    "type": "message",
                    "text": "ACTION=complete\nTARGET_STAGE=delivery\nREASON=Independent fixture read and hash passed.",
                }
            )
        elif req.role == "manager" and req.run_label == "reflection":
            events.append({"type": "message", "text": "WROTE: nothing"})
        else:
            raise AssertionError(f"Unexpected role/label {req.role}/{req.run_label}")
        events.append({"type": "completed"})
        self.events[req.request_id] = events

    def poll(self, rid, seq):
        return {
            "protocol_version": 1,
            "request_id": rid,
            "sequence": seq,
            **self.events[rid][seq - 1],
        }

    def tool_result(self, rid, seq, result):
        self.replies.append({"request_id": rid, "sequence": seq, "result": result})

    def finish(self, rid):
        pass

    def cancel(self, rid, reason):
        raise AssertionError("Unexpected cancellation: " + reason)


@pytest.mark.parametrize(
    "case,project_name",
    [
        ("relative", "project"),
        ("absolute", "项目 with spaces"),
        ("relative", "deep/" * 60 + "project"),
        ("two-rounds", "two-rounds"),
        ("only-path", "only-path"),
        ("late-write", "late-write"),
        ("unflushed", "unflushed"),
        ("wrong-cwd", "project root"),
        pytest.param(
            "unreadable",
            "unreadable",
            marks=pytest.mark.skipif(
                os.name != "posix" or getattr(os, "geteuid", lambda: 0)() == 0,
                reason="This case requires ordinary-user POSIX file permissions",
            ),
        ),
        ("hex-filename", "hex-filename"),
        ("hex-directory", "hex-directory"),
        ("hex-state-root", "hex-state-root"),
    ],
)
def test_real_factory_artifact_contract(tmp_path, case, project_name):
    project = tmp_path / project_name
    project.mkdir(parents=True)
    skills = tmp_path / "skills"
    skills.mkdir()
    life = tmp_path / ("a1b2c3d4" * 4 if case == "hex-state-root" else "life")
    persist_vertical(project, "software", workflow_mode="direct")
    host = Host(project, case)
    args = argparse.Namespace(
        backend="codex",
        workdir=str(project),
        skills_dir=str(skills),
        engineer_model="fixture",
        reviewer_model="fixture",
        max_rounds=3,
        project_state_dir=str(life),
        manager_session_root=str(life),
    )
    runner = build_dots_life_runner(args, transport=host)
    memory = LifeMemory.open(life)
    supervisor = LifeSupervisor(
        memory=memory,
        runner=runner,
        sink=JsonlEventSink(None, life_dir=life, verbosity="full"),
        config=LifeSupervisorConfig(project_worktree=project, artifact_root=project),
    )
    item = memory.backlog.add(
        BacklogItem.new(
            title="Publish and review fixture artifact",
            objective="Create the current artifact and independently inspect its content before approval.",
            manager_decision={"routed": True, "vertical": "software"},
            tags=["review:required", "scope:bounded"],
            iterate=False,
        )
    )
    try:
        outcome = supervisor.tick()
        persisted = LifeMemory.open(life)
        stored = next(x for x in persisted.backlog.all() if x.id == item.id)
        row = {
            "case": case,
            "project_name": project_name,
            "outcome": outcome,
            "persisted_status": stored.status,
            "roles": [r.role for r in host.requests],
            "requests": [
                {
                    "id": r.request_id,
                    "role": r.role,
                    "label": r.run_label,
                    "resume": r.resume_thread_id,
                    "cwd": r.options.get("working_dir"),
                }
                for r in host.requests
            ],
            "completion_observations": host.completion_observations,
            "reads": host.reads,
            "tool_replies": host.replies,
        }
        (tmp_path / "handoff-evidence.json").write_text(
            json.dumps(row, ensure_ascii=False), encoding="utf-8"
        )
        success = case in {"relative", "absolute", "two-rounds", "hex-filename", "hex-directory", "hex-state-root"}
        assert (outcome["status"] == "done") == success, row
        assert (stored.status == "done") == success, row
        assert len(host.reads) == (2 if case == "two-rounds" else 1), row
        assert len({r.request_id for r in host.requests}) == len(host.requests)
        assert host.engineer_turns == (2 if case == "two-rounds" else 1)
        if success:
            assert host.replies[-1]["result"].get("recorded") == "approve_review"
            assert all(r["sha256"] == r["expected_sha256"] for r in host.reads)
            before = len(host.requests)
            assert supervisor.tick() is None
            assert len(host.requests) == before
        else:
            assert stored.status == "paused_operator"
            assert host.replies[-1]["result"].get("recorded") == "request_review_decision"
            assert not any(x["result"].get("recorded") == "approve_review" for x in host.replies)
        if case == "two-rounds":
            assert len(host.reads) == 2 and host.reads[0]["sha256"] != host.reads[1]["sha256"]
    finally:
        if host.held:
            host.held.close()
        if host.path and host.path.exists():
            host.path.chmod(0o600)


@pytest.mark.skipif(os.name != "posix", reason="Native file bridge requires POSIX")
def test_native_role_transport_cannot_claim_real_reviewer_execution_controls(tmp_path):
    queue = RoleFileDotsTransport(tmp_path / "queue")
    result = Reviewer(DotsBackend(queue, role="reviewer")).evaluate(
        objective="Read local artifact before independent review",
        round_index=1,
        session_id=None,
        main_summary="Artifact generated.",
        main_error=None,
        config=ReviewerConfig(active_vertical="software", working_dir=str(tmp_path)),
    )
    assert result.status == "blocked" and result.backend_unavailable
    assert "lacks required execution capabilities" in result.backend_fatal_error
    assert not list(queue.root.glob("*/request.json"))
    (tmp_path / "capability-evidence.json").write_text(
        json.dumps(
            {
                "case": "native-capability-gate",
                "status": result.status,
                "fatal_error": result.backend_fatal_error,
                "requests_published": 0,
            }
        ),
        encoding="utf-8",
    )
