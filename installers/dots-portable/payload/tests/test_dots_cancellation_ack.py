"""Cancellation acknowledgements use only local deterministic test transports."""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from argus.adapters import dots_backend, dots_file_transport
from argus.adapters.dots_backend import DotsBackend, DotsBridgeError, DotsCapabilities, DotsRequest
from argus.adapters.dots_file_transport import FileDotsTransport
from argus.core.models import RunnerOptions
from argus.core.run_gateway import run_exec


class Clock:
    def __init__(self):
        self.elapsed = 0.0
        self.sleeps = []

    def time(self):
        return 1000 + self.elapsed

    def monotonic(self):
        return self.elapsed

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.elapsed += seconds


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(dots_backend, "time", clock)
    return clock


def event(request_id, sequence=1, **overrides):
    return {"protocol_version": 1, "request_id": request_id, "sequence": sequence,
            "type": "cancelled", "text": "host stopped its worker", **overrides}


class LegacyHost:
    capabilities = DotsCapabilities()

    def __init__(self):
        self.request = None
        self.cancelled = []
        self.finished = []

    def submit(self, request):
        self.request = request

    def poll(self, request_id, sequence):
        return None

    def cancel(self, request_id, reason):
        self.cancelled.append((request_id, reason))

    def finish(self, request_id):
        self.finished.append(request_id)


class ObservingHost(LegacyHost):
    def __init__(self, acknowledge=event):
        super().__init__()
        self.acknowledge = acknowledge
        self.observations = []

    def poll_cancellation(self, request_id):
        self.observations.append(request_id)
        return self.acknowledge(request_id)


def call(host, *, reason="operator pause requested", **kwargs):
    options = RunnerOptions(external_interrupt_reason_provider=(
        lambda: reason if host.request is not None else None))
    return run_exec(DotsBackend(host, **kwargs), prompt="local contract test",
                    run_label="cancellation", options=options)


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), -float("inf"),
                                  5.01, 10 ** 1000, True, False, None, "0.2", complex(1, 0)])
def test_grace_rejects_invalid_or_unbounded_values(value):
    with pytest.raises(ValueError, match="cancellation_grace_seconds"):
        DotsBackend(cancellation_grace_seconds=value)


@pytest.mark.parametrize("value", [0.001, 0.2, 5])
def test_fork_keeps_valid_grace(value):
    backend = DotsBackend(LegacyHost(), cancellation_grace_seconds=value)
    assert backend.fork().cancellation_grace_seconds == value


def test_legacy_transport_returns_unconfirmed_without_waiting(clock):
    host = LegacyHost()
    result = call(host)
    assert result.exit_code == 130 and result.stop_kind == "operator_pause"
    assert "unconfirmed" in result.fatal_error
    assert len(host.cancelled) == 1 and not host.finished and not clock.sleeps


def test_pre_dispatch_interrupt_needs_no_cancellation_observation(clock):
    host = ObservingHost()
    result = run_exec(DotsBackend(host), prompt="local test", run_label="pre-dispatch",
                      options=RunnerOptions(external_interrupt_reason_provider=(
                          lambda: "operator pause requested")))
    assert result.exit_code == 130 and result.stop_kind == "operator_pause"
    assert host.request is None and not host.cancelled and not host.observations
    assert not clock.sleeps


@pytest.mark.parametrize("reason,stop_kind", [
    ("operator pause requested", "operator_pause"),
    ("daemon stop requested", "daemon_shutdown"),
    ("global daily budget exhausted", "budget_exhausted"),
    ("operator abort requested", "operator_abort"),
])
def test_acknowledgement_preserves_original_interrupt(clock, reason, stop_kind):
    host = ObservingHost()
    result = call(host, reason=reason)
    assert result.exit_code == 130 and result.stop_kind == stop_kind
    assert reason in result.fatal_error and "host confirmed cancellation" in result.fatal_error
    assert "unconfirmed" not in result.fatal_error
    assert len(host.cancelled) == 1 and len(host.observations) == 1
    assert not host.finished and not result.output_tokens_present and not clock.sleeps


