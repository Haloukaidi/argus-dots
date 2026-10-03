"""Explicit Web launch configuration is scope, never a native dispatcher."""
from __future__ import annotations

import json

import pytest

from argus.adapters.dots_host_binding import (
    require_configured_dots_runtime,
    resolve_dots_host_binding,
)
from argus.core.dots_host_binding import HOST_CONFIG_ENV, binding_configuration_key
from argus.core.runtime_backend import RuntimeBackendUnavailable


def test_no_locator_keeps_strict_dots_refusal():
    assert resolve_dots_host_binding(env={}) is None
    with pytest.raises(RuntimeBackendUnavailable, match="no native receiving transport"):
        require_configured_dots_runtime("dots", env={})


def test_regular_backend_ignores_native_locator():
    assert require_configured_dots_runtime("codex", env={HOST_CONFIG_ENV: "/missing/config"}) is None


@pytest.mark.parametrize("value", [
    {"version": 1},
    {"version": True, "bridge_dir": "/tmp", "session_id": "a" * 32,
     "producer_id": "web", "project_root": "/tmp", "execution_profile": {}},
    {"version": 1, "bridge_dir": "/tmp", "session_id": "a" * 32,
     "producer_id": "web", "project_root": "/tmp", "execution_profile": {}, "api_key": "forbidden"},
])
def test_invalid_config_is_rejected_before_transport(tmp_path, value):
    path = tmp_path / "host.json"
    path.write_text(json.dumps(value))
    with pytest.raises(RuntimeBackendUnavailable, match="host-required: invalid explicit host configuration"):
        resolve_dots_host_binding(env={HOST_CONFIG_ENV: str(path)})


def test_duplicate_keys_and_relative_or_symlink_locator_rejected(tmp_path):
    path = tmp_path / "host.json"
    path.write_text('{"version": 1, "version": 1}')
    with pytest.raises(RuntimeBackendUnavailable, match="duplicate"):
        resolve_dots_host_binding(env={HOST_CONFIG_ENV: str(path)})
    with pytest.raises(RuntimeBackendUnavailable, match="absolute"):
        resolve_dots_host_binding(env={HOST_CONFIG_ENV: "host.json"})
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(RuntimeBackendUnavailable, match="host-required"):
        resolve_dots_host_binding(env={HOST_CONFIG_ENV: str(link)})


def test_oversized_config_and_fifo_rejected_without_waiting(tmp_path):
    import os

    path = tmp_path / "host.json"
    path.write_bytes(b" " * 16385)
    with pytest.raises(RuntimeBackendUnavailable, match="at most 16 KiB"):
        resolve_dots_host_binding(env={HOST_CONFIG_ENV: str(path)})
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(RuntimeBackendUnavailable, match="regular file"):
        resolve_dots_host_binding(env={HOST_CONFIG_ENV: str(fifo)})


def test_cache_identity_changes_when_same_locator_is_rebound(tmp_path):
    path = tmp_path / "host.json"
    value = {"version": 1, "bridge_dir": "/tmp", "session_id": "a" * 32,
             "producer_id": "web", "project_root": "/tmp", "execution_profile": {}}
    path.write_text(json.dumps(value))
    env = {HOST_CONFIG_ENV: str(path)}
    before = binding_configuration_key(env=env)
    value["session_id"] = "b" * 32
    path.write_text(json.dumps(value))
    assert binding_configuration_key(env=env) != before


def test_launcher_refuses_missing_host_before_web(monkeypatch, tmp_path):
    from argus.apps import cli, dots_web

    monkeypatch.setattr(cli, "main", lambda *_: pytest.fail("no Web process before live-host preflight"))
    assert dots_web.main(["--host-config", str(tmp_path / "missing.json")]) == 3
    assert HOST_CONFIG_ENV not in __import__("os").environ


def test_binding_cannot_redefault_a_mixed_pipeline_to_dots(monkeypatch):
    from types import SimpleNamespace

    from argus.adapters import dots_host_binding

    monkeypatch.setattr(dots_host_binding, "resolve_dots_host_binding", lambda **_: SimpleNamespace(
        transport=object(), execution_profile=object(),
    ))
    with pytest.raises(RuntimeBackendUnavailable, match="all five roles must resolve to dots"):
        require_configured_dots_runtime("codex", env={"ARGUS_SKILL_PLANNER_BACKEND": "dots"})


