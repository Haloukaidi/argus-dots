"""Deterministic supervised-profile fixtures, never native isolation evidence."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import pytest

from argus.adapters.dots_admission import BoundedDotsRoleHost, BoundedRoleFileDotsTransport
from argus.adapters.dots_backend import DotsBackend, DotsBridgeError, DotsRequest
from argus.adapters.dots_supervised import SupervisedDotsRoleHost, SupervisedRoleFileDotsTransport
from argus.core.dots_profile import SupervisedDotsProfile
from argus.core.models import RunnerOptions

OWNER = {"coordinator_task": "/root/coordinate_fixture", "generation": 1}


def profile():
    return SupervisedDotsProfile(model_efforts=(("", ("low", "high", "xhigh")),
                                               ("fixture-model", ("high", "xhigh"))))


def setup(tmp_path, *, policy=None):
    project = tmp_path / "project"
    project.mkdir()
    policy = policy or profile()
    raw = SupervisedRoleFileDotsTransport(tmp_path / "queue", profile=policy)
    host = SupervisedDotsRoleHost(raw)
    session = host.create(parent_task="/root", coordinator_task=OWNER["coordinator_task"],
                          producer_id="fixture-producer", project_root=project,
                          mission_id="fixture-workflow", allowed_roles=["manager", "reviewer", "engineer"],
                          max_requests=8, lifetime_seconds=600)
    bound = SupervisedRoleFileDotsTransport(raw.root, profile=policy,
        session_id=session["session_id"], producer_id="fixture-producer", project_root=project)
    return bound, host, session["session_id"]


def reviewer_fixture_state(host, sid, rid, future):
    """Keep the producer's original failure visible if a host phase fails."""
    try:
        snapshot = host.status(sid)["tasks"][rid]
        state = {key: snapshot[key] for key in ("accepted", "terminal", "cancel_requested", "closed")}
    except DotsBridgeError as exc:
        state = {"snapshot_error": str(exc)}
    if future.done():
        result = future.result()
        state["backend"] = {"exit_code": result.exit_code, "fatal_error": result.fatal_error}
    else:
        state["backend"] = "still running"
    return state


def settled_review_tool(monkeypatch, bound, host, sid, rid, future, deadline, **call):
    """Wait for the real producer settlement without taking its journal locks."""
    settled = Event()
    original = bound.tool_result

    def persist_reply(*args, **kwargs):
        original(*args, **kwargs)
        settled.set()  # The durable reply, not the in-memory decision, is ready.

    with monkeypatch.context() as patch:
        patch.setattr(bound, "tool_result", persist_reply)
        try:
            reply = host.request_tool(sid, rid, **call, **OWNER)
        except DotsBridgeError as exc:
            pytest.fail(f"typed action admission failed: {exc}; {reviewer_fixture_state(host, sid, rid, future)}")
        assert reply["status"] == "pending"
        # Repeated request_tool calls acquire the same root/task locks needed
        # by claim_tool_call and tool_result. A busy CI runner can exhaust the
        # producer's bounded lock wait and cancel this otherwise valid review.
        while not settled.wait(timeout=0.01):
            if future.done() or time.monotonic() >= deadline:
                pytest.fail(f"typed action was not settled: {reviewer_fixture_state(host, sid, rid, future)}")
        try:
            reply = host.request_tool(sid, rid, **call, **OWNER)
        except DotsBridgeError as exc:
            pytest.fail(f"typed action reply failed: {exc}; {reviewer_fixture_state(host, sid, rid, future)}")
    assert reply["status"] == "ready", reviewer_fixture_state(host, sid, rid, future)
    assert reply["result"] == {"recorded": "approve_review"}
    return reply


def request(number=1, **changes):
    return replace(DotsRequest(f"{number:032x}", "fixture prompt", "fixture", None, "high",
                               time.time(), time.time()+60), **changes)


def test_profile_round_trip_and_exact_deficit_consent():
    p = profile()
    assert SupervisedDotsProfile.from_dict(p.to_dict()) == p
    assert p.name == "supervised-approx-v1"
    changed = p.to_dict()
    changed["accepted_deficits"].remove("disable_tools")
    with pytest.raises(ValueError):
        SupervisedDotsProfile.from_dict(changed)


def test_profile_never_advertises_enforced_controls(tmp_path):
    bound, _, _ = setup(tmp_path)
    assert bound.capabilities.options == frozenset()
    assert bound.capabilities.role_tools and bound.capabilities.resume
    assert DotsBackend(bound).capability_report()["supported_options"] == []


