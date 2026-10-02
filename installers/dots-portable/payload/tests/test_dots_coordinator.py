"""Deterministic journal tests; fake worker IDs are fixtures, not native execution."""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from argus.adapters.dots_backend import DotsBridgeError, DotsRequest
from argus.adapters.dots_coordinator import DotsCoordinator
from argus.adapters.dots_file_transport import FileDotsTransport
from argus.apps.dots_coordinator import main

OWNER = {"coordinator_task": "/root/coordinate_tests", "generation": 1}


def setup(tmp_path, count=1, **kwargs):
    queue = FileDotsTransport(tmp_path / "queue")
    ids = [f"{i + 1:032x}" for i in range(count)]
    for identity in ids:
        queue.submit(DotsRequest(identity, f"authorized fixture {identity}", "test", None, None,
                                 time.time(), time.time() + 120))
    host = DotsCoordinator(queue)
    session = host.create(ids, parent_task="/root", coordinator_task=OWNER["coordinator_task"], **kwargs)
    return queue, host, session["session_id"], ids


def bind(host, session_id, action, **owner):
    owner = owner or OWNER
    return host.bind(session_id, action["request_id"], worker_id="fixture-" + action["request_id"],
                     worker_task=action["claim"]["worker_task"], **owner)


def complete(host, session_id, action, text="fixture output", **owner):
    owner = owner or OWNER
    return host.record(session_id, action["request_id"], worker_id="fixture-" + action["request_id"],
                       kind="completed", worker_status="completed", text=text, **owner)


def test_finite_list_roundtrip_and_repeated_result_are_idempotent(tmp_path):
    queue, host, sid, ids = setup(tmp_path, 2)
    for identity in ids:
        action = host.next(sid, **OWNER)
        assert action["action"] == "spawn" and action["request_id"] == identity
        assert action["claim"]["parent_task"] == "/root"
        bind(host, sid, action)
        bind(host, sid, action)
        complete(host, sid, action)
        complete(host, sid, action)
        assert [event["type"] for event in queue.inspect(identity)["events"]] == ["accepted", "message", "completed"]
        queue.finish(identity)
        complete(host, sid, action)
    assert host.next(sid, **OWNER) == {"action": "done", "terminal_count": 2}


def test_fresh_instance_never_reissues_an_unbound_dispatch(tmp_path):
    queue, host, sid, ids = setup(tmp_path)
    action = host.next(sid, **OWNER)
    restarted = DotsCoordinator(FileDotsTransport(queue.root))
    for _ in range(3):
        assert restarted.next(sid, **OWNER)["action"] == "reconcile"
    bind(restarted, sid, action)
    assert restarted.next(sid, **OWNER)["action"] == "wait"
    complete(restarted, sid, action)
    assert restarted.next(sid, **OWNER)["action"] == "done"


def test_concurrent_claim_attempts_admit_only_one_spawn(tmp_path):
    queue, host, sid, ids = setup(tmp_path, 3, max_concurrency=2)
    with ThreadPoolExecutor(max_workers=8) as pool:
        actions = list(pool.map(lambda _: host.next(sid, **OWNER), range(8)))
    assert sum(action["action"] == "spawn" for action in actions) == 1
    action = next(action for action in actions if action["action"] == "spawn")
    bind(host, sid, action)
    second = host.next(sid, **OWNER)
    assert second["action"] == "spawn" and second["request_id"] != action["request_id"]
    bind(host, sid, second)
    assert host.next(sid, **OWNER) == {"action": "wait", "active_count": 2, "pending_count": 1}
    complete(host, sid, action)
    assert host.next(sid, **OWNER)["action"] == "spawn"


def test_unlisted_request_is_never_discovered_or_dispatched(tmp_path):
    queue, host, sid, ids = setup(tmp_path)
    extra = DotsRequest("a" * 32, "not enrolled", "outside", None, None, time.time(), time.time() + 120)
    queue.submit(extra)
    first = host.next(sid, **OWNER)
    bind(host, sid, first)
    complete(host, sid, first)
    assert host.next(sid, **OWNER)["action"] == "done"
    assert queue.inspect(extra.request_id)["events"] == []
    with pytest.raises(DotsBridgeError, match="not in"):
        host.bind(sid, extra.request_id, worker_id="fixture", worker_task="/root/coordinate_tests/other", **OWNER)