def test_no_binding_preserves_existing_warm_cache_key():
    from argus.core.runtime_backend import runtime_configuration_key
    from argus.manager.runtime_config import refresh_manager_runtime_config

    state = {"backend": "codex", "manager_runtime_config": runtime_configuration_key(),
             "manager_runner": object()}
    runner = state["manager_runner"]
    refresh_manager_runtime_config(state)
    assert state["manager_runtime_config"] == runtime_configuration_key()
    assert state["manager_runner"] is runner


def test_supervised_continue_is_pinned_before_second_resolution(tmp_path, monkeypatch):
    import argparse

    from argus.apps.cli import _core
    from argus.core import session

    workdir = tmp_path / "workspace"
    workdir.mkdir()
    root = tmp_path / "home"
    session.write_session_meta(root, session.SessionMeta(id="selected", workdir=str(workdir)))
    calls = []

    def resolve_session(*, global_root, mode, session_id=None, **kwargs):
        calls.append(mode)
        if mode == "continue":
            # A second continue would choose a different project.
            return ("selected" if calls.count("continue") == 1 else "newer"), False
        assert mode == "resume" and session_id == "selected"
        return session_id, False

    monkeypatch.setattr(session, "resolve_session", resolve_session)
    args = argparse.Namespace(life_dir=str(root), continue_session=True, resume=None, new=False)
    assert _core._supervised_dots_cli_workdir(args) == workdir
    assert _core._resolve_session_id(args, root, default_to_new=False) == ("selected", False)
    assert calls == ["continue", "resume"]


@pytest.fixture
def live_binding(tmp_path, monkeypatch):
    """Deterministic host assertions; no native tools or model calls."""
    from argus.adapters.dots_supervised import (
        SupervisedDotsRoleHost,
        SupervisedRoleFileDotsTransport,
    )
    from argus.core.dots_profile import SupervisedDotsProfile
    from argus.core.runtime_backend import DOTS_ROLES

    project = tmp_path / "project"
    project.mkdir()
    profile = SupervisedDotsProfile(model_efforts=(("", ("low", "high", "xhigh")),))
    transport = SupervisedRoleFileDotsTransport(tmp_path / "queue", profile=profile)
    host = SupervisedDotsRoleHost(transport)
    owner = {"coordinator_task": "/root/fixture_host", "generation": 1}
    session = host.create(parent_task="/root", coordinator_task=owner["coordinator_task"],
                          producer_id="fixture-web", project_root=project, mission_id="fixture-workflow",
                          allowed_roles=list(DOTS_ROLES), max_requests=16, lifetime_seconds=600)
    host.heartbeat(session["session_id"], **owner)
    value = {"version": 1, "bridge_dir": str(transport.root), "session_id": session["session_id"],
             "producer_id": "fixture-web", "project_root": str(project), "execution_profile": profile.to_dict()}
    path = tmp_path / "binding.json"
    path.write_text(json.dumps(value))
    monkeypatch.setenv(HOST_CONFIG_ENV, str(path))
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "dots")
    return path, value, host, owner


def test_live_scoped_profile_is_ready_but_enforced_controls_stay_empty(live_binding):
    from argus.adapters.dots_host_binding import (
        check_configured_backend_readiness as check_backend_readiness,
    )
    from argus.core.role_config import resolve_all_roles
    from argus.core.runtime_backend import dots_readiness_problems

    _, value, _, _ = live_binding
    binding = require_configured_dots_runtime("dots", project_root=value["project_root"])
    assert binding.transport.capabilities.options == frozenset()
    assert dots_readiness_problems(binding.transport)  # strict gate still refuses
    assert not dots_readiness_problems(binding.transport, execution_profile=binding.execution_profile)
    report = check_backend_readiness("dots", dots_project_root=value["project_root"])
    assert report.ok and not report.auth_checked and report.executable == ""
    assert any("supervised-approx-v1" in warning for warning in report.warnings)
    assert all("supervised approximate" in role.backend_label for role in resolve_all_roles())


