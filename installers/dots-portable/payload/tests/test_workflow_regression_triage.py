"""Deterministic, isolated regressions derived from full-workflow triage.

Transport stays on loopback. The launcher fixture is a tiny checkout with no
interpreter. No provider transport, credentials, or external repository is used.
"""
from __future__ import annotations

import http.client
import shutil
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

from argus.core.role_tool_bridge import (
    MAX_ACTIVE_OPERATIONS,
    MAX_HANDLER_THREADS,
    CallBoundBridge,
    bridge_request,
)

ROOT = Path(__file__).resolve().parents[1]


def test_cancel_keeps_reserved_path_during_busy_response_teardown(monkeypatch):
    """Client-visible busy responses can precede release of handler capacity."""
    operations_full = threading.Event()
    release_operations = threading.Event()
    cleanup_occupied = threading.Event()
    release_cleanup = threading.Event()
    guard = threading.Lock()
    active = cleaning = 0

    def dispatch(operation, payload):
        nonlocal active
        if operation == "cancel":
            return {"cancelled": True}
        with guard:
            active += 1
            if active == MAX_ACTIVE_OPERATIONS:
                operations_full.set()
        assert release_operations.wait(5), "test did not release fake operations"
        return {"ok": True}

    def post_work(bridge):
        connection = http.client.HTTPConnection(*bridge.server.server_address, timeout=5)
        try:
            connection.request("POST", "/work", b"{}", {
                "Authorization": "Bearer " + bridge.environment["ARGUS_PLUGIN_TEST_TOKEN"],
            })
            response = connection.getresponse()
            response.read()
            return response.status
        finally:
            connection.close()

    with CallBoundBridge(dispatch, env_prefix="ARGUS_PLUGIN_TEST") as bridge:
        handler = bridge.server.RequestHandlerClass
        original_finish = handler.finish

        def finish_after_observable_response(self):
            nonlocal cleaning
            # The six fake operations are still blocked, so only the two
            # already-sent overflow responses can reach this point.
            if self.path == "/work" and not release_operations.is_set():
                with guard:
                    cleaning += 1
                    if cleaning >= 1:
                        cleanup_occupied.set()
                assert release_cleanup.wait(5), "test did not release handler cleanup"
            return original_finish(self)

        monkeypatch.setattr(handler, "finish", finish_after_observable_response)
        with ThreadPoolExecutor(max_workers=MAX_ACTIVE_OPERATIONS + 2) as executor:
            running = [executor.submit(post_work, bridge) for _ in range(MAX_ACTIVE_OPERATIONS)]
            try:
                assert operations_full.wait(3)
                overflow = [executor.submit(post_work, bridge) for _ in range(2)]
                assert [future.result(3) for future in overflow] == [503, 503]
                assert cleanup_occupied.wait(3)
                # Every overflow caller has finished, yet at least one busy
                # handler still retains its normal admission slot.
                assert bridge_request("ARGUS_PLUGIN_TEST", "cancel", {}, env=bridge.environment) == {"cancelled": True}
            finally:
                release_cleanup.set()
                release_operations.set()
            assert [future.result(3) for future in running] == [200] * MAX_ACTIVE_OPERATIONS


def test_launcher_reports_missing_interpreter_in_hermetic_checkout(tmp_path):
    node = shutil.which("node")
    assert node is not None, "this workflow explicitly supplies Node.js"
    checkout = tmp_path / "checkout"
    launcher = checkout / "frontend" / "scripts" / "python.mjs"
    launcher.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "frontend" / "scripts" / "python.mjs", launcher)
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    # No repository-local venv, no PATH interpreter, and no ambient
    # ARGUS_PYTHON/PYTHONPATH/VIRTUAL_ENV; a real missing-interpreter fixture.
    completed = subprocess.run(
        [node, str(launcher), "-m", "argus.release_tools.generate_event_types", "--check"],
        env={"HOME": str(home), "PATH": str(empty_path)},
        cwd=checkout, capture_output=True, text=True, timeout=10,
    )
    assert completed.returncode == 1
    assert "set ARGUS_PYTHON" in completed.stderr
    assert "argus.release_tools.generate_event_types" in completed.stderr
    assert "Traceback" not in completed.stderr