def test_changed_request_invalidates_authorization(tmp_path):
    queue, host, sid, ids = setup(tmp_path)
    path = queue.root / ids[0] / "request.json"
    value = json.loads(path.read_text())
    value["prompt"] = "changed after enrollment"
    path.write_text(json.dumps(value))
    with pytest.raises(DotsBridgeError, match="changed"):
        host.next(sid, **OWNER)


def test_stop_drains_unstarted_tasks_without_spawning(tmp_path):
    queue, host, sid, ids = setup(tmp_path, 2)
    host.stop(sid, **OWNER)
    assert host.next(sid, **OWNER) == {"action": "done", "terminal_count": 2}
    for identity in ids:
        events = queue.inspect(identity)["events"]
        assert len(events) == 1 and events[0]["type"] == "cancelled"
        assert not (queue.root / identity / "host-claim.json").exists()


def test_cancel_requires_actual_worker_mapping_and_terminal_observation(tmp_path):
    queue, host, sid, ids = setup(tmp_path)
    action = host.next(sid, **OWNER)
    queue.cancel(ids[0], "user stop")
    assert host.next(sid, **OWNER)["action"] == "reconcile"
    assert bind(host, sid, action)["action"] == "cancel"
    assert queue.inspect(ids[0])["events"] == []
    with pytest.raises(DotsBridgeError, match="late"):
        complete(host, sid, action)
    with pytest.raises(DotsBridgeError, match="status"):
        host.record(sid, ids[0], worker_id="fixture-" + ids[0], kind="cancelled", text="stopped", worker_status="running", **OWNER)
    host.record(sid, ids[0], worker_id="fixture-" + ids[0], kind="cancelled", text="fixture interrupted", worker_status="interrupted", **OWNER)
    assert host.next(sid, **OWNER)["action"] == "done"


def test_bound_cancellation_rejects_late_completion(tmp_path):
    queue, host, sid, ids = setup(tmp_path)
    action = host.next(sid, **OWNER)
    bind(host, sid, action)
    queue.cancel(ids[0], "operator cancellation")
    assert host.next(sid, **OWNER)["action"] == "cancel"
    with pytest.raises(DotsBridgeError, match="late"):
        complete(host, sid, action)
    assert queue.inspect(ids[0])["events"][-1]["type"] == "accepted"


def test_expired_session_cancels_active_and_unstarted(tmp_path, monkeypatch):
    queue, host, sid, ids = setup(tmp_path, 2, lifetime_seconds=30)
    action = host.next(sid, **OWNER)
    bind(host, sid, action)
    deadline = host.status(sid)["session"]["expires_at"]
    monkeypatch.setattr(time, "time", lambda: deadline + 1)
    assert host.next(sid, **OWNER)["action"] == "cancel"
    host.record(sid, ids[0], worker_id="fixture-" + ids[0], kind="cancelled", text="fixture stopped", worker_status="completed", **OWNER)
    assert host.next(sid, **OWNER)["action"] == "done"
    assert queue.inspect(ids[1])["events"][0]["type"] == "cancelled"


def test_handoff_revokes_owner_and_preserves_child_mapping(tmp_path):
    queue, host, sid, ids = setup(tmp_path, 2)
    action = host.next(sid, **OWNER)
    replacement = host.handoff(sid, parent_task="/root", previous_generation=1, coordinator_task="/root/replacement")
    owner2 = {"coordinator_task": "/root/replacement", "generation": 2}
    assert replacement["generation"] == 2
    with pytest.raises(DotsBridgeError, match="stale"):
        host.next(sid, **OWNER)
    assert host.next(sid, **owner2)["claim"]["worker_task"] == action["claim"]["worker_task"]
    bind(host, sid, action, **owner2)
    complete(host, sid, action, **owner2)
    new_action = host.next(sid, **owner2)
    assert new_action["claim"]["worker_task"].startswith("/root/replacement/")
    with pytest.raises(DotsBridgeError, match="recorded parent"):
        host.handoff(sid, parent_task="/root", previous_generation=1, coordinator_task="/root/replacement2")


def test_claim_cannot_be_rebound_to_another_worker_or_path(tmp_path):
    queue, host, sid, ids = setup(tmp_path)
    action = host.next(sid, **OWNER)
    with pytest.raises(DotsBridgeError, match="direct-child"):
        host.bind(sid, ids[0], worker_id="fixture", worker_task="/root/wrong/task", **OWNER)
    bind(host, sid, action)
    with pytest.raises(DotsBridgeError, match="another worker"):
        host.bind(sid, ids[0], worker_id="other", worker_task=action["claim"]["worker_task"], **OWNER)
    with pytest.raises(DotsBridgeError, match="actual bound"):
        host.record(sid, ids[0], worker_id="other", kind="completed", worker_status="completed", text="fixture", **OWNER)