def test_wrong_project_and_profile_fail_before_any_request(live_binding, tmp_path):
    path, value, host, _ = live_binding
    with pytest.raises(RuntimeBackendUnavailable, match="outside the explicit host binding"):
        require_configured_dots_runtime("dots", project_root=tmp_path)
    value["execution_profile"]["filesystem_isolation"] = True
    path.write_text(json.dumps(value))
    with pytest.raises(RuntimeBackendUnavailable, match="profile metadata|exact versioned deficits"):
        require_configured_dots_runtime("dots")
    assert not host.status(value["session_id"])["tasks"]


def test_stale_lease_is_host_required(live_binding, monkeypatch):
    import time
    from types import SimpleNamespace

    from argus.adapters import dots_supervised

    _, value, host, owner = live_binding
    now = time.time()
    host.heartbeat(value["session_id"], lease_seconds=1, **owner)
    monkeypatch.setattr(dots_supervised, "time", SimpleNamespace(time=lambda: now + 2))
    with pytest.raises(RuntimeBackendUnavailable, match="host-required.*stale"):
        require_configured_dots_runtime("dots")


@pytest.mark.parametrize("changed_field", ["session_id", "request_timeout_seconds"])
def test_configuration_replacement_blocks_new_calls_but_preserves_settlement(live_binding, changed_field):
    import time

    from argus.adapters.dots_backend import DotsRequest

    path, value, host, owner = live_binding
    binding = require_configured_dots_runtime("dots")
    now = time.time()
    request = DotsRequest("1" * 32, "fixture only", "fixture", None, None, now, now + 60)
    binding.transport.submit(request)
    action = host.next(value["session_id"], **owner)
    host.bind(value["session_id"], request.request_id, worker_id="fixture-worker",
              worker_task=action["claim"]["worker_task"], **owner)
    value[changed_field] = "f" * 32 if changed_field == "session_id" else 900
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="launcher binding changed"):
        binding.transport.assert_ready()
    # Existing immutable request settlement is readable after a locator change.
    host.record(binding.transport.session_id, request.request_id, worker_id="fixture-worker",
                worker_status="completed", turn_request_id=request.request_id,
                kind="completed", text="ARGUS_DOTS_CALL:" + request.request_id + "\nfixture result", **owner)
    assert binding.transport.poll(request.request_id, 1)["type"] == "accepted"
    assert binding.transport.poll(request.request_id, 2)["text"] == "fixture result"
    # Exact retry only recovers its original immutable receipt, even after
    # config replacement. Same ID with changed content is never a new request.
    binding.transport.submit(request)
    from dataclasses import replace

    with pytest.raises(ValueError, match="different producer session or digest|conflicting producer retry"):
        binding.transport.submit(replace(request, prompt="changed fixture"))
    later = DotsRequest("2" * 32, "fixture only", "fixture", None, None, now, now + 60)
    with pytest.raises(ValueError, match="launcher binding changed"):
        binding.transport.submit(later)


def test_readiness_never_recreates_missing_queue_or_lock(live_binding):
    from pathlib import Path

    _, value, _, _ = live_binding
    root = Path(value["bridge_dir"])
    lock = root / "lock"
    lock.unlink()
    before = sorted(path.name for path in root.iterdir())
    with pytest.raises(RuntimeBackendUnavailable, match="host-required"):
        require_configured_dots_runtime("dots")
    assert not lock.exists()
    assert sorted(path.name for path in root.iterdir()) == before


def test_launcher_uses_original_web_entry_and_explicit_environment(live_binding, monkeypatch):
    import os

    from argus.apps import cli, dots_web

    path, value, _, _ = live_binding
    monkeypatch.delenv(HOST_CONFIG_ENV)
    seen = {}

    def start(argv):
        seen.update(argv=argv, locator=os.environ[HOST_CONFIG_ENV], backend=os.environ["ARGUS_SKILL_RUNNER_BACKEND"])
        return 0

    monkeypatch.setattr(cli, "main", start)
    assert dots_web.main(["--host-config", str(path), "--web-port", "8888"]) == 0
    assert seen == {"argv": ["--web", "--web-host", "127.0.0.1", "--web-port", "8888"],
                    "locator": str(path), "backend": "dots"}
    assert HOST_CONFIG_ENV not in os.environ