def test_pending_ack_is_observed_within_bounded_grace(clock):
    host = ObservingHost()
    host.acknowledge = lambda identity: event(identity) if len(host.observations) == 3 else None
    result = call(host, poll_interval=0.05, cancellation_grace_seconds=0.2)
    assert "host confirmed cancellation" in result.fatal_error
    assert clock.elapsed == pytest.approx(0.1)


def test_no_ack_exhausts_grace_even_when_poll_interval_is_long(clock):
    host = ObservingHost(lambda _: None)
    result = call(host, poll_interval=100, cancellation_grace_seconds=0.15)
    assert "unconfirmed" in result.fatal_error
    assert clock.elapsed == pytest.approx(0.15)
    assert clock.sleeps == [0.15] and len(host.observations) == 1
    assert len(host.cancelled) == 1 and not host.finished


@pytest.mark.parametrize("delay", [0.2, 0.21])
def test_ack_returning_at_or_after_deadline_is_not_confirmed(clock, delay):
    def late(identity):
        clock.elapsed += delay
        return event(identity)
    host = ObservingHost(late)
    result = call(host, cancellation_grace_seconds=0.2)
    assert result.exit_code == 130 and result.stop_kind == "operator_pause"
    assert "unconfirmed" in result.fatal_error and not host.finished


@pytest.mark.parametrize("ack", [
    lambda identity: True,
    lambda identity: {},
    lambda identity: event(identity, sequence=0),
    lambda identity: event(identity, sequence=True),
    lambda identity: event(identity, sequence=4097),
    lambda identity: event("b" * 32),
    lambda identity: event(identity, protocol_version=True),
    lambda identity: event(identity, text=""),
    lambda identity: event(identity, extra="unexpected"),
    lambda identity: {"protocol_version": 1, "request_id": identity, "sequence": 1, "type": "completed"},
    lambda identity: event(identity, type="failed"),
    lambda identity: event(identity, type="message"),
    lambda identity: {"protocol_version": 1, "request_id": identity, "sequence": 1,
                      "type": "tool_call", "name": "approve_review", "arguments": {}},
])
def test_invalid_or_non_cancelled_observation_cannot_change_stop(clock, ack):
    host = ObservingHost(ack)
    result = call(host)
    assert result.exit_code == 130 and result.stop_kind == "operator_pause"
    assert "unconfirmed" in result.fatal_error and "acknowledgement failed" in result.fatal_error
    assert not host.finished and not result.agent_messages


@pytest.mark.parametrize("error", [OSError("read unavailable"), KeyboardInterrupt()])
def test_observation_errors_preserve_stop_and_remain_visible(clock, error):
    def broken(_):
        raise error
    result = call(ObservingHost(broken))
    assert result.exit_code == 130 and result.stop_kind == "operator_pause"
    assert "unconfirmed" in result.fatal_error
    assert "acknowledgement failed" in result.fatal_error or "wait interrupted" in result.fatal_error


def test_noncallable_observation_is_an_explicit_failure(clock):
    host = LegacyHost()
    host.poll_cancellation = True
    result = call(host)
    assert "unconfirmed" in result.fatal_error and "must be callable" in result.fatal_error


@pytest.mark.parametrize("error", [OSError("write unavailable"), KeyboardInterrupt()])
def test_cancellation_record_errors_preserve_original_stop(clock, error):
    class Broken(ObservingHost):
        def cancel(self, *_):
            raise error
    host = Broken()
    result = call(host)
    assert result.exit_code == 130 and result.stop_kind == "operator_pause"
    assert "cancellation could not be recorded" in result.fatal_error
    assert "unconfirmed" in result.fatal_error and not host.observations


@pytest.mark.parametrize("acknowledge", [event, lambda _: None])
def test_timeout_outcome_survives_cancellation_grace(clock, acknowledge):
    host = ObservingHost(acknowledge)
    result = call(host, reason=None, timeout_seconds=0.1,
                  poll_interval=0.05, cancellation_grace_seconds=0.2)
    assert result.exit_code == 124 and result.stop_kind == "permanent_error"
    assert "timed out" in result.fatal_error and len(host.cancelled) == 1
    assert clock.elapsed == pytest.approx(0.1 if acknowledge is event else 0.3)