def test_multiple_sessions_cannot_dispatch_same_request(tmp_path):
    queue, host, sid, ids = setup(tmp_path)
    second = host.create(ids, parent_task="/root", coordinator_task="/root/another")
    host.next(sid, **OWNER)
    with pytest.raises(DotsBridgeError, match="different session"):
        host.next(second["session_id"], coordinator_task="/root/another", generation=1)
    with pytest.raises(DotsBridgeError, match="claimed"):
        host.create(ids, parent_task="/root", coordinator_task="/root/another")


def test_binding_recovers_crash_between_mapping_and_accepted(tmp_path, monkeypatch):
    queue, host, sid, ids = setup(tmp_path)
    action = host.next(sid, **OWNER)
    original = queue._emit_locked
    monkeypatch.setattr(queue, "_emit_locked", lambda *a, **kw: (_ for _ in ()).throw(OSError("fixture crash")))
    with pytest.raises(OSError, match="fixture crash"):
        bind(host, sid, action)
    snapshot = host.status(sid)["tasks"][ids[0]]
    assert snapshot["claim"]["worker_id"] and not snapshot["accepted"]
    assert host.next(sid, **OWNER)["action"] == "reconcile"
    monkeypatch.setattr(queue, "_emit_locked", original)
    bind(host, sid, action)
    assert host.next(sid, **OWNER)["action"] == "wait"


def test_completion_recovers_crash_after_message_without_duplicate_output(tmp_path, monkeypatch):
    queue, host, sid, ids = setup(tmp_path)
    action = host.next(sid, **OWNER)
    bind(host, sid, action)
    original = queue._emit_locked

    def crash(fd, request_id, kind, **kwargs):
        if kind == "completed":
            raise OSError("fixture crash")
        return original(fd, request_id, kind, **kwargs)

    monkeypatch.setattr(queue, "_emit_locked", crash)
    with pytest.raises(OSError):
        complete(host, sid, action)
    monkeypatch.setattr(queue, "_emit_locked", original)
    complete(host, sid, action)
    assert [e["type"] for e in queue.inspect(ids[0])["events"]] == ["accepted", "message", "completed"]


def test_cancel_can_supersede_incomplete_result_after_crash(tmp_path, monkeypatch):
    queue, host, sid, ids = setup(tmp_path)
    action = host.next(sid, **OWNER)
    bind(host, sid, action)
    original = queue._emit_locked
    monkeypatch.setattr(queue, "_emit_locked", lambda *a, **kw: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError):
        complete(host, sid, action)
    queue.cancel(ids[0], "stop after interrupted result write")
    monkeypatch.setattr(queue, "_emit_locked", original)
    host.record(sid, ids[0], worker_id="fixture-" + ids[0], kind="cancelled", text="fixture completed and stopped", worker_status="completed", **OWNER)
    assert host.next(sid, **OWNER)["action"] == "done"


def test_conflicting_completion_retry_rejected(tmp_path):
    queue, host, sid, ids = setup(tmp_path)
    action = host.next(sid, **OWNER)
    bind(host, sid, action)
    complete(host, sid, action)
    with pytest.raises(DotsBridgeError, match="conflicting"):
        complete(host, sid, action, text="different output")


def test_confirmed_failed_spawn_ends_request_and_never_requeues(tmp_path):
    queue, host, sid, ids = setup(tmp_path)
    host.next(sid, **OWNER)
    with pytest.raises(DotsBridgeError, match="confirmed"):
        host.abandon(sid, ids[0], spawn_status="unknown", text="missing response", **OWNER)
    host.abandon(sid, ids[0], spawn_status="not_created", text="fixture native tool explicitly rejected creation", **OWNER)
    assert host.next(sid, **OWNER)["action"] == "done"
    assert queue.inspect(ids[0])["events"][0]["type"] == "failed"