@contextmanager
def _saturated_bridge(monkeypatch, *, cancel_operation="cancel", cancel_dispatch=None):
    """Six active work calls plus one completed busy handler held in cleanup."""
    release = threading.Event()
    full = threading.Event()
    cleanup = threading.Event()
    release_cleanup = threading.Event()
    no_handlers = threading.Event()
    guard = threading.Lock()
    state = {"active": 0, "handlers": 0, "peak_handlers": 0, "cancelled": []}

    def dispatch(operation, payload):
        if operation == cancel_operation:
            state["cancelled"].append(payload)
            return cancel_dispatch(payload) if cancel_dispatch else {"cancelled": True}
        with guard:
            state["active"] += 1
            if state["active"] == MAX_ACTIVE_OPERATIONS:
                full.set()
        try:
            assert release.wait(10)
            return {"ok": True}
        finally:
            with guard:
                state["active"] -= 1

    with CallBoundBridge(dispatch, env_prefix="ARGUS_PLUGIN_TEST", cancel_operation=cancel_operation) as bridge:
        original_thread = bridge.server.process_request_thread

        def tracked(*args):
            with guard:
                state["handlers"] += 1
                no_handlers.clear()
                state["peak_handlers"] = max(state["peak_handlers"], state["handlers"])
            try:
                return original_thread(*args)
            finally:
                with guard:
                    state["handlers"] -= 1
                    if state["handlers"] == 0:
                        no_handlers.set()

        monkeypatch.setattr(bridge.server, "process_request_thread", tracked)
        handler = bridge.server.RequestHandlerClass
        original_finish = handler.finish

        def held_busy_cleanup(self):
            if self.path == "/work" and not release.is_set():
                cleanup.set()
                assert release_cleanup.wait(10)
            return original_finish(self)

        monkeypatch.setattr(handler, "finish", held_busy_cleanup)
        with ThreadPoolExecutor(max_workers=MAX_ACTIVE_OPERATIONS) as executor:
            work = [executor.submit(bridge_request, "ARGUS_PLUGIN_TEST", "work", {}, env=bridge.environment)
                    for _ in range(MAX_ACTIVE_OPERATIONS)]
            try:
                assert full.wait(3)
                assert _wire_request(bridge, path="/work")[0] == 503
                assert cleanup.wait(3)
                assert state["handlers"] == 7
                yield bridge, state
            finally:
                release_cleanup.set()
                release.set()
            assert [future.result(3) for future in work] == [{"ok": True}] * MAX_ACTIVE_OPERATIONS
        assert no_handlers.wait(3), "handler leaked after all fake calls were released"
        assert state["peak_handlers"] <= MAX_HANDLER_THREADS


def _wire_request(bridge, *, path="/cancel", body=b"{}", token=None, extra_headers=(), declared_size=None):
    connection = http.client.HTTPConnection(*bridge.server.server_address, timeout=5)
    try:
        connection.putrequest("POST", path)
        connection.putheader("Authorization", "Bearer " + (token or bridge.environment["ARGUS_PLUGIN_TEST_TOKEN"]))
        connection.putheader("Content-Length", str(len(body) if declared_size is None else declared_size))
        for name, value in extra_headers:
            connection.putheader(name, value)
        connection.endheaders(body)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


@pytest.mark.parametrize("kwargs,expected", [
    ({"path": "/work"}, 503),
    ({"path": "/work", "extra_headers": [("X-Control", "cancel")]}, 503),
    ({"path": "/cancel/work"}, 503),
    ({"path": "/cancel?operation=work"}, 503),
    ({"token": "forged-token"}, 503),
    ({"extra_headers": [("Authorization", "Bearer forged-token")]}, 503),
    ({"extra_headers": [("Content-Length", "3")]}, 503),
    ({"extra_headers": [("Transfer-Encoding", "chunked")]}, 503),
    ({"declared_size": 65537}, 503),
    ({"body": b'{"value":NaN}'}, 400),
    ({"body": b"[]"}, 400),
    ({"body": b"{"}, 400),
])
def test_reserved_control_admission_rejects_untrusted_requests(monkeypatch, kwargs, expected):
    with _saturated_bridge(monkeypatch) as (bridge, state):
        assert _wire_request(bridge, **kwargs)[0] == expected
        assert state["cancelled"] == []
        assert state["active"] == MAX_ACTIVE_OPERATIONS


def test_reserved_control_admission_honors_configured_cancel_operation(monkeypatch):
    with _saturated_bridge(monkeypatch, cancel_operation="cancel_validation") as (bridge, state):
        assert _wire_request(bridge)[0] == 503
        assert bridge_request("ARGUS_PLUGIN_TEST", "cancel_validation", {}, env=bridge.environment) == {"cancelled": True}
        assert state["cancelled"] == [{}]
        assert state["peak_handlers"] == MAX_HANDLER_THREADS


def test_partial_headers_do_not_occupy_reserved_control_worker(monkeypatch):
    with _saturated_bridge(monkeypatch) as (bridge, state):
        for fragment in (b"", b"POST /cancel HTTP/1.1\r\nAuthoriz"):
            stream = socket.create_connection(bridge.server.server_address, timeout=2)
            try:
                if fragment:
                    stream.sendall(fragment)
                response = http.client.HTTPResponse(stream)
                response.begin()
                assert response.status == 503
                response.read()
            finally:
                stream.close()
        assert state["peak_handlers"] == 7
        assert bridge_request("ARGUS_PLUGIN_TEST", "cancel", {}, env=bridge.environment) == {"cancelled": True}