def test_hard_idle_outcome_survives_cancellation_grace(clock):
    host = ObservingHost()
    result = run_exec(DotsBackend(host, poll_interval=0.25), prompt="local test", run_label="idle",
                      options=RunnerOptions(watchdog_hard_idle_seconds=1))
    assert result.exit_code == 124 and result.stop_kind == "permanent_error"
    assert "hard idle timeout" in result.fatal_error and "host confirmed cancellation" in result.fatal_error
    assert clock.elapsed == 1 and len(host.cancelled) == 1


def test_keyboard_interrupt_can_report_ack_without_turning_into_success(clock):
    class Interrupted(ObservingHost):
        def poll(self, *_):
            raise KeyboardInterrupt
    result = call(Interrupted(), reason=None)
    assert result.exit_code == 130 and result.stop_kind == "operator_abort"
    assert "keyboard interrupt" in result.fatal_error and "host confirmed cancellation" in result.fatal_error


def test_original_transport_error_stays_visible_after_ack(clock):
    class Broken(ObservingHost):
        def poll(self, *_):
            raise OSError("original poll failure")
    result = call(Broken(), reason=None)
    assert result.exit_code == 1 and result.stop_kind == "permanent_error"
    assert "original poll failure" in result.fatal_error and "host confirmed cancellation" in result.fatal_error


@pytest.mark.parametrize("ack_sequence,confirmed", [(1, False), (2, True), (3, True)])
def test_unread_cancelled_tail_can_ack_but_replayed_sequence_cannot(clock, ack_sequence, confirmed):
    host = ObservingHost(lambda identity: event(identity, sequence=ack_sequence))
    interrupted = False
    def poll(identity, sequence):
        nonlocal interrupted
        if sequence == 1:
            return {"protocol_version": 1, "request_id": identity, "sequence": 1,
                    "type": "accepted", "worker_id": "accepted-worker"}
        interrupted = True
        # This completion races an interrupt after the transport read.
        return {"protocol_version": 1, "request_id": identity, "sequence": sequence, "type": "completed"}
    host.poll = poll
    result = run_exec(DotsBackend(host), prompt="local race", run_label="race",
                      options=RunnerOptions(external_interrupt_reason_provider=(
                          lambda: "operator pause requested" if interrupted else None)))
    assert result.exit_code == 130 and result.stop_kind == "operator_pause"
    assert result.thread_id == "accepted-worker" and not host.finished
    assert ("host confirmed cancellation" in result.fatal_error) is confirmed
    assert ("unconfirmed" in result.fatal_error) is not confirmed


def make_queue(tmp_path):
    queue = FileDotsTransport(tmp_path / "queue")
    now = time.time()
    request = DotsRequest("a" * 32, "local test", "test", None, None, now, now + 30)
    queue.submit(request)
    return queue, request.request_id


def test_file_transport_observes_actual_cancelled_event_without_reopening(tmp_path):
    queue, identity = make_queue(tmp_path)
    queue.emit(identity, "accepted", worker_id="actual-host-worker")
    queue.emit(identity, "message", text="partial output")
    assert queue.poll_cancellation(identity) is None
    queue.cancel(identity, "operator stop")
    assert queue.poll_cancellation(identity) is None  # closure alone is not a stopped worker
    expected = queue.emit(identity, "cancelled", text="host stopped actual-host-worker")
    assert queue.poll_cancellation(identity) == expected
    assert queue.inspect(identity)["closed"]["status"] == "cancel_requested"
    with pytest.raises(DotsBridgeError, match="closed"):
        queue.poll(identity, expected["sequence"])
    with pytest.raises(DotsBridgeError, match="terminal"):
        queue.emit(identity, "completed")