def test_host_requires_explicit_fresh_lease(tmp_path):
    bound, host, sid = setup(tmp_path)
    with pytest.raises(DotsBridgeError, match="host-required"):
        bound.assert_ready()
    host.heartbeat(sid, **OWNER)
    bound.assert_ready()
    with pytest.raises(DotsBridgeError):
        bound.assert_ready(project_root=tmp_path)
    with pytest.raises(DotsBridgeError):
        host.heartbeat(sid, coordinator_task="/root/wrong", generation=1)


def test_strict_backend_does_not_adopt_transport_profile(tmp_path):
    bound, host, sid = setup(tmp_path)
    host.heartbeat(sid, **OWNER)
    result = DotsBackend(bound).run_exec(prompt="fixture", run_label="fixture",
                                         options=RunnerOptions(disable_tools=True))
    assert result.exit_code != 0
    assert not host.status(sid)["tasks"]


def test_approximate_submission_preserves_original_options_and_missions(tmp_path):
    bound, host, sid = setup(tmp_path)
    host.heartbeat(sid, **OWNER)
    original = request(options={"working_dir": bound.project_root,
                                "sandbox_mode": "read-only", "disable_tools": True})
    bound.submit(original)
    action = host.next(sid, **OWNER)
    assert action["request"] == original.to_dict()
    assert action["execution_profile"]["name"] == "supervised-approx-v1"
    assert action["dispatch"]["reasoning_effort"] == "high"
    assert action["model_resolution"]["observed_model"] is None
    later = request(2, role="engineer", mission_id="upstream-created-mission")
    bound.submit(later)
    assert host.status(sid)["session"]["mission_id"] == "fixture-workflow"


@pytest.mark.parametrize("changes", [
    {"options": {"isolate_workdir": True}},
    {"options": {"extra_args": ["--unsafe"]}},
    {"model": "unknown-model"},
    {"reasoning_effort": "unknown-effort"},
])
def test_unsupported_requests_fail_before_publication(tmp_path, changes):
    bound, host, sid = setup(tmp_path)
    host.heartbeat(sid, **OWNER)
    with pytest.raises(DotsBridgeError):
        bound.submit(request(**changes))
    assert not host.status(sid)["tasks"]


def test_protocol_three_cannot_adopt_approximate_session(tmp_path):
    bound, _, sid = setup(tmp_path)
    old = BoundedDotsRoleHost(BoundedRoleFileDotsTransport(bound.root))
    with pytest.raises(DotsBridgeError):
        old.status(sid)


def test_changed_profile_cannot_adopt_session(tmp_path):
    bound, host, sid = setup(tmp_path)
    host.heartbeat(sid, **OWNER)
    changed = SupervisedRoleFileDotsTransport(bound.root, profile=SupervisedDotsProfile(),
        session_id=sid, producer_id=bound.producer_id, project_root=bound.project_root)
    with pytest.raises(DotsBridgeError):
        changed.assert_ready()


def test_original_backend_supervised_options_are_audited_without_capability_upgrade(tmp_path):
    bound, host, sid = setup(tmp_path)
    host.heartbeat(sid, **OWNER)
    options = RunnerOptions(working_dir=bound.project_root, reasoning_effort="high",
                            sandbox_mode="read-only", disable_tools=True, dangerous_yolo=True,
                            watchdog_soft_idle_seconds=5, inactivity_callback=lambda _: "restart")
    backend = DotsBackend(bound, execution_profile=bound.execution_profile,
                          timeout_seconds=5, poll_interval=0.001)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(backend.run_exec, prompt="fixture", run_label="fixture", options=options)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            action = host.next(sid, **OWNER)
            if action["action"] == "spawn":
                break
            time.sleep(0.001)
        else:
            pytest.fail("fixture producer did not submit")
        rid = action["request_id"]
        host.bind(sid, rid, worker_id="fixture-worker", worker_task=action["claim"]["worker_task"], **OWNER)
        host.record(sid, rid, turn_request_id=rid, kind="completed", worker_id="fixture-worker",
                    worker_status="completed", text=f"ARGUS_DOTS_CALL:{rid}\nfixture result", **OWNER)
        result = future.result(timeout=1)
    assert result.exit_code == 0
    assert not result.input_tokens_present and not result.output_tokens_present
    assert result.usage_model == ""
    assert action["request"]["options"] == {
        "working_dir": bound.project_root, "sandbox_mode": "read-only", "disable_tools": True,
        "dangerous_yolo": True, "watchdog_soft_idle_seconds": 5, "inactivity_callback": True}
    assert action["option_dispositions"]["dangerous_yolo"] == "ignored-without-privilege-expansion"
    assert bound.capabilities.options == frozenset()


