"""Deterministic transport fixtures, not evidence of native execution/isolation."""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from argus.adapters.dots_admission import BoundedDotsRoleHost, BoundedRoleFileDotsTransport
from argus.adapters.dots_backend import DotsBackend, DotsBridgeError, DotsRequest
from argus.adapters.dots_coordinator import DotsCoordinator
from argus.adapters.dots_file_transport import FileDotsTransport
from argus.adapters.dots_role_host import DotsRoleHost, RoleFileDotsTransport
from argus.apps.dots_admission import main
from argus.core.models import RunnerOptions
from argus.core.run_gateway import run_exec

OWNER = {"coordinator_task": "/root/coordinate_fixture", "generation": 1}


def setup(tmp_path, **overrides):
    project = tmp_path / "project"
    project.mkdir()
    raw = BoundedRoleFileDotsTransport(tmp_path / "queue")
    host = BoundedDotsRoleHost(raw)
    options = {"parent_task": "/root", "coordinator_task": OWNER["coordinator_task"],
               "producer_id": "fixture-upstream", "project_root": project,
               "mission_id": "fixture-mission", "allowed_roles": ["manager", "engineer", "reviewer"],
               "max_requests": 8, "lifetime_seconds": 600}
    options.update(overrides)
    session = host.create(**options)
    bound = BoundedRoleFileDotsTransport(raw.root, session_id=session["session_id"],
                                         producer_id=session["producer_id"], project_root=project)
    return bound, host, session["session_id"]


def request(number=1, **changes):
    value = DotsRequest(f"{number:032x}", "fixture authorized task", "fixture", None, None,
                        time.time(), time.time() + 60, mission_id="fixture-mission")
    return replace(value, **changes)


def submit(host, queue, sid, req):
    return host.submit(sid, req, producer_id=queue.producer_id, project_root=queue.project_root)


def spawn(host, sid, worker="fixture-worker", owner=None):
    owner = owner or OWNER
    action = host.next(sid, **owner)
    host.bind(sid, action["request_id"], worker_id=worker,
              worker_task=action["claim"]["worker_task"], **owner)
    return action


def complete(host, sid, rid, *, worker="fixture-worker", owner=None, text="fixture output"):
    return host.record(sid, rid, turn_request_id=rid, kind="completed", worker_id=worker,
                       worker_status="completed", text=f"ARGUS_DOTS_CALL:{rid}\n{text}", **(owner or OWNER))


def test_empty_open_session_then_sequential_native_contract_without_root_reenrollment(tmp_path):
    q, host, sid = setup(tmp_path)
    assert host.next(sid, **OWNER)["action"] == "wait"
    first = request()
    q.submit(first)
    action = spawn(host, sid)
    complete(host, sid, first.request_id)
    q.finish(first.request_id)
    assert host.next(sid, **OWNER)["action"] == "wait"
    second = request(2, resume_thread_id="fixture-worker")
    q.submit(second)
    resumed = spawn(host, sid)
    assert resumed["action"] == "followup"
    assert resumed["claim"]["worker_task"] == action["claim"]["worker_task"]
    complete(host, sid, second.request_id)
    q.finish(second.request_id)
    q.close_producer()
    assert host.next(sid, **OWNER) == {"action": "done", "terminal_count": 2}


