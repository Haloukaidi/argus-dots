from __future__ import annotations

import json
import os
import stat
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from argus.adapters.dots_backend import MAX_PAYLOAD_BYTES, DotsBackend, DotsBridgeError, DotsRequest
from argus.adapters.dots_file_transport import FileDotsTransport
from argus.apps.dots_bridge import main
from argus.core.models import RunnerOptions
from argus.core.run_gateway import run_exec


def request(identity="a" * 32, **kwargs):
    return DotsRequest(identity, "only the authorized test", "test", None, None,
                       time.time(), time.time() + 20, **kwargs)


def test_private_atomic_roundtrip_and_no_replay(tmp_path):
    queue = FileDotsTransport(tmp_path / "bridge")
    req = request()
    queue.submit(req)
    assert stat.S_IMODE(queue.root.stat().st_mode) == 0o700
    for path in (queue.root / req.request_id).iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    queue.emit(req.request_id, "accepted", worker_id="real-worker")
    queue.emit(req.request_id, "message", text="answer")
    queue.emit(req.request_id, "completed")
    assert queue.poll(req.request_id, 2)["text"] == "answer"
    queue.finish(req.request_id)
    assert queue.inspect(req.request_id)["closed"] == {"status": "consumed"}
    with pytest.raises(DotsBridgeError, match="closed"):
        queue.poll(req.request_id, 1)
    with pytest.raises(FileExistsError):
        queue.submit(req)
    with pytest.raises(DotsBridgeError, match="terminal"):
        queue.emit(req.request_id, "completed")
    assert not list(queue.root.rglob(".pending-*"))


def test_actual_runner_waits_for_host_events(tmp_path):
    queue = FileDotsTransport(tmp_path / "bridge")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_exec, DotsBackend(queue, timeout_seconds=3, poll_interval=0.001),
                             prompt="test", run_label="role-boundary", options=RunnerOptions())
        deadline = time.monotonic() + 2
        while not list(queue.root.glob("*/request.json")) and time.monotonic() < deadline:
            time.sleep(0.001)
        path = next(queue.root.glob("*/request.json"))
        identity = path.parent.name
        queue.emit(identity, "accepted", worker_id="test-host-worker")
        queue.emit(identity, "message", text="actual file event")
        queue.emit(identity, "completed")
        result = future.result(3)
    assert result.exit_code == 0 and result.last_agent_message == "actual file event"


@pytest.mark.parametrize("identity", ["../outside", "/tmp/elsewhere", "A" * 32, "a/" * 16, "", "a" * 33])
def test_request_paths_cannot_escape(tmp_path, identity):
    queue = FileDotsTransport(tmp_path / "bridge")
    with pytest.raises(DotsBridgeError):
        queue.inspect(identity)


def test_queue_symlinks_and_parent_symlinks_are_rejected(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "alias"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(OSError):
        FileDotsTransport(link)
    with pytest.raises(OSError):
        FileDotsTransport(link / "queue")
    with pytest.raises(DotsBridgeError):
        FileDotsTransport(tmp_path / ".." / "escape")


def test_task_and_event_symlinks_are_rejected_without_touching_target(tmp_path):
    queue = FileDotsTransport(tmp_path / "queue")
    target = tmp_path / "target"
    target.write_text("untouched")
    (queue.root / ("b" * 32)).symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OSError):
        queue.inspect("b" * 32)
    req = request()
    queue.submit(req)
    (queue.root / req.request_id / "event-00000001.json").symlink_to(target)
    with pytest.raises(OSError):
        queue.poll(req.request_id, 1)
    assert target.read_text() == "untouched"


def test_private_modes_hardlinks_and_fifos_are_required(tmp_path):
    broad = tmp_path / "broad"
    broad.mkdir(mode=0o755)
    with pytest.raises(DotsBridgeError, match="private"):
        FileDotsTransport(broad)
    queue = FileDotsTransport(tmp_path / "queue")
    req = request()
    queue.submit(req)
    event = queue.root / req.request_id / "event-00000001.json"
    os.mkfifo(event, mode=0o600)
    with pytest.raises(DotsBridgeError, match="regular"):
        queue.poll(req.request_id, 1)
    event.unlink()
    os.link(queue.root / req.request_id / "request.json", event)
    with pytest.raises(DotsBridgeError, match="hard-linked"):
        queue.poll(req.request_id, 1)


