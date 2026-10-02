"""Fixture protocol tests. Separate real native runs establish host capability."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from argus.adapters.dots_backend import DotsBackend, DotsBridgeError, DotsRequest
from argus.adapters.dots_coordinator import DotsCoordinator
from argus.adapters.dots_file_transport import FileDotsTransport
from argus.adapters.dots_role_host import DotsRoleHost, RoleFileDotsTransport
from argus.core.models import RunnerOptions
from argus.core.run_gateway import run_exec
from argus.reviewer.tools import ReviewActions

OWNER = {"coordinator_task": "/root/coordinate_roles", "generation": 1}


def setup(tmp_path):
    queue = RoleFileDotsTransport(tmp_path / "roles")
    return queue, DotsRoleHost(queue)


def submit(queue, host, number=1, **kwargs):
    req = DotsRequest(f"{number:032x}", "fixture task", "role test", None, None,
                      time.time(), time.time() + 60, mission_id="fixture-mission", **kwargs)
    queue.submit(req)
    session = host.create([req.request_id], parent_task="/root", coordinator_task=OWNER["coordinator_task"])
    return req.request_id, session["session_id"]


def spawn(host, sid, worker_id="fixture-worker"):
    action = host.next(sid, **OWNER)
    host.bind(sid, action["request_id"], worker_id=worker_id, worker_task=action["claim"]["worker_task"], **OWNER)
    return action


def complete(host, sid, rid, text="fixture result", worker_id="fixture-worker"):
    return host.record(sid, rid, kind="completed", turn_request_id=rid, worker_id=worker_id,
                       worker_status="completed", text=f"ARGUS_DOTS_CALL:{rid}\n{text}", **OWNER)


def request_tool(host, sid, rid, name="approve_review", arguments=None, call_id="call1"):
    return host.request_tool(sid, rid, worker_id="fixture-worker", call_id=call_id, name=name,
                             arguments=arguments if arguments is not None else {"review": "fixture evidence"}, **OWNER)


def reviewer(queue, host):
    actions = ReviewActions()
    rid, sid = submit(queue, host, role="reviewer", tools=actions.tools)
    spawn(host, sid)
    return rid, sid, actions


def test_v1_remains_conservative_and_cannot_adopt_v2(tmp_path):
    q, host = setup(tmp_path)
    rid, sid = submit(q, host)
    assert not FileDotsTransport.capabilities.resume and not FileDotsTransport.capabilities.role_tools
    assert RoleFileDotsTransport.capabilities.resume and RoleFileDotsTransport.capabilities.role_tools
    assert not RoleFileDotsTransport.capabilities.options
    with pytest.raises(DotsBridgeError, match="session"):
        DotsCoordinator(FileDotsTransport(q.root)).next(sid, **OWNER)


def test_same_role_followup_keeps_actual_worker_and_strips_envelope(tmp_path):
    q, host = setup(tmp_path)
    first, sid1 = submit(q, host)
    old = spawn(host, sid1)
    complete(host, sid1, first, text="first")
    q.finish(first)
    second, sid2 = submit(q, host, 2, resume_thread_id="fixture-worker")
    action = host.next(sid2, **OWNER)
    assert action["action"] == "followup"
    assert action["claim"]["worker_task"] == old["claim"]["worker_task"]
    assert host.next(sid2, **OWNER)["action"] == "reconcile"
    host.bind(sid2, second, worker_id="fixture-worker", worker_task=action["claim"]["worker_task"], **OWNER)
    complete(host, sid2, second, text="second")
    events = q.inspect(second)["events"]
    assert events[0]["worker_id"] == "fixture-worker" and events[1]["text"] == "second"


def test_stale_turn_output_and_plain_approval_cannot_complete(tmp_path):
    q, host = setup(tmp_path)
    rid, sid = submit(q, host)
    spawn(host, sid)
    for turn_id, text in [("f" * 32, "old"), (rid, "approve_review"), (rid, 'ARGUS_DOTS_CALL:' + "f" * 32 + '\nold')]:
        with pytest.raises(DotsBridgeError, match="turn|envelope"):
            host.record(sid, rid, turn_request_id=turn_id, kind="completed", worker_id="fixture-worker", worker_status="completed", text=text, **OWNER)
    complete(host, sid, rid, text='{"approve_review":true}')
    assert q.inspect(rid)["events"][1]["text"] == '{"approve_review":true}'


@pytest.mark.parametrize("scope", ["role", "mission", "unknown", "active"])
def test_invalid_session_resume_fails_closed(tmp_path, scope):
    q, host = setup(tmp_path)
    first, sid1 = submit(q, host)
    spawn(host, sid1)
    if scope != "active":
        complete(host, sid1, first)
    kwargs = {"resume_thread_id": "missing" if scope == "unknown" else "fixture-worker"}
    if scope == "role":
        kwargs["role"] = "engineer"
    second = DotsRequest("2" * 32, "second", "test", None, None, time.time(), time.time() + 50,
                         mission_id="other-mission" if scope == "mission" else "fixture-mission", **kwargs)
    q.submit(second)
    sid = host.create([second.request_id], parent_task="/root", coordinator_task=OWNER["coordinator_task"])["session_id"]
    with pytest.raises(DotsBridgeError, match="mapping|mismatch|prior turn"):
        host.next(sid, **OWNER)
    assert q.inspect(second.request_id)["events"] == []


def test_global_single_active_turn_across_batches(tmp_path):
    q, host = setup(tmp_path)
    first, sid = submit(q, host)
    spawn(host, sid)
    complete(host, sid, first)
    second, sid2 = submit(q, host, 2, resume_thread_id="fixture-worker")
    third, sid3 = submit(q, host, 3, resume_thread_id="fixture-worker")
    action = host.next(sid2, **OWNER)
    with pytest.raises(DotsBridgeError, match="prior turn"):
        host.next(sid3, **OWNER)
    with pytest.raises(DotsBridgeError, match="existing actual"):
        host.bind(sid2, second, worker_id="wrong", worker_task=action["claim"]["worker_task"], **OWNER)
    host.bind(sid2, second, worker_id="fixture-worker", worker_task=action["claim"]["worker_task"], **OWNER)
    complete(host, sid2, second)
    assert host.next(sid3, **OWNER)["action"] == "followup"


def test_fresh_request_cannot_reuse_existing_worker_without_resume(tmp_path):
    q, host = setup(tmp_path)
    first, sid = submit(q, host)
    spawn(host, sid)
    complete(host, sid, first)
    second, sid2 = submit(q, host, 2)
    action = host.next(sid2, **OWNER)
    with pytest.raises(DotsBridgeError, match="explicit same-role"):
        host.bind(sid2, second, worker_id="fixture-worker", worker_task=action["claim"]["worker_task"], **OWNER)
    assert host.status(sid2)["tasks"][second]["claim"]["worker_id"] is None


def test_followup_intent_crash_cannot_repeat_admission(tmp_path, monkeypatch):
    q, host = setup(tmp_path)
    first, sid = submit(q, host)
    spawn(host, sid)
    complete(host, sid, first)
    second, sid2 = submit(q, host, 2, resume_thread_id="fixture-worker")
    original = q._write

    def crash(fd, name, value, **kwargs):
        if name == "host-claim.json":
            raise OSError("fixture claim publication failure")
        return original(fd, name, value, **kwargs)

    monkeypatch.setattr(q, "_write", crash)
    with pytest.raises(OSError):
        host.next(sid2, **OWNER)
    monkeypatch.setattr(q, "_write", original)
    with pytest.raises(DotsBridgeError, match="uncertain"):
        host.next(sid2, **OWNER)


def test_real_typed_event_schema_feedback_and_idempotent_result(tmp_path):
    q, host = setup(tmp_path)
    rid, sid, actions = reviewer(q, host)
    issued = request_tool(host, sid, rid)
    seq = issued["sequence"]
    assert issued["status"] == "pending" and actions.decision is None
    assert q.claim_tool_call(rid, seq) is None
    reply = actions.dispatch("approve_review", {"review": "fixture evidence"})
    q.tool_result(rid, seq, reply)
    assert request_tool(host, sid, rid)["result"] == reply
    assert q.claim_tool_call(rid, seq) == reply
    q.tool_result(rid, seq, reply)
    complete(host, sid, rid)
    assert actions.decision.status == "done"


def test_pending_or_unknown_tool_dispatch_cannot_complete_or_repeat(tmp_path):
    q, host = setup(tmp_path)
    rid, sid, actions = reviewer(q, host)
    issued = request_tool(host, sid, rid)
    with pytest.raises(DotsBridgeError, match="pending"):
        complete(host, sid, rid)
    assert q.claim_tool_call(rid, issued["sequence"]) is None
    with pytest.raises(DotsBridgeError, match="uncertain"):
        q.claim_tool_call(rid, issued["sequence"])
    with pytest.raises(DotsBridgeError, match="pending"):
        request_tool(host, sid, rid, call_id="second")
    assert actions.decision is None


def test_call_id_cannot_change_arguments_and_duplicate_is_one_event(tmp_path):
    q, host = setup(tmp_path)
    rid, sid, _ = reviewer(q, host)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: request_tool(host, sid, rid), range(8)))
    assert {result["sequence"] for result in results} == {2}
    assert len(q.inspect(rid)["events"]) == 2
    with pytest.raises(DotsBridgeError, match="different arguments"):
        request_tool(host, sid, rid, arguments={"review": "different"})


def test_tool_publication_recovers_without_duplicate_dispatch(tmp_path, monkeypatch):
    q, host = setup(tmp_path)
    rid, sid, _ = reviewer(q, host)
    original = q._emit_locked
    monkeypatch.setattr(q, "_emit_locked", lambda *a, **kw: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError):
        request_tool(host, sid, rid)
    monkeypatch.setattr(q, "_emit_locked", original)
    with pytest.raises(DotsBridgeError, match="pending"):
        request_tool(host, sid, rid, call_id="different")
    assert request_tool(host, sid, rid)["sequence"] == 2
    assert len(q.inspect(rid)["events"]) == 2


def test_tools_reject_wrong_role_identity_unbound_name_and_after_cancel(tmp_path):
    q, host = setup(tmp_path)
    rid, sid, _ = reviewer(q, host)
    with pytest.raises(DotsBridgeError, match="bound"):
        host.request_tool(sid, rid, worker_id="wrong", call_id="x", name="approve_review", arguments={}, **OWNER)
    with pytest.raises(DotsBridgeError, match="not bound"):
        request_tool(host, sid, rid, name="not-a-tool")
    issued = request_tool(host, sid, rid)
    q.claim_tool_call(rid, issued["sequence"])
    q.cancel(rid, "stop")
    with pytest.raises(DotsBridgeError, match="active bound"):
        request_tool(host, sid, rid)
    q.tool_result(rid, issued["sequence"], {"status": "done"})
    with pytest.raises(DotsBridgeError, match="closed"):
        q.claim_tool_call(rid, issued["sequence"])


def test_native_options_still_fail_before_publication(tmp_path):
    q, _ = setup(tmp_path)
    for options in (RunnerOptions(sandbox_mode="read-only"), RunnerOptions(disable_tools=True), RunnerOptions(force_safe_mode=True)):
        result = run_exec(DotsBackend(q, role="reviewer"), prompt="fixture", run_label="blocked", options=options)
        assert result.exit_code and "capabilities" in result.fatal_error
    assert not list(q.root.glob("*/request.json"))


def test_gateway_dispatches_actual_typed_events_and_feedback(tmp_path):
    q, host = setup(tmp_path)
    actions = ReviewActions()
    backend = DotsBackend(q, role="reviewer", timeout_seconds=5, poll_interval=0.002)

    def producer():
        with backend.bind_role_tools(actions.tools, actions.dispatch):
            return run_exec(backend, prompt="fixture review", run_label="probe", options=RunnerOptions())

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(producer)
        deadline = time.monotonic() + 2
        while not list(q.root.glob("*/request.json")) and time.monotonic() < deadline:
            time.sleep(0.002)
        rid = next(q.root.glob("*/request.json")).parent.name
        sid = host.create([rid], parent_task="/root", coordinator_task=OWNER["coordinator_task"])["session_id"]
        spawn(host, sid)
        bad = request_tool(host, sid, rid, arguments={"review": ""}, call_id="bad")
        while bad["status"] != "ready":
            time.sleep(0.002)
            bad = request_tool(host, sid, rid, arguments={"review": ""}, call_id="bad")
        assert bad["result"]["status"] == "failed" and actions.decision is None
        good = request_tool(host, sid, rid, call_id="good")
        while good["status"] != "ready":
            time.sleep(0.002)
            good = request_tool(host, sid, rid, call_id="good")
        complete(host, sid, rid, text="actual fixture review")
        result = future.result(2)
    assert result.exit_code == 0 and result.last_agent_message == "actual fixture review"
    assert result.tool_activity_observed
    assert actions.decision.status == "done"


def test_stop_blocks_queued_callback_and_inflight_requires_settlement(tmp_path):
    q, host = setup(tmp_path)
    rid, sid, _ = reviewer(q, host)
    issued = request_tool(host, sid, rid)
    host.stop(sid, **OWNER)
    with pytest.raises(DotsBridgeError, match="closed"):
        q.claim_tool_call(rid, issued["sequence"])
    host.record(sid, rid, kind="cancelled", turn_request_id=rid, worker_id="fixture-worker",
                worker_status="interrupted", text="fixture stopped before dispatch", **OWNER)


def test_inflight_callback_blocks_cancel_ack_until_settlement(tmp_path):
    q, host = setup(tmp_path)
    rid, sid, _ = reviewer(q, host)
    issued = request_tool(host, sid, rid)
    q.claim_tool_call(rid, issued["sequence"])
    host.stop(sid, **OWNER)
    with pytest.raises(DotsBridgeError, match="settlement"):
        host.record(sid, rid, kind="cancelled", turn_request_id=rid, worker_id="fixture-worker",
                    worker_status="interrupted", text="fixture worker stopped", **OWNER)
    q.tool_result(rid, issued["sequence"], {"status": "completed before cancellation settled"})
    host.record(sid, rid, kind="cancelled", turn_request_id=rid, worker_id="fixture-worker",
                worker_status="interrupted", text="fixture worker and tool stopped", **OWNER)


def test_session_deadline_blocks_pending_tool_dispatch(tmp_path, monkeypatch):
    q, host = setup(tmp_path)
    rid, sid, _ = reviewer(q, host)
    issued = request_tool(host, sid, rid)
    session = host.status(sid)["session"]
    # The session is shorter than the request without mutating enrolled data.
    with q._root_fd() as root, q._locked(root):
        session["expires_at"] = session["created_at"] + 0.01
        q._write(root, host._name(sid), session, replace=True)
    monkeypatch.setattr(time, "time", lambda: session["expires_at"] + 0.01)
    with pytest.raises(DotsBridgeError, match="expired"):
        q.claim_tool_call(rid, issued["sequence"])


def test_registry_publication_failure_keeps_exact_durable_worker_identity(tmp_path, monkeypatch):
    q, host = setup(tmp_path)
    rid, sid = submit(q, host)
    action = host.next(sid, **OWNER)
    original = q._write

    def crash(fd, name, value, **kwargs):
        if name.startswith("role-worker-"):
            raise OSError("fixture registry crash")
        return original(fd, name, value, **kwargs)

    monkeypatch.setattr(q, "_write", crash)
    with pytest.raises(OSError):
        host.bind(sid, rid, worker_id="fixture-worker", worker_task=action["claim"]["worker_task"], **OWNER)
    monkeypatch.setattr(q, "_write", original)
    with pytest.raises(DotsBridgeError, match="another worker"):
        host.bind(sid, rid, worker_id="different-worker", worker_task=action["claim"]["worker_task"], **OWNER)
    host.bind(sid, rid, worker_id="fixture-worker", worker_task=action["claim"]["worker_task"], **OWNER)
    assert host.status(sid)["tasks"][rid]["accepted"] == "fixture-worker"


def test_pending_native_result_recovery_preserves_turn_envelope(tmp_path, monkeypatch):
    q, host = setup(tmp_path)
    rid, sid = submit(q, host)
    spawn(host, sid)
    original = q._emit_locked
    monkeypatch.setattr(q, "_emit_locked", lambda *a, **kw: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError):
        complete(host, sid, rid, text="already observed result")
    monkeypatch.setattr(q, "_emit_locked", original)
    recovery = host.next(sid, **OWNER)
    assert recovery["action"] == "recover_result" and recovery["turn_request_id"] == rid
    host.record(sid, rid, turn_request_id=rid, **recovery["result"], **OWNER)
    assert q.inspect(rid)["events"][1]["text"] == "already observed result"