@pytest.mark.parametrize("terminal", ["completed", "failed"])
def test_existing_non_cancelled_terminal_does_not_ack_cancellation(tmp_path, terminal):
    queue, identity = make_queue(tmp_path)
    queue.emit(identity, "accepted", worker_id="host-worker")
    queue.emit(identity, terminal, **({"text": "failed"} if terminal == "failed" else {}))
    queue.cancel(identity, "operator stop raced terminal publication")
    assert queue.poll_cancellation(identity) is None
    with pytest.raises(DotsBridgeError, match="closed"):
        queue.poll(identity, 2)


def test_consumed_or_unrequested_cancellation_is_not_observed(tmp_path):
    queue, identity = make_queue(tmp_path)
    queue.emit(identity, "cancelled", text="host declined before start")
    assert queue.poll_cancellation(identity) is None
    queue.finish(identity)
    assert queue.poll_cancellation(identity) is None


def test_busy_cancellation_read_returns_pending(tmp_path):
    queue, identity = make_queue(tmp_path)
    queue.cancel(identity, "operator stop")
    with queue._task_fd(identity) as fd, queue._locked(fd):
        assert queue.poll_cancellation(identity) is None


def test_expired_request_can_still_receive_cancel_ack(tmp_path, monkeypatch):
    queue, identity = make_queue(tmp_path)
    queue.emit(identity, "accepted", worker_id="host-worker")
    expires_at = queue.inspect(identity)["request"]["expires_at"]
    monkeypatch.setattr(dots_file_transport, "time", SimpleNamespace(
        time=lambda: expires_at + 1, monotonic=time.monotonic, sleep=time.sleep))
    queue.cancel(identity, "request timed out")
    with pytest.raises(DotsBridgeError, match="closed or expired"):
        queue.emit(identity, "completed")
    acknowledgement = queue.emit(identity, "cancelled", text="host stopped expired worker")
    assert queue.poll_cancellation(identity) == acknowledgement


@pytest.mark.parametrize("target", ["request.json", "closed.json", "head.json", "event-00000003.json"])
def test_corrupt_cancellation_records_are_rejected(tmp_path, target):
    queue, identity = make_queue(tmp_path)
    queue.emit(identity, "accepted", worker_id="host-worker")
    queue.emit(identity, "message", text="before")
    queue.cancel(identity, "operator stop")
    queue.emit(identity, "cancelled", text="host stopped worker")
    (queue.root / identity / target).write_text("{}")
    with pytest.raises(DotsBridgeError):
        queue.poll_cancellation(identity)


def test_ack_audits_old_history_not_just_the_cancelled_tail(tmp_path):
    queue, identity = make_queue(tmp_path)
    queue.emit(identity, "accepted", worker_id="host-worker")
    queue.emit(identity, "message", text="before")
    queue.cancel(identity, "operator stop")
    queue.emit(identity, "cancelled", text="host stopped worker")
    path = queue.root / identity / "event-00000002.json"
    message = json.loads(path.read_text())
    message["text"] = "after!"
    path.write_text(json.dumps(message))
    with pytest.raises(DotsBridgeError, match="audit"):
        queue.poll_cancellation(identity)


def test_runner_file_queue_ack_preserves_partial_output_and_pause(tmp_path):
    class AcknowledgingQueue(FileDotsTransport):
        def submit(self, request):
            super().submit(request)
            self.emit(request.request_id, "accepted", worker_id="test-host-worker")
            self.emit(request.request_id, "message", text="partial")

        def cancel(self, identity, reason):
            super().cancel(identity, reason)
            self.emit(identity, "cancelled", text="test host stopped its worker")

    queue = AcknowledgingQueue(tmp_path / "queue")
    messages = []
    result = run_exec(DotsBackend(queue), prompt="test local queue", run_label="file cancellation",
                      options=RunnerOptions(on_agent_message=messages.append,
                          external_interrupt_reason_provider=(
                              lambda: "operator pause requested" if messages else None)))
    assert result.exit_code == 130 and result.stop_kind == "operator_pause"
    assert result.last_agent_message == "partial" and result.thread_id == "test-host-worker"
    assert "host confirmed cancellation" in result.fatal_error
    assert "unconfirmed" not in result.fatal_error