@pytest.mark.parametrize("timeout", [None, 900, 0.5, 3600])
def test_frontdoor_and_daemon_factory_share_same_bound_host(live_binding, monkeypatch, tmp_path, timeout):
    import argparse
    from pathlib import Path
    from types import SimpleNamespace

    from argus.apps import _runtime, _runtime_construction
    from argus.daemon._life_worker_runtime_context import _runner_namespace
    from argus.daemon.config import LifeWorkerConfig
    from argus.manager.front_door import _ensure_manager_runner

    path, value, _, _ = live_binding
    if timeout is not None:
        value["request_timeout_seconds"] = timeout
        path.write_text(json.dumps(value))
    expected_timeout = 300 if timeout is None else timeout
    seen = []

    def build(ns, **kwargs):
        seen.append(ns)
        return SimpleNamespace()

    monkeypatch.setattr(_runtime, "build_life_runner", build)
    mem = SimpleNamespace(project_root=value["project_root"], global_root=tmp_path / "state", root=tmp_path / "state")
    state = {"backend": "dots"}
    manager = _ensure_manager_runner(state, mem)
    assert manager is not None
    assert _ensure_manager_runner(state, mem) is manager
    assert len(seen) == 1
    manager_ns = seen[0]
    assert manager_ns.dots_transport.session_id == value["session_id"]
    assert manager_ns.dots_execution_profile.name == "supervised-approx-v1"
    assert manager_ns.dots_request_timeout_seconds == expected_timeout

    # The detached daemon's standard namespace has no serialized Python host.
    # Shared composition reconstructs the same explicit locator/session.
    cfg = LifeWorkerConfig(life_dir=tmp_path / "state", project_workdir=Path(value["project_root"]), backend="dots")
    daemon_ns = _runner_namespace(cfg)
    assert isinstance(daemon_ns, argparse.Namespace)
    assert not hasattr(daemon_ns, "dots_transport")
    monkeypatch.setattr(_runtime_construction, "build_dots_life_runner", lambda ns, **kwargs: kwargs)
    received = _runtime_construction.build_life_runner(daemon_ns)
    assert received["transport"].session_id == value["session_id"]
    assert received["timeout_seconds"] == expected_timeout
    assert daemon_ns.dots_request_timeout_seconds == expected_timeout
    assert daemon_ns.dots_execution_profile == manager_ns.dots_execution_profile