def test_oversized_and_duplicate_key_json_rejected(tmp_path):
    queue = FileDotsTransport(tmp_path / "queue")
    req = request()
    queue.submit(req)
    event = queue.root / req.request_id / "event-00000001.json"
    event.write_text("x" * (MAX_PAYLOAD_BYTES + 1))
    event.chmod(0o600)
    with pytest.raises(DotsBridgeError, match="1 MiB"):
        queue.poll(req.request_id, 1)
    event.write_text('{"type":"completed","type":"failed"}')
    with pytest.raises(DotsBridgeError, match="duplicate"):
        queue.poll(req.request_id, 1)


def test_cancel_and_expiry_reject_late_work_but_allow_stop_ack(tmp_path):
    queue = FileDotsTransport(tmp_path / "queue")
    req = request()
    queue.submit(req)
    queue.emit(req.request_id, "accepted", worker_id="active-worker")
    queue.cancel(req.request_id, "operator stop")
    for kind, kwargs in [("message", {"text": "late"}), ("completed", {})]:
        with pytest.raises(DotsBridgeError, match="closed"):
            queue.emit(req.request_id, kind, **kwargs)
    queue.emit(req.request_id, "cancelled", text="host actually interrupted active-worker")
    assert queue.inspect(req.request_id)["events"][-1]["type"] == "cancelled"
    expired = DotsRequest("b" * 32, "task", "test", None, None, time.time() - 2, time.time() - 1)
    with pytest.raises(DotsBridgeError, match="expired"):
        queue.submit(expired)


def test_poll_during_normal_write_lock_is_pending_not_failure(tmp_path):
    queue = FileDotsTransport(tmp_path / "queue")
    req = request()
    queue.submit(req)
    with queue._task_fd(req.request_id) as fd, queue._locked(fd):
        assert queue.poll(req.request_id, 1) is None


def test_emit_requires_explicit_acceptance_and_unique_claim(tmp_path):
    queue = FileDotsTransport(tmp_path / "queue")
    req = request()
    queue.submit(req)
    with pytest.raises(DotsBridgeError, match="accept"):
        queue.emit(req.request_id, "completed")
    queue.emit(req.request_id, "accepted", worker_id="w")
    with pytest.raises(DotsBridgeError, match="accepted"):
        queue.emit(req.request_id, "accepted", worker_id="second-worker")


def test_cli_capabilities_and_no_host_timeout(tmp_path, capsys):
    queue = str(tmp_path / "queue")
    assert main(["--bridge-dir", queue, "capabilities"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert set(report) == {"manager", "engineer", "reviewer", "planner", "curator"}
    assert all(not v["role_tools"] and not v["resume"] for v in report.values())
    assert main(["--bridge-dir", queue, "run", "--prompt", "test", "--timeout", "0.01"]) == 124
    result = json.loads(capsys.readouterr().out)
    assert result["exit_code"] == 124 and result["stop_kind"] == "permanent_error"
    assert "unconfirmed" in result["fatal_error"]


def test_total_response_limit_is_enforced_before_host_inspect_or_append(tmp_path, monkeypatch):
    import argus.adapters.dots_file_transport as module

    queue = FileDotsTransport(tmp_path / "queue")
    req = request()
    queue.submit(req)
    queue.emit(req.request_id, "accepted", worker_id="w")
    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", 300)
    with pytest.raises(DotsBridgeError, match="8 MiB"):
        queue.emit(req.request_id, "message", text="x" * 200)
    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", 1)
    with pytest.raises(DotsBridgeError, match="8 MiB"):
        queue.inspect(req.request_id)


def test_event_published_before_head_update_recovers_without_overwrite(tmp_path, monkeypatch):
    queue = FileDotsTransport(tmp_path / "queue")
    req = request()
    queue.submit(req)
    original = queue._write
    def fail_head(fd, name, value, **kwargs):
        if name == "head.json":
            raise OSError("simulated crash before head publication")
        return original(fd, name, value, **kwargs)
    monkeypatch.setattr(queue, "_write", fail_head)
    with pytest.raises(OSError, match="simulated crash"):
        queue.emit(req.request_id, "accepted", worker_id="first-worker")
    monkeypatch.setattr(queue, "_write", original)
    assert queue.poll(req.request_id, 1)["worker_id"] == "first-worker"
    with pytest.raises(DotsBridgeError, match="accepted"):
        queue.emit(req.request_id, "accepted", worker_id="must-not-replay")
    queue.emit(req.request_id, "message", text="continued after recovery")
    assert len(queue.inspect(req.request_id)["events"]) == 2


def test_committed_head_cannot_roll_back_or_accept_changed_history(tmp_path):
    queue = FileDotsTransport(tmp_path / "queue")
    req = request()
    queue.submit(req)
    queue.emit(req.request_id, "accepted", worker_id="w")
    event = queue.root / req.request_id / "event-00000001.json"
    event.write_text(event.read_text().replace('"w"', '"changed"'))
    with pytest.raises(DotsBridgeError, match="changed"):
        queue.emit(req.request_id, "message", text="must fail")
    event.unlink()
    with pytest.raises(DotsBridgeError, match="envelope"):
        queue.emit(req.request_id, "accepted", worker_id="replacement")


def test_missing_head_recovers_but_malformed_head_fails(tmp_path):
    queue = FileDotsTransport(tmp_path / "queue")
    req = request()
    queue.submit(req)
    queue.emit(req.request_id, "accepted", worker_id="w")
    head = queue.root / req.request_id / "head.json"
    head.unlink()
    queue.emit(req.request_id, "message", text="recovery")
    assert json.loads(head.read_text())["sequence"] == 2
    data = json.loads(head.read_text())
    data["sequence"] = True
    head.write_text(json.dumps(data))
    with pytest.raises(DotsBridgeError, match="head"):
        queue.emit(req.request_id, "completed")


def test_normal_append_does_not_reparse_the_growing_log(tmp_path, monkeypatch):
    queue = FileDotsTransport(tmp_path / "queue")
    req = request()
    queue.submit(req)
    def unexpected_scan(*_):
        raise AssertionError("normal append must use the committed incremental head")
    monkeypatch.setattr(FileDotsTransport, "_events", unexpected_scan)
    queue.emit(req.request_id, "accepted", worker_id="w")
    for index in range(200):
        queue.emit(req.request_id, "message", text=str(index))
    queue.emit(req.request_id, "completed")
    assert queue.poll(req.request_id, 202)["type"] == "completed"


def test_two_hosts_cannot_both_claim_one_task(tmp_path):
    queue = FileDotsTransport(tmp_path / "queue")
    req = request()
    queue.submit(req)
    def claim(index):
        try:
            queue.emit(req.request_id, "accepted", worker_id=str(index))
            return True
        except DotsBridgeError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim, range(2))) == [False, True]
    assert len(queue.inspect(req.request_id)["events"]) == 1