def test_authenticated_slow_control_body_times_out_without_dispatch(monkeypatch):
    with _saturated_bridge(monkeypatch) as (bridge, state):
        stream = socket.create_connection(bridge.server.server_address, timeout=5)
        token = bridge.environment["ARGUS_PLUGIN_TEST_TOKEN"]
        try:
            stream.sendall((f"POST /cancel HTTP/1.1\r\nAuthorization: Bearer {token}\r\n"
                            "Content-Length: 50\r\n\r\n{").encode())
            started = time.monotonic()
            response = http.client.HTTPResponse(stream)
            response.begin()
            assert response.status == 400
            response.read()
            elapsed = time.monotonic() - started
            assert 2.5 <= elapsed < 4.5
            assert state["cancelled"] == []
            assert state["peak_handlers"] == MAX_HANDLER_THREADS
        finally:
            stream.close()


def test_blocked_cancel_and_burst_stay_bounded_and_shutdown_does_not_wait(monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def cancel(_payload):
        entered.set()
        assert release.wait(5)
        return {"cancelled": True}

    with _saturated_bridge(monkeypatch, cancel_dispatch=cancel) as (bridge, state):
        with ThreadPoolExecutor(max_workers=13) as executor:
            first = executor.submit(bridge_request, "ARGUS_PLUGIN_TEST", "cancel", {}, env=bridge.environment)
            try:
                assert entered.wait(2)
                overflow = [executor.submit(_wire_request, bridge) for _ in range(12)]
                assert [future.result(3)[0] for future in overflow] == [503] * 12
                assert state["cancelled"] == [{}]
                assert state["peak_handlers"] == MAX_HANDLER_THREADS
                started = time.monotonic()
                bridge.__exit__(None, None, None)
                assert time.monotonic() - started < 0.5
            finally:
                release.set()
            assert first.result(2) == {"cancelled": True}


def test_shutdown_with_partial_control_body_does_not_wait(monkeypatch):
    with _saturated_bridge(monkeypatch) as (bridge, state):
        stream = socket.create_connection(bridge.server.server_address, timeout=2)
        token = bridge.environment["ARGUS_PLUGIN_TEST_TOKEN"]
        control_started = threading.Event()
        original = bridge.server.process_request_thread

        def observed(*args):
            if len(args) == 3 and args[2]:
                control_started.set()
            return original(*args)

        monkeypatch.setattr(bridge.server, "process_request_thread", observed)
        try:
            stream.sendall((f"POST /cancel HTTP/1.1\r\nAuthorization: Bearer {token}\r\n"
                            "Content-Length: 50\r\n\r\n{").encode())
            assert control_started.wait(2)
            started = time.monotonic()
            bridge.__exit__(None, None, None)
            assert time.monotonic() - started < 0.5
            assert state["cancelled"] == []
        finally:
            stream.close()


def test_control_slot_is_released_when_worker_start_fails(monkeypatch):
    with _saturated_bridge(monkeypatch) as (bridge, state):
        original_start = threading.Thread.start
        errors = []
        attempts = 0

        def fail_first_start(thread):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("injected control worker start failure")
            return original_start(thread)

        monkeypatch.setattr(threading.Thread, "start", fail_first_start)
        monkeypatch.setattr(bridge.server, "handle_error", lambda *_args: errors.append("start failed"))
        with pytest.raises((http.client.RemoteDisconnected, ValueError)):
            bridge_request("ARGUS_PLUGIN_TEST", "cancel", {}, env=bridge.environment)
        assert errors == ["start failed"]
        assert bridge_request("ARGUS_PLUGIN_TEST", "cancel", {}, env=bridge.environment) == {"cancelled": True}
        assert state["cancelled"] == [{}]


def test_control_slot_is_released_when_socket_teardown_raises(monkeypatch):
    with _saturated_bridge(monkeypatch) as (bridge, state):
        original_shutdown = bridge.server.shutdown_request
        original_thread = bridge.server.process_request_thread
        finished = threading.Event()
        errors = []
        attempts = 0

        def fail_first_shutdown(request):
            nonlocal attempts
            attempts += 1
            original_shutdown(request)
            if attempts == 1:
                raise OSError("injected socket teardown failure")

        def capture_expected_thread_failure(*args):
            try:
                return original_thread(*args)
            except OSError as exc:
                assert str(exc) == "injected socket teardown failure"
                errors.append(str(exc))
            finally:
                finished.set()

        monkeypatch.setattr(bridge.server, "shutdown_request", fail_first_shutdown)
        monkeypatch.setattr(bridge.server, "process_request_thread", capture_expected_thread_failure)
        assert bridge_request("ARGUS_PLUGIN_TEST", "cancel", {}, env=bridge.environment) == {"cancelled": True}
        assert finished.wait(2)
        assert errors == ["injected socket teardown failure"]
        assert bridge_request("ARGUS_PLUGIN_TEST", "cancel", {}, env=bridge.environment) == {"cancelled": True}
        assert state["cancelled"] == [{}, {}]