def test_resume_preserves_original_mission_and_model_effort(tmp_path):
    bound, host, sid = setup(tmp_path)
    host.heartbeat(sid, **OWNER)
    original = request(mission_id="upstream-mission")
    bound.submit(original)
    action = host.next(sid, **OWNER)
    rid = action["request_id"]
    host.bind(sid, rid, worker_id="fixture-worker", worker_task=action["claim"]["worker_task"], **OWNER)
    host.record(sid, rid, turn_request_id=rid, kind="completed", worker_id="fixture-worker",
                worker_status="completed", text=f"ARGUS_DOTS_CALL:{rid}\nfixture", **OWNER)
    for changes in ({"mission_id": "different"}, {"reasoning_effort": "xhigh"},
                    {"role": "engineer"}, {"model": "fixture-model"}):
        with pytest.raises(DotsBridgeError):
            bound.submit(replace(request(2, mission_id="upstream-mission", resume_thread_id="fixture-worker"), **changes))
    bound.submit(request(2, mission_id="upstream-mission", resume_thread_id="fixture-worker"))
    assert host.next(sid, **OWNER)["action"] == "followup"


def test_readiness_never_recreates_a_deleted_bound_queue(tmp_path):
    import shutil
    bound, _, _ = setup(tmp_path)
    shutil.rmtree(bound.root)
    with pytest.raises(OSError):
        bound.assert_ready()
    assert not bound.root.exists()


@pytest.mark.parametrize("options", [RunnerOptions(isolate_workdir=True),
    RunnerOptions(extra_args=["--unsafe"]), RunnerOptions(extension_env={"API_KEY": "fixture-not-a-secret"}),
    RunnerOptions(disable_tools="yes"), RunnerOptions(output_schema={"type": "object"})])
def test_backend_unknown_or_unsupported_controls_remain_fail_closed(tmp_path, options):
    bound, host, sid = setup(tmp_path)
    host.heartbeat(sid, **OWNER)
    result = DotsBackend(bound, execution_profile=bound.execution_profile).run_exec(
        prompt="fixture", run_label="fixture", options=options)
    assert result.exit_code != 0
    assert not host.status(sid)["tasks"]


def test_exact_enrolled_retry_survives_close_and_stale_lease(tmp_path, monkeypatch):
    bound, host, sid = setup(tmp_path)
    host.heartbeat(sid, **OWNER)
    original = request(options={"working_dir": bound.project_root})
    bound.submit(original)
    bound.close_producer()
    monkeypatch.setattr(bound, "assert_admission_current", lambda: (_ for _ in ()).throw(DotsBridgeError("locator changed")))
    with host.transport._root_fd() as root:
        lease = host.transport._read(root, host._lease_name(sid))
        lease["observed_at"] -= 100
        lease["expires_at"] -= 100
        host.transport._write(root, host._lease_name(sid), lease, replace=True)
    bound.submit(original)
    assert len(host.status(sid)["tasks"]) == 1
    with pytest.raises(DotsBridgeError):
        bound.submit(replace(original, prompt="changed bytes"))
    with pytest.raises(DotsBridgeError):
        bound.submit(request(2))


@pytest.mark.parametrize("field,value", [("version", True), ("version", 1.0),
    ("filesystem_isolation", 0), ("native_tool_allowlist", 0)])
def test_profile_metadata_rejects_equal_but_wrong_types(field, value):
    altered = profile().to_dict()
    altered[field] = value
    with pytest.raises(ValueError):
        SupervisedDotsProfile.from_dict(altered)


def test_declared_effort_override_is_explicit_and_never_a_model_fallback():
    p = SupervisedDotsProfile(model_efforts=(("", ("xhigh",)),),
                              effort_overrides=(("low", "xhigh"), ("high", "xhigh")))
    assert p.dispatch(None, "low") == {"reasoning_effort": "xhigh"}
    resolution = p.resolution(None, "low")
    assert resolution["requested_reasoning_effort"] == "low"
    assert resolution["effort_resolution"] == "explicit-profile-override"
    assert resolution["observed_model"] is None
    assert SupervisedDotsProfile.from_dict(p.to_dict()) == p
    with pytest.raises(ValueError):
        p.dispatch("missing-model", "low")
    with pytest.raises(ValueError):
        p.dispatch(None, "unsupported-effort")