def test_original_gateway_publishes_dependent_requests_and_consumes_both(tmp_path):
    q, host, sid = setup(tmp_path)
    backend = DotsBackend(q, timeout_seconds=10, poll_interval=0.001)
    options = RunnerOptions(extension_env={"ARGUS_PLUGIN_PARENT_MISSION_ID": "fixture-mission"})

    def producer():
        first = run_exec(backend, prompt="first fixture", options=options, run_label="first")
        assert first.exit_code == 0
        second = run_exec(backend, prompt="second fixture", options=options, run_label="second",
                          resume_thread_id=first.thread_id)
        q.close_producer()
        return first, second

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(producer)
        observed = []
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            action = host.next(sid, **OWNER)
            if action["action"] in {"spawn", "followup"}:
                observed.append(action["action"])
                host.bind(sid, action["request_id"], worker_id="fixture-worker",
                          worker_task=action["claim"]["worker_task"], **OWNER)
                complete(host, sid, action["request_id"])
            elif action["action"] == "done":
                break
            else:
                assert action["action"] == "wait"
                time.sleep(0.001)
        else:
            pytest.fail("fixture host did not reach bounded producer completion")
        results = future.result(timeout=1)
    assert observed == ["spawn", "followup"]
    assert all(result.exit_code == 0 and result.thread_id == "fixture-worker" for result in results)
    assert all(task["closed"] == {"status": "consumed"} for task in host.status(sid)["tasks"].values())


def test_upstream_selects_roles_without_new_workflow_ordering(tmp_path):
    q, host, sid = setup(tmp_path, max_concurrency=2)
    q.submit(request(1, role="engineer"))
    q.submit(request(2, role="manager"))
    assert spawn(host, sid, "fixture-engineer")["request"]["role"] == "engineer"
    assert spawn(host, sid, "fixture-manager")["request"]["role"] == "manager"
    assert host.next(sid, **OWNER)["active_count"] == 2


def test_unlisted_queue_file_never_becomes_executable(tmp_path):
    q, host, sid = setup(tmp_path)
    outside = request(99)
    FileDotsTransport.submit(q, outside)
    assert host.next(sid, **OWNER)["action"] == "wait"
    q.close_producer()
    assert host.next(sid, **OWNER) == {"action": "done", "terminal_count": 0}
    assert FileDotsTransport(q.root).inspect(outside.request_id)["events"] == []
    assert not (q.root / outside.request_id / "host-claim.json").exists()


@pytest.mark.parametrize("change", [{"mission_id": "wrong"}, {"role": "planner"},
                                    {"options": {"disable_tools": True}}, {"model": "explicit"},
                                    {"reasoning_effort": "high"}, {"tools": [{"name": "bad"}]}])
def test_unsupported_or_out_of_scope_submission_never_publishes(tmp_path, change):
    q, host, sid = setup(tmp_path)
    req = request(**change)
    with pytest.raises(DotsBridgeError):
        q.submit(req)
    assert host.status(sid)["session"]["requests"] == {}
    assert not (q.root / req.request_id).exists()


@pytest.mark.parametrize("producer,project", [("wrong", None), (None, "/other-project")])
def test_wrong_producer_or_project_cannot_submit_or_close(tmp_path, producer, project):
    q, host, sid = setup(tmp_path)
    binding = {"producer_id": producer or q.producer_id, "project_root": project or q.project_root}
    with pytest.raises(DotsBridgeError, match="binding"):
        host.submit(sid, request(), **binding)
    with pytest.raises(DotsBridgeError, match="binding"):
        host.close_producer(sid, **binding)


def test_original_capability_gates_remain_pre_dispatch(tmp_path):
    q, host, sid = setup(tmp_path)
    assert q.capabilities == RoleFileDotsTransport.capabilities
    assert not q.capabilities.options
    backend = DotsBackend(q)
    result = run_exec(backend, prompt="production gate fixture", run_label="gate",
                      options=RunnerOptions(sandbox_mode="read-only", disable_tools=True))
    assert result.exit_code != 0 and result.stop_kind == "permanent_error"
    assert "required execution capabilities" in result.fatal_error
    assert host.status(sid)["session"]["requests"] == {}
    assert backend.capability_report()["automatic_dispatch"] is False


def test_v1_v2_cannot_adopt_bounded_session(tmp_path):
    q, _, sid = setup(tmp_path)
    for host in [DotsCoordinator(FileDotsTransport(q.root)), DotsRoleHost(RoleFileDotsTransport(q.root))]:
        with pytest.raises(DotsBridgeError, match="session"):
            host.next(sid, **OWNER)
    with pytest.raises(DotsBridgeError, match="protocol-3"):
        BoundedDotsRoleHost(RoleFileDotsTransport(q.root))