@pytest.mark.parametrize("fields", [
    {"options": {"sandbox_mode": "read-only"}}, {"tools": [{"name": "approve_review"}]},
    {"resume_thread_id": "previous"}, {"model": "specific-model"}, {"reasoning_effort": "high"},
])
def test_unsupported_host_capabilities_fail_closed(tmp_path, fields):
    queue = FileDotsTransport(tmp_path / "queue")
    value = DotsRequest("a" * 32, "fixture", "test", None, None, time.time(), time.time() + 100).to_dict()
    value.update(fields)
    queue.submit(DotsRequest(**value))
    with pytest.raises(DotsBridgeError, match="plain text|host-default"):
        DotsCoordinator(queue).create([value["request_id"]], parent_task="/root", coordinator_task="/root/test")
    assert queue.inspect(value["request_id"])["events"] == []


@pytest.mark.parametrize("kwargs", [
    {"lifetime_seconds": float("nan")}, {"lifetime_seconds": 3601}, {"lifetime_seconds": 0},
    {"max_concurrency": 0}, {"max_concurrency": True}, {"max_concurrency": 7},
])
def test_session_bounds(tmp_path, kwargs):
    with pytest.raises(DotsBridgeError):
        setup(tmp_path, **kwargs)


def test_cli_create_and_next_are_only_journal_actions(tmp_path, capsys):
    queue, host, sid, ids = setup(tmp_path)
    base = ["--bridge-dir", str(queue.root)]
    assert main(base + ["next", sid, "--owner", OWNER["coordinator_task"], "--generation", "1"]) == 0
    action = json.loads(capsys.readouterr().out)
    assert action["action"] == "spawn"
    assert queue.inspect(ids[0])["events"] == []
    assert main(base + ["next", sid, "--owner", OWNER["coordinator_task"], "--generation", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "reconcile"
    assert main(base + ["next", sid, "--owner", "/root/wrong", "--generation", "1"]) == 1
    assert "stale or wrong" in capsys.readouterr().err


def test_host_metadata_uses_private_symlink_safe_reads(tmp_path):
    queue, host, sid, ids = setup(tmp_path)
    metadata = queue.root / ("coordinator-" + sid + ".json")
    metadata.chmod(0o644)
    with pytest.raises(DotsBridgeError, match="private"):
        host.status(sid)
    metadata.chmod(0o600)
    original = metadata.read_bytes()
    metadata.unlink()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_bytes(original)
    elsewhere.chmod(0o600)
    metadata.symlink_to(elsewhere)
    with pytest.raises(OSError):
        host.status(sid)


def test_committed_event_history_corruption_is_not_a_done_result(tmp_path):
    queue, host, sid, ids = setup(tmp_path)
    action = host.next(sid, **OWNER)
    bind(host, sid, action)
    complete(host, sid, action)
    path = queue.root / ids[0] / "event-00000002.json"
    event = json.loads(path.read_text())
    event["text"] = "tampered"
    path.write_text(json.dumps(event))
    for operation in (lambda: host.next(sid, **OWNER), lambda: host.status(sid)):
        with pytest.raises(DotsBridgeError, match="audit"):
            operation()


def test_worker_identity_cannot_be_shared_between_requests(tmp_path):
    queue, host, sid, ids = setup(tmp_path, 2, max_concurrency=2)
    first = host.next(sid, **OWNER)
    bind(host, sid, first)
    second = host.next(sid, **OWNER)
    with pytest.raises(DotsBridgeError, match="another request"):
        host.bind(sid, second["request_id"], worker_id="fixture-" + first["request_id"],
                  worker_task=second["claim"]["worker_task"], **OWNER)


def test_next_exposes_pending_result_without_redispatch(tmp_path, monkeypatch):
    queue, host, sid, ids = setup(tmp_path)
    action = host.next(sid, **OWNER)
    bind(host, sid, action)
    original = queue._emit_locked
    monkeypatch.setattr(queue, "_emit_locked", lambda *a, **kw: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError):
        complete(host, sid, action, text="durable observed output")
    monkeypatch.setattr(queue, "_emit_locked", original)
    recovery = host.next(sid, **OWNER)
    assert recovery["action"] == "recover_result"
    assert recovery["result"]["text"] == "durable observed output"
    host.record(sid, ids[0], **recovery["result"], **OWNER)
    assert host.next(sid, **OWNER)["action"] == "done"


def test_raw_emit_cannot_bypass_managed_host_protocol(tmp_path):
    queue, host, sid, ids = setup(tmp_path)
    action = host.next(sid, **OWNER)
    with pytest.raises(DotsBridgeError, match="managed request"):
        queue.emit(ids[0], "accepted", worker_id="bypass")
    bind(host, sid, action)
    with pytest.raises(DotsBridgeError, match="managed request"):
        queue.emit(ids[0], "completed")
    complete(host, sid, action)