def test_normal_web_message_accepts_bound_project_and_refuses_other_project(live_binding, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from argus.webapi.daemon_lifecycle import create_daemon
    from argus.webapi.server import create_app

    _, value, _, _ = live_binding
    root = tmp_path / "state"
    accepted = create_daemon(name="bound fixture", workdir=value["project_root"], global_root=root)
    refused = create_daemon(name="unbound fixture", global_root=root)
    calls = []

    def message(*args, **kwargs):
        calls.append(args)
        return {"kind": "chat", "reply": "deterministic fixture reply"}

    monkeypatch.setattr("argus.webapi.manager_bridge.manager_message", message)
    monkeypatch.setattr("argus.adapters.agent_cli_backend.AgentCliBackend",
                        lambda *a, **k: pytest.fail("native binding must not construct CLI"))
    with TestClient(create_app(global_root=root)) as client:
        ok = client.post(f"/api/projects/{accepted['sid']}/message", json={"text": "fixture"})
        blocked = client.post(f"/api/projects/{refused['sid']}/message", json={"text": "fixture"})
    assert ok.status_code == 200 and ok.json()["reply"] == "deterministic fixture reply"
    assert blocked.status_code == 503 and "outside the explicit host binding" in blocked.json()["detail"]
    assert len(calls) == 1


@pytest.mark.parametrize("timeout", [None, 900])
@pytest.mark.parametrize("entry", ["direct", "web", "daemon"])
def test_actual_shared_factory_constructs_five_explicit_profile_backends(live_binding, monkeypatch, tmp_path, timeout, entry):
    import argparse
    from pathlib import Path
    from types import SimpleNamespace

    from argus.apps._runtime_construction import build_life_runner
    from argus.core.runtime_backend import DOTS_ROLES
    from argus.daemon._life_worker_runtime_context import _runner_namespace
    from argus.daemon.config import LifeWorkerConfig
    from argus.manager.front_door import _ensure_manager_runner

    path, value, _, _ = live_binding
    if timeout is not None:
        value["request_timeout_seconds"] = timeout
        path.write_text(json.dumps(value))
    monkeypatch.setattr("argus.adapters.agent_cli_backend.AgentCliBackend",
                        lambda *a, **k: pytest.fail("native factory must not construct CLI"))
    if entry == "web":
        mem = SimpleNamespace(project_root=value["project_root"], global_root=tmp_path / "state", root=tmp_path / "state")
        runner = _ensure_manager_runner({"backend": "dots"}, mem)
    else:
        args = (_runner_namespace(LifeWorkerConfig(life_dir=tmp_path / "state",
                    project_workdir=Path(value["project_root"]), backend="dots"))
                if entry == "daemon" else argparse.Namespace(backend="dots",
                    workdir=value["project_root"], skills_dir=str(tmp_path / "skills")))
        runner = build_life_runner(args)
    for role in DOTS_ROLES:
        backend = getattr(runner, role + "_backend")
        assert backend.backend == "dots"
        assert backend.timeout_seconds == (300 if timeout is None else timeout)
        assert backend.execution_profile.name == "supervised-approx-v1"
        assert backend.transport.session_id == value["session_id"]
        assert backend.capability_report()["supported_options"] == []


def test_readiness_rejects_session_without_all_upstream_roles(live_binding):
    path, value, host, owner = live_binding
    smaller = host.create(parent_task="/root", coordinator_task=owner["coordinator_task"],
                          producer_id=value["producer_id"], project_root=value["project_root"],
                          mission_id="manager-only", allowed_roles=["manager"], max_requests=4)
    host.heartbeat(smaller["session_id"], **owner)
    value["session_id"] = smaller["session_id"]
    path.write_text(json.dumps(value))
    with pytest.raises(RuntimeBackendUnavailable, match="role is not supported"):
        require_configured_dots_runtime("dots")


def test_readiness_refuses_exhausted_budget(live_binding):
    import time

    from argus.adapters.dots_backend import DotsRequest
    from argus.core.runtime_backend import DOTS_ROLES

    path, value, host, owner = live_binding
    bounded = host.create(parent_task="/root", coordinator_task=owner["coordinator_task"],
                          producer_id=value["producer_id"], project_root=value["project_root"],
                          mission_id="one-call", allowed_roles=list(DOTS_ROLES), max_requests=1)
    host.heartbeat(bounded["session_id"], **owner)
    value["session_id"] = bounded["session_id"]
    path.write_text(json.dumps(value))
    binding = require_configured_dots_runtime("dots")
    now = time.time()
    binding.transport.submit(DotsRequest("1" * 32, "fixture", "fixture", None, None, now, now + 60))
    with pytest.raises(RuntimeBackendUnavailable, match="budget is exhausted"):
        require_configured_dots_runtime("dots")


def test_live_binding_does_not_make_cli_or_api_auth_ready(live_binding):
    from argus.adapters.dots_host_binding import (
        check_configured_backend_readiness as check_backend_readiness,
    )

    report = check_backend_readiness("dots", "model_api")
    assert not report.ok
    assert report.problems[0].capability == "auth_mode"
    assert not report.auth_checked and not report.executable


def test_injected_transport_does_not_implicitly_enable_approximation(live_binding):
    import argparse

    from argus.apps._runtime_construction import build_life_runner

    _, value, _, _ = live_binding
    binding = require_configured_dots_runtime("dots")
    args = argparse.Namespace(backend="dots", workdir=value["project_root"], dots_transport=binding.transport)
    with pytest.raises(RuntimeBackendUnavailable, match="missing enforced controls"):
        build_life_runner(args)


def test_explicit_native_host_auth_is_retained_for_live_dots(live_binding, monkeypatch):
    from argus.adapters.dots_host_binding import check_configured_backend_readiness
    from argus.core.backend_readiness import resolve_backend_profile

    monkeypatch.setenv("ARGUS_SKILL_BACKEND_AUTH_MODE", "native_host")
    profile = resolve_backend_profile("dots")
    assert profile.auth_mode == "native_host"
    assert profile.auth_mode_source == "env:ARGUS_SKILL_BACKEND_AUTH_MODE"
    assert check_configured_backend_readiness("dots").ok


def test_launcher_reaches_actual_cli_web_composition(live_binding, monkeypatch):
    from argus.apps import dots_web
    from argus.webapi import server

    path, _, _, _ = live_binding
    calls = []
    monkeypatch.setattr(server, "serve", lambda **kwargs: calls.append(kwargs) or 0)
    assert dots_web.main(["--host-config", str(path), "--web-port", "8888"]) == 0
    assert len(calls) == 1
    assert calls[0]["host"] == "127.0.0.1" and calls[0]["port"] == 8888


@pytest.mark.parametrize("timeout", [0, -1, True, False, "900", None, float("nan"), float("inf"), -float("inf"), 3600.1, 10**400])
def test_invalid_request_timeout_fails_before_transport_or_writes(live_binding, monkeypatch, timeout):
    from pathlib import Path

    from argus.adapters import dots_supervised

    path, value, host, _ = live_binding
    root = Path(value["bridge_dir"])
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    value["request_timeout_seconds"] = timeout
    path.write_text(json.dumps(value))
    monkeypatch.setattr(dots_supervised, "SupervisedRoleFileDotsTransport",
                        lambda *a, **k: pytest.fail("invalid config must not construct transport"))
    with pytest.raises(RuntimeBackendUnavailable, match="request_timeout_seconds|nonfinite"):
        require_configured_dots_runtime("dots")
    assert {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before
    assert not host.status(value["session_id"])["tasks"]


def test_timeout_only_edit_changes_identity_and_rebuilds_warm_manager(live_binding, tmp_path):
    from types import SimpleNamespace

    from argus.manager.front_door import _ensure_manager_runner

    path, value, _, _ = live_binding
    binding = require_configured_dots_runtime("dots")
    before = binding_configuration_key()
    mem = SimpleNamespace(project_root=value["project_root"], global_root=tmp_path / "state", root=tmp_path / "state")
    state = {"backend": "dots"}
    original = _ensure_manager_runner(state, mem)
    assert original.manager_backend.timeout_seconds == 300
    value["request_timeout_seconds"] = 900
    path.write_text(json.dumps(value))
    assert binding_configuration_key() != before
    assert require_configured_dots_runtime("dots").identity != binding.identity
    rebuilt = _ensure_manager_runner(state, mem)
    assert rebuilt is not original
    assert rebuilt.manager_backend.timeout_seconds == 900
    assert original.manager_backend.timeout_seconds == 300


@pytest.fixture
def host_clock(live_binding, monkeypatch):
    import time

    from argus.adapters import (
        dots_admission,
        dots_backend,
        dots_coordinator,
        dots_file_transport,
        dots_supervised,
    )

    now = time.time()

    class Clock:
        elapsed = 0.0

        def time(self):
            return now + self.elapsed

        def monotonic(self):
            return self.elapsed

        def sleep(self, seconds):
            self.elapsed += seconds

    clock = Clock()
    for module in (dots_admission, dots_backend, dots_coordinator, dots_file_transport, dots_supervised):
        monkeypatch.setattr(module, "time", clock)
    return clock


@pytest.mark.parametrize("timeout", [300, 900])
@pytest.mark.parametrize("interrupt_at_expiry", [False, True])
def test_effective_deadline_includes_preparation_and_queued_time(live_binding, host_clock, monkeypatch, tmp_path, timeout, interrupt_at_expiry):
    import argparse

    from argus.apps._runtime_construction import build_life_runner
    from argus.core.models import RunnerOptions

    path, value, host, owner = live_binding
    value["request_timeout_seconds"] = timeout
    path.write_text(json.dumps(value))
    runner = build_life_runner(argparse.Namespace(backend="dots", workdir=value["project_root"],
                                                skills_dir=str(tmp_path / "skills")))
    backend = runner.manager_backend
    transport = backend.transport
    session_deadline = host.status(value["session_id"])["session"]["expires_at"]
    readiness = transport.assert_ready()
    assert readiness["session_expires_at"] == session_deadline
    assert readiness["expires_at"] < session_deadline  # renewable lease is not the session budget
    assert backend.capability_report()["request_timeout_seconds"] == timeout
    started = host_clock.time()
    original_ready, original_submit = transport.assert_ready, transport.submit
    submitted = []

    def prepare():
        ready = original_ready()
        host_clock.sleep(7)  # preparation consumes the original call budget
        return ready

    def queue(request):
        original_submit(request)
        submitted.append(request)
        host_clock.sleep(request.expires_at - host_clock.time() + 1)
        # A delayed dispatch cannot create a fresh execution timeout.

    monkeypatch.setattr(transport, "assert_ready", prepare)
    monkeypatch.setattr(transport, "submit", queue)
    monkeypatch.setattr(transport, "poll", lambda *a: pytest.fail("expired queue must not poll for success"))
    result = backend.run_exec(prompt="queued fixture", run_label="fixture", options=RunnerOptions(
        external_interrupt_reason_provider=lambda: (
            "operator abort requested" if submitted and interrupt_at_expiry else None),
    ))
    request = submitted[0]
    assert request.created_at == started
    assert request.expires_at == min(started + timeout, session_deadline)
    assert backend.timeout_seconds == timeout  # requested budget is still distinct from the effective deadline
    assert result.exit_code == (130 if interrupt_at_expiry else 124)
    assert ("External interrupt" if interrupt_at_expiry else "timed out") in result.fatal_error
    host.next(value["session_id"], **owner)
    task = host.status(value["session_id"])["tasks"][request.request_id]
    assert task["claim"] is None and task["terminal"] == "cancelled"
    assert transport.poll_cancellation(request.request_id)["type"] == "cancelled"
    # The exact enrolled receipt remains recoverable after its immutable expiry.
    transport.submit = original_submit
    transport.submit(request)
    assert transport.inspect(request.request_id)["request"] == request.to_dict()


@pytest.mark.parametrize("session_expiry", [None, True, False, 0, "3600", float("nan"), float("inf"), -float("inf")])
def test_supervised_missing_or_invalid_session_deadline_fails_before_publication(live_binding, monkeypatch, session_expiry):
    from argus.adapters.dots_backend import DotsBackend
    from argus.core.models import RunnerOptions

    _, value, host, _ = live_binding
    binding = require_configured_dots_runtime("dots")
    monkeypatch.setattr(binding.transport, "assert_ready", lambda: {"session_expires_at": session_expiry})
    result = DotsBackend(binding.transport, execution_profile=binding.execution_profile).run_exec(
        prompt="fixture", run_label="fixture", options=RunnerOptions())
    assert result.exit_code != 0 and "session_expires_at" in result.fatal_error
    assert not host.status(value["session_id"])["tasks"]


def test_long_timeout_cannot_outlive_host_stop(live_binding, monkeypatch):
    from argus.adapters.dots_backend import DotsBackend, DotsBridgeError
    from argus.core.models import RunnerOptions

    path, value, host, owner = live_binding
    value["request_timeout_seconds"] = 900
    path.write_text(json.dumps(value))
    binding = require_configured_dots_runtime("dots")
    original_submit = binding.transport.submit

    def stop_before_result(request):
        original_submit(request)
        action = host.next(value["session_id"], **owner)
        host.bind(value["session_id"], request.request_id, worker_id="fixture-worker",
                  worker_task=action["claim"]["worker_task"], **owner)
        host.stop(value["session_id"], **owner)
        with pytest.raises(DotsBridgeError, match="late result"):
            host.record(value["session_id"], request.request_id, worker_id="fixture-worker",
                        worker_status="completed", turn_request_id=request.request_id,
                        kind="completed", text="ARGUS_DOTS_CALL:" + request.request_id + "\nlate result", **owner)
        host.record(value["session_id"], request.request_id, worker_id="fixture-worker",
                    worker_status="interrupted", turn_request_id=request.request_id,
                    kind="cancelled", text="host observed stopped worker", **owner)

    monkeypatch.setattr(binding.transport, "submit", stop_before_result)
    result = DotsBackend(binding.transport, timeout_seconds=binding.request_timeout_seconds,
                         execution_profile=binding.execution_profile).run_exec(
        prompt="fixture", run_label="fixture", options=RunnerOptions())
    assert result.exit_code == 130 and "host confirmed cancellation" in result.fatal_error
    assert "late result" not in result.last_agent_message


def test_configured_budget_accepts_completion_after_legacy_300_seconds(live_binding, host_clock, monkeypatch, tmp_path):
    import argparse

    from argus.apps._runtime_construction import build_life_runner
    from argus.core.models import RunnerOptions

    path, value, host, owner = live_binding
    value["request_timeout_seconds"] = 900
    path.write_text(json.dumps(value))
    runner = build_life_runner(argparse.Namespace(backend="dots", workdir=value["project_root"],
                                                skills_dir=str(tmp_path / "skills")))
    backend = runner.engineer_backend
    original_submit = backend.transport.submit
    submitted = []

    def delayed_completion(request):
        original_submit(request)
        submitted.append(request)
        action = host.next(value["session_id"], **owner)
        host.bind(value["session_id"], request.request_id, worker_id="fixture-worker",
                  worker_task=action["claim"]["worker_task"], **owner)
        host_clock.sleep(301)  # deterministic observation; no native worker or model call
        host.record(value["session_id"], request.request_id, worker_id="fixture-worker",
                    worker_status="completed", turn_request_id=request.request_id,
                    kind="completed", text="ARGUS_DOTS_CALL:" + request.request_id + "\nlong fixture completed", **owner)

    monkeypatch.setattr(backend.transport, "submit", delayed_completion)
    result = backend.run_exec(prompt="long fixture", run_label="fixture", options=RunnerOptions())
    request = submitted[0]
    assert result.exit_code == 0 and result.last_agent_message == "long fixture completed"
    assert request.created_at + 300 < result.completed_at < request.expires_at
    assert request.expires_at == host.status(value["session_id"])["session"]["expires_at"]
    assert backend.timeout_seconds == 900


def test_explicit_lease_profile_is_bound_to_session_and_config_identity(live_binding):
    from dataclasses import replace

    from argus.adapters.dots_supervised import (
        SupervisedDotsRoleHost,
        SupervisedRoleFileDotsTransport,
    )
    from argus.core.dots_profile import SupervisedDotsProfile
    from argus.core.runtime_backend import DOTS_ROLES

    path, value, _, owner = live_binding
    policy = replace(SupervisedDotsProfile.from_dict(value["execution_profile"]), lease_duration_seconds=90)
    transport = SupervisedRoleFileDotsTransport(value["bridge_dir"], profile=policy)
    host = SupervisedDotsRoleHost(transport)
    session = host.create(parent_task="/root", coordinator_task=owner["coordinator_task"],
                          producer_id=value["producer_id"], project_root=value["project_root"],
                          mission_id="explicit-lease-fixture", allowed_roles=list(DOTS_ROLES), max_requests=8)
    host.heartbeat(session["session_id"], **owner)
    value.update(session_id=session["session_id"], execution_profile=policy.to_dict())
    path.write_text(json.dumps(value))
    binding = require_configured_dots_runtime("dots")
    assert binding.execution_profile.lease_duration_seconds == 90
    assert binding.transport.assert_ready()["lease_duration_seconds"] == 90
    before = binding_configuration_key()
    value["execution_profile"]["lease_duration_seconds"] = 60
    path.write_text(json.dumps(value))
    assert binding_configuration_key() != before
    with pytest.raises(ValueError, match="binding changed"):
        binding.transport.assert_ready()
    with pytest.raises(RuntimeBackendUnavailable, match="profile does not match"):
        require_configured_dots_runtime("dots")
    assert host.status(session["session_id"])["session"]["execution_profile"] == policy.to_dict()


@pytest.mark.parametrize("duration", [0, -1, True, "90", None, 90.0, 91])
def test_invalid_lease_profile_config_fails_before_transport(live_binding, monkeypatch, duration):
    from argus.adapters import dots_supervised

    path, value, host, _ = live_binding
    value["execution_profile"]["lease_duration_seconds"] = duration
    path.write_text(json.dumps(value))
    monkeypatch.setattr(dots_supervised, "SupervisedRoleFileDotsTransport",
                        lambda *a, **k: pytest.fail("invalid profile must not construct transport"))
    with pytest.raises(RuntimeBackendUnavailable, match="lease_duration_seconds|exact versioned"):
        require_configured_dots_runtime("dots")
    assert not host.status(value["session_id"])["tasks"]