@pytest.mark.parametrize("host_type,transport_type", [(DotsCoordinator, FileDotsTransport),
                                                      (DotsRoleHost, RoleFileDotsTransport)])
def test_legacy_host_cannot_enroll_a_reserved_bounded_request(tmp_path, host_type, transport_type):
    q, host, sid = setup(tmp_path)
    req = request()
    q.submit(req)
    legacy = host_type(transport_type(q.root))
    with pytest.raises(DotsBridgeError, match="bounded producer"):
        legacy.create([req.request_id], parent_task="/root", coordinator_task="/root/legacy")
    assert host.next(sid, **OWNER)["action"] == "spawn"


@pytest.mark.parametrize("host_type,transport_type", [(DotsCoordinator, FileDotsTransport),
                                                      (DotsRoleHost, RoleFileDotsTransport)])
def test_legacy_host_checks_new_reservation_before_claiming(tmp_path, host_type, transport_type):
    q, _, _ = setup(tmp_path)
    req = request()
    raw = transport_type(q.root)
    raw.submit(req)
    legacy = host_type(raw)
    legacy_session = legacy.create([req.request_id], parent_task="/root", coordinator_task="/root/legacy")
    # A reservation appearing after old enrollment must still prevent dispatch.
    with q._root_fd() as root:
        q._write(root, "admission-" + req.request_id + ".json", {"fixture": "reserved"})
    with pytest.raises(DotsBridgeError, match="bounded producer"):
        legacy.next(legacy_session["session_id"], coordinator_task="/root/legacy", generation=1)
    assert not (q.root / req.request_id / "host-claim.json").exists()


def test_raw_emit_cannot_bypass_bounded_host_before_first_claim(tmp_path):
    q, _, _ = setup(tmp_path)
    req = request()
    q.submit(req)
    with pytest.raises(DotsBridgeError, match="raw emit"):
        FileDotsTransport(q.root).emit(req.request_id, "accepted", worker_id="fixture-forged")
    assert q.inspect(req.request_id)["events"] == []


def test_unbound_or_partial_binding_cannot_submit(tmp_path):
    q, _, sid = setup(tmp_path)
    with pytest.raises(DotsBridgeError, match="binding"):
        BoundedRoleFileDotsTransport(q.root).submit(request())
    with pytest.raises(DotsBridgeError, match="binding"):
        BoundedRoleFileDotsTransport(q.root, session_id=sid)


@pytest.mark.parametrize("change", [{"max_requests": 0}, {"max_requests": 65}, {"max_requests": True},
                                    {"lifetime_seconds": 3601}, {"lifetime_seconds": float("nan")},
                                    {"max_concurrency": 0}, {"max_concurrency": 6},
                                    {"allowed_roles": []}, {"allowed_roles": ["other"]},
                                    {"allowed_roles": ["manager", "manager"]}, {"allowed_roles": "manager"},
                                    {"mission_id": ""}, {"producer_id": ""},
                                    {"coordinator_task": "/root/nested/coordinator"}])
def test_invalid_contract_rejected(tmp_path, change):
    with pytest.raises(DotsBridgeError):
        setup(tmp_path, **change)


def test_repeated_submission_is_idempotent_even_after_closure(tmp_path):
    q, host, sid = setup(tmp_path)
    req = request()
    receipt = submit(host, q, sid, req)
    assert submit(host, q, sid, req) == receipt
    spawn(host, sid)
    complete(host, sid, req.request_id)
    q.finish(req.request_id)
    q.close_producer()
    assert submit(host, q, sid, req) == receipt
    assert len(q.inspect(req.request_id)["events"]) == 3
    with pytest.raises(DotsBridgeError, match="conflicting|reserved"):
        submit(host, q, sid, replace(req, prompt="different"))