@pytest.mark.parametrize("closed", [{}, {"status": "unknown"}, {"status": "consumed", "reason": "extra"},
    {"status": "cancel_requested", "reason": ""}, {"status": "cancel_requested", "reason": 4}])
def test_corrupt_closed_state_never_reopens_request(tmp_path, closed):
    queue = FileDotsTransport(tmp_path / "queue")
    req = request()
    queue.submit(req)
    path = queue.root / req.request_id / "closed.json"
    path.write_text(json.dumps(closed))
    path.chmod(0o600)
    with pytest.raises(DotsBridgeError, match="closed"):
        queue.poll(req.request_id, 1)
    with pytest.raises(DotsBridgeError, match="closed"):
        queue.emit(req.request_id, "accepted", worker_id="w")


@pytest.mark.parametrize("replacement", ["longer tampered text", "after!"])
def test_consumption_audits_full_history_and_rejects_hidden_gaps(tmp_path, replacement):
    queue = FileDotsTransport(tmp_path / "queue")
    req = request()
    queue.submit(req)
    queue.emit(req.request_id, "accepted", worker_id="w")
    queue.emit(req.request_id, "message", text="before")
    queue.emit(req.request_id, "completed")
    # A local mutation to an older record cannot be made invisible by the
    # O(1) tail check: final consumption re-audits the complete bounded log.
    old = queue.root / req.request_id / "event-00000002.json"
    old.write_text(old.read_text().replace('"before"', json.dumps(replacement)))
    with pytest.raises(DotsBridgeError, match="audit"):
        queue.finish(req.request_id)
    old.rename(old.with_name("event-00000099.json"))
    with pytest.raises(DotsBridgeError, match="sequence"):
        queue.finish(req.request_id)


@pytest.mark.parametrize("field,value", [("accepted", False), ("bytes", 0), ("sequence", True),
    ("protocol_version", True), ("terminal", "completed")])
def test_invalid_head_cannot_admit_another_worker(tmp_path, field, value):
    queue = FileDotsTransport(tmp_path / "queue")
    req = request()
    queue.submit(req)
    queue.emit(req.request_id, "accepted", worker_id="original")
    path = queue.root / req.request_id / "head.json"
    head = json.loads(path.read_text())
    head[field] = value
    path.write_text(json.dumps(head))
    with pytest.raises(DotsBridgeError):
        queue.emit(req.request_id, "accepted", worker_id="replacement")