def test_unapproved_external_paths_fail_before_publication(tmp_path):
    bound, host, sid = setup(tmp_path)
    host.heartbeat(sid, **OWNER)
    outside = tmp_path / "outside"
    outside.mkdir()
    for options in ({"add_dirs": [str(outside)]}, {"skill_paths": [str(outside)]},
                    {"review_output": {"path": str(outside / "REVIEW.md"), "receipt": str(outside / "receipt.json")}}):
        with pytest.raises(DotsBridgeError, match="approved"):
            bound.submit(request(options=options))
    assert not host.status(sid)["tasks"]


def test_supervised_cli_requires_profile_and_explicit_heartbeat(tmp_path, capsys):
    import json

    from argus.apps.dots_supervised import main

    project = tmp_path / "project"
    project.mkdir()
    profile_file = tmp_path / "profile.json"
    profile_file.write_text(json.dumps(profile().to_dict()))
    common = ["--bridge-dir", str(tmp_path / "queue"), "--profile-file", str(profile_file)]
    assert main([*common, "create", "--parent", "/root", "--coordinator", OWNER["coordinator_task"],
                 "--producer", "fixture", "--project-root", str(project), "--mission", "workflow",
                 "--roles", "manager", "--max-requests", "3"]) == 0
    sid = json.loads(capsys.readouterr().out)["session_id"]
    assert main([*common, "heartbeat", sid, "--owner", OWNER["coordinator_task"], "--generation", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["version"] == 4


@pytest.mark.parametrize("mutate", [False, True])
@pytest.mark.parametrize("slow_settlement", [False, True])
def test_original_typed_approval_requires_post_terminal_evidence_check(tmp_path, monkeypatch, mutate, slow_settlement):
    from pathlib import Path

    from argus.reviewer.tools import ReviewActions

    bound, host, sid = setup(tmp_path)
    candidate = Path(bound.project_root) / "candidate.txt"
    candidate.write_text("verified candidate")
    if slow_settlement:
        original_write = bound._write

        def delayed_reply(fd, name, value, **kwargs):
            if name.startswith("tool-reply-"):
                # Hold the real journal locks longer than their 0.5s contender
                # budget. The host must wait for settlement without polling.
                time.sleep(0.7)
            return original_write(fd, name, value, **kwargs)

        monkeypatch.setattr(bound, "_write", delayed_reply)
    host.heartbeat(sid, **OWNER)
    backend = DotsBackend(bound, role="reviewer", execution_profile=bound.execution_profile,
                          timeout_seconds=5, poll_interval=0.001)
    actions = ReviewActions()

    def producer():
        with backend.bind_role_tools(actions.tools, actions.dispatch):
            return backend.run_exec(prompt="fixture independent review", run_label="reviewer",
                options=RunnerOptions(working_dir=bound.project_root, sandbox_mode="read-only", reasoning_effort="high"))

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(producer)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            action = host.next(sid, **OWNER)
            if action["action"] == "spawn":
                break
            time.sleep(0.001)
        else:
            pytest.fail("reviewer fixture was not submitted")
        rid = action["request_id"]
        binding = host.bind(sid, rid, worker_id="fixture-reviewer", worker_task=action["claim"]["worker_task"], **OWNER)
        assert binding["action"] == "bound", reviewer_fixture_state(host, sid, rid, future)
        settled_review_tool(monkeypatch, bound, host, sid, rid, future, deadline,
            worker_id="fixture-reviewer", call_id="approve1", name="approve_review",
            arguments={"review": "fixture checked"})
        assert actions.decision.status == "done"
        if mutate:
            candidate.write_text("changed after approval")
        host.record(sid, rid, turn_request_id=rid, kind="completed", worker_id="fixture-reviewer",
                    worker_status="completed", text=f"ARGUS_DOTS_CALL:{rid}\nreview complete", **OWNER)
        result = future.result(timeout=1)
    assert (result.exit_code != 0) is mutate
    if mutate:
        assert "verification failed" in result.fatal_error
    else:
        assert bound.inspect(rid)["closed"] == {"status": "consumed"}


@pytest.mark.parametrize("kind", ["read_roots", "report_roots"])
def test_missing_approved_root_blocks_new_work_but_not_cancellation_or_receipt(tmp_path, kind):
    outside = tmp_path / "external"
    outside.mkdir()
    policy = replace(profile(), **{kind: (str(outside),)})
    bound, host, sid = setup(tmp_path, policy=policy)
    host.heartbeat(sid, **OWNER)
    original = request()
    bound.submit(original)
    action = host.next(sid, **OWNER)
    rid = action["request_id"]
    host.bind(sid, rid, worker_id="fixture-worker", worker_task=action["claim"]["worker_task"], **OWNER)
    outside.rmdir()
    assert host.status(sid)["tasks"][rid]["accepted"] == "fixture-worker"
    bound.submit(original)
    with pytest.raises((OSError, ValueError)):
        bound.submit(request(2))
    host.stop(sid, **OWNER)
    host.record(sid, rid, turn_request_id=rid, kind="cancelled", worker_id="fixture-worker",
                worker_status="interrupted", text="fixture native stop confirmed", **OWNER)
    assert bound.inspect(rid)["events"][-1]["type"] == "cancelled"
    bound.submit(original)


def test_final_budgeted_submission_keeps_its_exact_receipt(tmp_path):
    bound, host, sid = setup(tmp_path)
    host.heartbeat(sid, **OWNER)
    for number in range(1, 9):
        bound.submit(request(number))
    original = bound.inspect(f"{8:032x}")["request"]
    bound.submit(DotsRequest(**original))
    assert len(host.status(sid)["tasks"]) == 8
    with pytest.raises(DotsBridgeError, match="budget"):
        bound.assert_ready()


@pytest.mark.parametrize("replace_root", [False, True])
def test_cwdless_new_request_rejects_missing_or_replaced_project_but_old_receipt_remains(tmp_path, replace_root):
    from pathlib import Path

    bound, host, sid = setup(tmp_path)
    host.heartbeat(sid, **OWNER)
    original = request()
    bound.submit(original)
    project = Path(bound.project_root)
    project.rename(tmp_path / "old-project")
    if replace_root:
        project.mkdir()
    for operation in (bound.assert_ready, lambda: bound.submit(request(2))):
        with pytest.raises(DotsBridgeError, match="project"):
            operation()
    bound.submit(original)
    assert len(host.status(sid)["tasks"]) == 1
    host.stop(sid, **OWNER)


def test_declared_default_effort_preserves_original_none_and_actual_resume_config(tmp_path):
    assert profile().dispatch(None, None) == {}
    policy = SupervisedDotsProfile(model_efforts=(("", ("high", "xhigh")),),
        effort_overrides=(("low", "xhigh"),), default_effort_override="xhigh")
    assert policy.dispatch(None, None) == {"reasoning_effort": "xhigh"}
    assert SupervisedDotsProfile.from_dict(policy.to_dict()) == policy
    resolution = policy.resolution(None, None)
    assert resolution["requested_reasoning_effort"] is None
    assert resolution["observed_reasoning_effort"] is None
    assert resolution["effort_resolution"] == "explicit-profile-override"
    bound, host, sid = setup(tmp_path, policy=policy)
    host.heartbeat(sid, **OWNER)
    original = request(reasoning_effort=None)
    bound.submit(original)
    action = host.next(sid, **OWNER)
    rid = action["request_id"]
    assert action["request"]["reasoning_effort"] is None
    assert action["dispatch"] == {"reasoning_effort": "xhigh"}
    host.bind(sid, rid, worker_id="fixture-worker", worker_task=action["claim"]["worker_task"], **OWNER)
    host.record(sid, rid, turn_request_id=rid, kind="completed", worker_id="fixture-worker",
                worker_status="completed", text=f"ARGUS_DOTS_CALL:{rid}\nfixture", **OWNER)
    with pytest.raises(DotsBridgeError, match="followup cannot change"):
        bound.submit(request(2, resume_thread_id="fixture-worker", reasoning_effort="high"))
    bound.submit(request(2, resume_thread_id="fixture-worker", reasoning_effort="low"))
    followup = host.next(sid, **OWNER)
    assert followup["action"] == "followup"
    assert followup["request"]["reasoning_effort"] == "low"
    assert followup["dispatch"] == {"reasoning_effort": "xhigh"}