def test_same_request_cannot_be_admitted_by_two_producer_sessions(tmp_path):
    q, host, sid = setup(tmp_path)
    second = host.create(parent_task="/root", coordinator_task="/root/other_coordinator",
                         producer_id="other-producer", project_root=tmp_path,
                         mission_id="fixture-mission", allowed_roles=["manager"], max_requests=2)
    req = request()
    q.submit(req)
    with pytest.raises(DotsBridgeError, match="reserved"):
        host.submit(second["session_id"], req, producer_id="other-producer", project_root=str(tmp_path))
    assert host.status(second["session_id"])["session"]["requests"] == {}
    assert host.next(sid, **OWNER)["action"] == "spawn"


def test_cross_session_collision_race_has_only_one_admission_owner(tmp_path):
    q, host, sid = setup(tmp_path)
    second = host.create(parent_task="/root", coordinator_task="/root/other_coordinator",
                         producer_id=q.producer_id, project_root=q.project_root,
                         mission_id="fixture-mission", allowed_roles=["manager"], max_requests=2)
    req = request()

    def attempt(session_id):
        try:
            return submit(host, q, session_id, req)
        except DotsBridgeError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [sid, second["session_id"]]))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert any(isinstance(result, str) and "reserved" in result for result in results)
    assert sum(len(host.status(identity)["session"]["requests"]) for identity in [sid, second["session_id"]]) == 1


@pytest.mark.parametrize("after_write", [False, True])
def test_reservation_write_failure_allows_only_exact_safe_retry(tmp_path, monkeypatch, after_write):
    q, host, sid = setup(tmp_path)
    req = request()
    original = q._write

    def crash(fd, name, value, **kwargs):
        if name == host._admission_name(req.request_id):
            if after_write:
                original(fd, name, value, **kwargs)
            raise OSError("fixture reservation write response lost")
        return original(fd, name, value, **kwargs)

    monkeypatch.setattr(q, "_write", crash)
    with pytest.raises(OSError):
        q.submit(req)
    assert not (q.root / req.request_id).exists()
    monkeypatch.setattr(q, "_write", original)
    q.submit(req)
    assert host.next(sid, **OWNER)["action"] == "spawn"


def test_lost_enrollment_cannot_be_recovered_by_another_session(tmp_path, monkeypatch):
    q, host, sid = setup(tmp_path)
    second = host.create(parent_task="/root", coordinator_task="/root/other_coordinator",
                         producer_id=q.producer_id, project_root=q.project_root,
                         mission_id="fixture-mission", allowed_roles=["manager"], max_requests=2)
    req = request()
    original = q._write

    def crash(fd, name, value, **kwargs):
        if name == host._name(sid):
            raise OSError("fixture uncommitted enrollment")
        return original(fd, name, value, **kwargs)

    monkeypatch.setattr(q, "_write", crash)
    with pytest.raises(OSError):
        q.submit(req)
    monkeypatch.setattr(q, "_write", original)
    with pytest.raises(DotsBridgeError, match="reserved"):
        submit(host, q, second["session_id"], req)
    q.submit(req)
    assert host.next(sid, **OWNER)["action"] == "spawn"


def test_unowned_identical_request_cannot_be_adopted(tmp_path):
    q, host, sid = setup(tmp_path)
    req = request()
    FileDotsTransport.submit(q, req)
    with pytest.raises(DotsBridgeError, match="unowned"):
        q.submit(req)
    assert host.status(sid)["session"]["requests"] == {}


def test_admission_owner_checked_again_before_dispatch(tmp_path):
    q, host, sid = setup(tmp_path)
    req = request()
    q.submit(req)
    path = q.root / host._admission_name(req.request_id)
    changed = json.loads(path.read_text())
    changed["session_id"] = "f" * 32
    path.write_text(json.dumps(changed))
    with pytest.raises(DotsBridgeError, match="admission owner"):
        host.next(sid, **OWNER)


def test_deadline_rechecked_after_publication_before_enrollment(tmp_path, monkeypatch):
    q, host, sid = setup(tmp_path)
    req = request()
    original = q._write
    deadline = host.status(sid)["session"]["expires_at"]

    def expire(fd, name, value, **kwargs):
        result = original(fd, name, value, **kwargs)
        if name == "head.json":
            monkeypatch.setattr(time, "time", lambda: deadline + 1)
        return result

    monkeypatch.setattr(q, "_write", expire)
    with pytest.raises(DotsBridgeError, match="expired during"):
        q.submit(req)
    assert host.status(sid)["session"]["requests"] == {}
    assert host.next(sid, **OWNER)["action"] == "done"


def test_concurrent_exact_retries_and_maximum_are_atomic(tmp_path):
    q, host, sid = setup(tmp_path, max_requests=1)
    req = request()
    with ThreadPoolExecutor(max_workers=6) as pool:
        receipts = list(pool.map(lambda _: submit(host, q, sid, req), range(6)))
    assert all(receipt == receipts[0] for receipt in receipts)
    assert len(host.status(sid)["session"]["requests"]) == 1
    with pytest.raises(DotsBridgeError, match="limit"):
        q.submit(request(2))
    assert not (q.root / f"{2:032x}").exists()


def test_concurrent_claims_and_slot_limit_never_duplicate_dispatch(tmp_path):
    q, host, sid = setup(tmp_path)
    q.submit(request(1))
    q.submit(request(2))
    with ThreadPoolExecutor(max_workers=6) as pool:
        actions = list(pool.map(lambda _: host.next(sid, **OWNER), range(6)))
    assert [action["action"] for action in actions].count("spawn") == 1
    first = next(action for action in actions if action["action"] == "spawn")
    host.bind(sid, first["request_id"], worker_id="fixture-worker",
              worker_task=first["claim"]["worker_task"], **OWNER)
    assert host.next(sid, **OWNER)["active_count"] == 1
    complete(host, sid, first["request_id"])
    assert host.next(sid, **OWNER)["action"] == "spawn"


def test_close_drains_existing_work_and_rejects_future_work(tmp_path):
    q, host, sid = setup(tmp_path)
    req = request()
    q.submit(req)
    q.close_producer()
    q.close_producer()
    with pytest.raises(DotsBridgeError, match="closed"):
        q.submit(request(2))
    assert spawn(host, sid)["action"] == "spawn"
    complete(host, sid, req.request_id)
    assert host.next(sid, **OWNER)["action"] == "done"


def test_stop_cancels_pending_and_requires_observed_stop_for_active(tmp_path):
    q, host, sid = setup(tmp_path)
    q.submit(request(1))
    q.submit(request(2))
    first = spawn(host, sid)
    host.stop(sid, **OWNER)
    with pytest.raises(DotsBridgeError, match="closed"):
        q.submit(request(3))
    assert host.next(sid, **OWNER)["action"] == "cancel"
    with pytest.raises(DotsBridgeError, match="late"):
        complete(host, sid, first["request_id"])
    host.record(sid, first["request_id"], turn_request_id=first["request_id"], kind="cancelled",
                worker_id="fixture-worker", worker_status="interrupted", text="fixture stopped", **OWNER)
    assert host.next(sid, **OWNER) == {"action": "done", "terminal_count": 2}
    assert q.inspect(f"{2:032x}")["events"][-1]["type"] == "cancelled"
    assert not (q.root / f"{2:032x}" / "host-claim.json").exists()


def test_session_deadline_never_extended_by_new_submission(tmp_path, monkeypatch):
    q, host, sid = setup(tmp_path, lifetime_seconds=30)
    q.submit(request())
    action = spawn(host, sid)
    deadline = host.status(sid)["session"]["expires_at"]
    monkeypatch.setattr(time, "time", lambda: deadline + 1)
    with pytest.raises(DotsBridgeError, match="expired"):
        q.submit(request(2))
    assert host.next(sid, **OWNER)["action"] == "cancel"
    assert host.status(sid)["session"]["expires_at"] == deadline
    with pytest.raises(DotsBridgeError, match="late"):
        complete(host, sid, action["request_id"])


def test_empty_open_session_expires_without_a_worker(tmp_path, monkeypatch):
    _, host, sid = setup(tmp_path)
    deadline = host.status(sid)["session"]["expires_at"]
    monkeypatch.setattr(time, "time", lambda: deadline + 1)
    assert host.next(sid, **OWNER) == {"action": "done", "terminal_count": 0}


@pytest.mark.parametrize("offset", [-1000, 1000])
def test_request_creation_time_must_belong_to_session(tmp_path, offset):
    q, _, _ = setup(tmp_path)
    req = request(created_at=time.time() + offset, expires_at=time.time() + 2000)
    with pytest.raises(DotsBridgeError, match="created"):
        q.submit(req)


def test_recreated_host_preserves_unknown_spawn_and_handoff_generation(tmp_path):
    q, host, sid = setup(tmp_path)
    q.submit(request())
    action = host.next(sid, **OWNER)
    recovered = BoundedDotsRoleHost(BoundedRoleFileDotsTransport(q.root))
    assert recovered.next(sid, **OWNER)["action"] == "reconcile"
    old_deadline = recovered.status(sid)["session"]["expires_at"]
    recovered.handoff(sid, parent_task="/root", previous_generation=1, coordinator_task="/root/replacement")
    new = {"coordinator_task": "/root/replacement", "generation": 2}
    with pytest.raises(DotsBridgeError, match="stale"):
        recovered.next(sid, **OWNER)
    pending = recovered.next(sid, **new)
    assert pending["action"] == "reconcile" and pending["claim"]["worker_task"] == action["claim"]["worker_task"]
    recovered.bind(sid, action["request_id"], worker_id="fixture-worker", worker_task=action["claim"]["worker_task"], **new)
    complete(recovered, sid, action["request_id"], owner=new)
    q.submit(request(2))
    assert recovered.next(sid, **new)["claim"]["worker_task"].startswith("/root/replacement/")
    assert recovered.status(sid)["session"]["expires_at"] == old_deadline


@pytest.mark.parametrize("after_write", [False, True])
def test_submission_enrollment_crash_exact_retry_is_safe(tmp_path, monkeypatch, after_write):
    q, host, sid = setup(tmp_path)
    req = request()
    original = q._write
    failed = False

    def crash(fd, name, value, **kwargs):
        nonlocal failed
        if name == host._name(sid) and not failed:
            failed = True
            if after_write:
                original(fd, name, value, **kwargs)
            raise OSError("fixture enrollment response lost")
        return original(fd, name, value, **kwargs)

    monkeypatch.setattr(q, "_write", crash)
    with pytest.raises(OSError):
        q.submit(req)
    monkeypatch.setattr(q, "_write", original)
    assert not (q.root / req.request_id / "host-claim.json").exists()
    q.submit(req)
    assert list(host.status(sid)["session"]["requests"]) == [req.request_id]
    assert host.next(sid, **OWNER)["action"] == "spawn"
    assert host.next(sid, **OWNER)["action"] == "reconcile"


def test_partial_publication_with_intact_identity_recovers_without_rewrite(tmp_path, monkeypatch):
    q, host, sid = setup(tmp_path)
    req = request()
    original = q._write

    def crash(fd, name, value, **kwargs):
        if name == "head.json":
            raise OSError("fixture head failure")
        return original(fd, name, value, **kwargs)

    monkeypatch.setattr(q, "_write", crash)
    with pytest.raises(OSError):
        q.submit(req)
    before = (q.root / req.request_id / "request.json").read_bytes()
    monkeypatch.setattr(q, "_write", original)
    q.submit(req)
    assert (q.root / req.request_id / "request.json").read_bytes() == before
    assert host.next(sid, **OWNER)["action"] == "spawn"


def test_missing_partial_identity_is_not_automatically_recreated(tmp_path):
    q, host, sid = setup(tmp_path)
    req = request()
    (q.root / req.request_id).mkdir(mode=0o700)
    with pytest.raises(DotsBridgeError):
        q.submit(req)
    assert host.status(sid)["session"]["requests"] == {}


@pytest.mark.parametrize("handled", ["changed", "closed", "accepted"])
def test_existing_conflicting_or_handled_request_not_adopted(tmp_path, handled):
    q, host, sid = setup(tmp_path)
    req = request()
    FileDotsTransport.submit(q, replace(req, prompt="different") if handled == "changed" else req)
    if handled == "closed":
        FileDotsTransport(q.root).cancel(req.request_id, "fixture close")
    elif handled == "accepted":
        FileDotsTransport(q.root).emit(req.request_id, "accepted", worker_id="fixture-external")
    with pytest.raises(DotsBridgeError):
        q.submit(req)
    assert host.status(sid)["session"]["requests"] == {}


def test_enrolled_bytes_are_frozen(tmp_path):
    q, host, sid = setup(tmp_path)
    req = request()
    q.submit(req)
    path = q.root / req.request_id / "request.json"
    changed = json.loads(path.read_text())
    changed["prompt"] = "unauthorized change"
    path.write_text(json.dumps(changed))
    with pytest.raises(DotsBridgeError, match="changed"):
        host.next(sid, **OWNER)
    with pytest.raises(DotsBridgeError, match="changed"):
        q.submit(req)


@pytest.mark.parametrize("state", ["unknown", "active", "failed", "wrong-role"])
def test_resume_requires_this_scope_and_completed_prior_turn(tmp_path, state):
    q, host, sid = setup(tmp_path)
    first = request()
    q.submit(first)
    spawn(host, sid)
    if state in {"unknown", "wrong-role"}:
        complete(host, sid, first.request_id)
    elif state == "failed":
        host.record(sid, first.request_id, turn_request_id=first.request_id, kind="failed",
                    worker_id="fixture-worker", worker_status="failed", text="fixture failure", **OWNER)
    second = request(2, resume_thread_id="missing" if state == "unknown" else "fixture-worker",
                     role="engineer" if state == "wrong-role" else "manager")
    with pytest.raises(DotsBridgeError):
        q.submit(second)
    assert second.request_id not in host.status(sid)["session"]["requests"]


def test_two_pending_continuations_cannot_own_same_worker(tmp_path):
    q, host, sid = setup(tmp_path)
    first = request()
    q.submit(first)
    spawn(host, sid)
    complete(host, sid, first.request_id)
    q.submit(request(2, resume_thread_id="fixture-worker"))
    with pytest.raises(DotsBridgeError, match="active turn"):
        q.submit(request(3, resume_thread_id="fixture-worker"))


def test_typed_tool_channel_uses_protocol3_host_and_preserves_dispatch_contract(tmp_path):
    q, host, sid = setup(tmp_path)
    tools = [{"name": "approve_review", "inputSchema": {"type": "object"}}]
    req = request(role="reviewer", tools=tools)
    q.submit(req)
    spawn(host, sid)
    kwargs = {"worker_id": "fixture-worker", "call_id": "fixture-call", "name": "approve_review",
              "arguments": {"review": "fixture"}, **OWNER}
    pending = host.request_tool(sid, req.request_id, **kwargs)
    assert pending["status"] == "pending"
    assert q.claim_tool_call(req.request_id, pending["sequence"]) is None
    with pytest.raises(DotsBridgeError, match="pending|settlement"):
        complete(host, sid, req.request_id)
    q.tool_result(req.request_id, pending["sequence"], {"recorded": "approve_review"})
    ready = host.request_tool(sid, req.request_id, **kwargs)
    assert ready["status"] == "ready"
    assert q.claim_tool_call(req.request_id, pending["sequence"]) == {"recorded": "approve_review"}
    complete(host, sid, req.request_id)


@pytest.mark.parametrize("operation", ["poll", "cancel", "poll_cancellation", "finish", "inspect",
                                       "claim_tool_call", "tool_result", "emit"])
def test_bound_producer_cannot_operate_on_another_sessions_request(tmp_path, operation):
    q, host, _ = setup(tmp_path)
    other = host.create(parent_task="/root", coordinator_task="/root/other_coordinator",
                        producer_id="different-producer", project_root=tmp_path,
                        mission_id="different-mission", allowed_roles=["manager"], max_requests=2)
    qb = BoundedRoleFileDotsTransport(q.root, session_id=other["session_id"],
                                      producer_id="different-producer", project_root=tmp_path)
    req = request(mission_id="different-mission")
    qb.submit(req)
    args = {"poll": (1,), "cancel": ("foreign cancellation",), "poll_cancellation": (),
            "finish": (), "inspect": (), "claim_tool_call": (1,), "tool_result": (1, {}),
            "emit": ("cancelled",)}[operation]
    with pytest.raises(DotsBridgeError, match="bound producer|cannot emit"):
        getattr(q, operation)(req.request_id, *args)
    assert qb.inspect(req.request_id)["closed"] is None
    assert qb.inspect(req.request_id)["events"] == []
    assert BoundedRoleFileDotsTransport(q.root).inspect(req.request_id)["request"]["mission_id"] == "different-mission"


def test_owned_orphan_can_be_cancelled_after_failed_enrollment(tmp_path, monkeypatch):
    q, host, sid = setup(tmp_path)
    req = request()
    original = q._write

    def crash(fd, name, value, **kwargs):
        if name == host._name(sid):
            raise OSError("fixture enrollment failure")
        return original(fd, name, value, **kwargs)

    monkeypatch.setattr(q, "_write", crash)
    with pytest.raises(OSError):
        q.submit(req)
    monkeypatch.setattr(q, "_write", original)
    q.cancel(req.request_id, "gateway submission failure")
    assert q.inspect(req.request_id)["closed"]["status"] == "cancel_requested"
    with pytest.raises(DotsBridgeError, match="handled"):
        q.submit(req)
    assert host.status(sid)["session"]["requests"] == {}


@pytest.mark.parametrize("field,value", [("producer_closed", 1), ("max_requests", False),
                                         ("allowed_roles", ["unknown"]), ("project_root", "relative")])
def test_malformed_persisted_contract_fails_closed(tmp_path, field, value):
    q, host, sid = setup(tmp_path)
    path = q.root / host._name(sid)
    state = json.loads(path.read_text())
    state[field] = value
    path.write_text(json.dumps(state))
    with pytest.raises(DotsBridgeError):
        host.next(sid, **OWNER)


def test_host_cli_can_create_wait_close_and_finish_without_dispatch(tmp_path, capsys):
    project = tmp_path / "project"
    project.mkdir()
    base = ["--bridge-dir", str(tmp_path / "queue")]
    assert main(base + ["create", "--parent", "/root", "--coordinator", OWNER["coordinator_task"],
                       "--producer", "fixture", "--project-root", str(project), "--mission", "fixture-mission",
                       "--roles", "manager", "--max-requests", "2"]) == 0
    sid = json.loads(capsys.readouterr().out)["session_id"]
    owner = ["--owner", OWNER["coordinator_task"], "--generation", "1"]
    assert main(base + ["next", sid] + owner) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "wait"
    assert main(base + ["close-producer", sid, "--producer", "fixture", "--project-root", str(project)]) == 0
    capsys.readouterr()
    assert main(base + ["next", sid] + owner) == 0
    assert json.loads(capsys.readouterr().out) == {"action": "done", "terminal_count": 0}
