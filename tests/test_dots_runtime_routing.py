"""Explicit runtime selection is never an authorization for a CLI fallback."""
from __future__ import annotations

import argparse
from dataclasses import replace

import pytest

from argus.adapters.dots_backend import DotsCapabilities
from argus.core.runtime_backend import (
    DOTS_REQUIRED_OPTIONS,
    DOTS_ROLES,
    RUNTIME_BACKENDS,
    RuntimeBackendUnavailable,
    dots_readiness_problems,
    normalize_runtime_backend,
    require_dots_runtime,
)


def forbidden(*args, **kwargs):
    pytest.fail("dots must not construct, resolve, or dispatch an ordinary CLI")


class CapableFixtureHost:
    """Fixture contract only; not evidence of native platform enforcement."""
    capabilities = DotsCapabilities(
        options=frozenset().union(*DOTS_REQUIRED_OPTIONS.values()),
        role_tools=True, resume=True,
    )

    def submit(self, request):
        pytest.fail("routing/readiness must not submit a role request")


def test_runtime_name_does_not_register_a_tenth_cli():
    from argus.agent_cli.runner_backend import SUPPORTED_BACKENDS, normalize_runner_backend

    assert RUNTIME_BACKENDS == (*SUPPORTED_BACKENDS, "dots")
    assert "dots" not in SUPPORTED_BACKENDS
    assert normalize_runtime_backend(" DOTS ") == "dots"
    assert normalize_runtime_backend(None) == "codex"
    assert normalize_runtime_backend("opencod") == "opencode"
    with pytest.raises(ValueError):
        normalize_runner_backend("dots")
    with pytest.raises(ValueError):
        normalize_runtime_backend("dotss")


def test_existing_backends_do_not_require_native_readiness():
    from argus.agent_cli.runner_backend import SUPPORTED_BACKENDS

    for name in SUPPORTED_BACKENDS:
        assert require_dots_runtime(name, env={}) is False


def test_unconfigured_host_is_not_ready():
    with pytest.raises(RuntimeBackendUnavailable, match="no native receiving transport"):
        require_dots_runtime("dots", env={})


def test_file_host_controls_are_not_upgraded(tmp_path):
    from argus.adapters.dots_role_host import RoleFileDotsTransport

    transport = RoleFileDotsTransport(tmp_path / "queue")
    assert transport.capabilities.options == frozenset()
    problems = dots_readiness_problems(transport)
    assert set(problems) == set(DOTS_ROLES)
    assert "disable_tools" in problems["manager"]
    assert "sandbox_mode:read-only" in problems["planner"]
    assert "review_output" in problems["reviewer"]
    with pytest.raises(RuntimeBackendUnavailable):
        require_dots_runtime("dots", transport=transport, env={})
    assert not list((tmp_path / "queue").iterdir())


def test_readiness_reports_absent_roles_and_bound_tools():
    host = CapableFixtureHost()
    host.capabilities = replace(host.capabilities, roles=frozenset({"reviewer"}), role_tools=False)
    problems = dots_readiness_problems(host)
    assert "role is not supported" in problems["manager"]
    assert "call-bound role_tools" in problems["reviewer"]


@pytest.mark.parametrize("env", [
    {"ARGUS_SKILL_ENGINEER_BACKEND": "codex"},
    {"ARGUS_SKILL_RUNNER_BACKEND": "claude"},
])
def test_explicit_dots_does_not_accept_conflicting_cli_overrides(env):
    with pytest.raises(RuntimeBackendUnavailable, match="all five roles"):
        require_dots_runtime("dots", transport=CapableFixtureHost(), env=env)


def test_role_only_dots_selection_fails_closed_before_cli_factory(tmp_path, monkeypatch):
    from argus.apps._runtime_construction import build_life_runner

    monkeypatch.setenv("ARGUS_SKILL_REVIEWER_BACKEND", "dots")
    monkeypatch.setattr("argus.adapters.agent_cli_backend.AgentCliBackend", forbidden)
    with pytest.raises(RuntimeBackendUnavailable, match="all five roles"):
        build_life_runner(argparse.Namespace(backend="codex"))


@pytest.mark.parametrize("selection", ["argument", "environment", "persisted"])
def test_shared_factory_routes_all_roles_without_cli(tmp_path, monkeypatch, selection):
    from argus.apps._runtime_construction import build_life_runner
    from argus.core.knob_store import write_persisted_knobs

    backend = "dots" if selection == "argument" else "codex"
    if selection == "environment":
        monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "dots")
    if selection == "persisted":
        assert write_persisted_knobs({"ARGUS_SKILL_RUNNER_BACKEND": "dots"})
    monkeypatch.setattr("argus.adapters.agent_cli_backend.AgentCliBackend", forbidden)
    monkeypatch.setattr("argus.agent_cli.runner_backend.resolve_available_runner", forbidden)
    args = argparse.Namespace(backend=backend, workdir=str(tmp_path),
                              skills_dir=str(tmp_path / "skills"), dots_transport=CapableFixtureHost())
    runner = build_life_runner(args)
    assert args.backend == backend
    assert runner.manager.runner is runner.manager_backend
    for role in DOTS_ROLES:
        assert getattr(runner, role + "_backend").role == role
        assert getattr(runner, role + "_backend").backend == "dots"


def test_shared_factory_missing_host_refuses_before_cli(monkeypatch):
    from argus.apps._runtime_construction import build_life_runner

    monkeypatch.setattr("argus.adapters.agent_cli_backend.AgentCliBackend", forbidden)
    with pytest.raises(RuntimeBackendUnavailable, match="no native receiving transport"):
        build_life_runner(argparse.Namespace(backend="dots"))


def test_cli_parser_accepts_explicit_dots_without_execution():
    from argus.apps.cli._parser import build_parser

    assert build_parser().parse_args(["--backend", "dots", "--daemon-fg"]).backend == "dots"


def test_persisted_switch_clears_cli_binary_and_renders_native(monkeypatch):
    from argus.core.knob_store import read_persisted_knobs, write_persisted_knobs
    from argus.core.knobs import normalize_cockpit_knob_value
    from argus.core.role_config import resolve_all_roles, runner_backend_label

    assert write_persisted_knobs({"ARGUS_SKILL_RUNNER_BACKEND": "codex", "ARGUS_SKILL_RUNNER_BIN": "/old/codex"})
    value = normalize_cockpit_knob_value("ARGUS_SKILL_RUNNER_BACKEND", "DOTS")
    assert write_persisted_knobs({"ARGUS_SKILL_RUNNER_BACKEND": value})
    assert read_persisted_knobs()["ARGUS_SKILL_RUNNER_BIN"] == ""
    monkeypatch.setattr("argus.agent_cli.runner_backend.resolve_available_runner", forbidden)
    assert {role.backend for role in resolve_all_roles()} == {"dots"}
    assert runner_backend_label() == "dots"


def test_web_chat_state_keeps_explicit_dots(monkeypatch):
    from argus.webapi import manager_state

    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "dots")
    monkeypatch.setattr(manager_state, "_STATES", {})
    assert manager_state._chat_state_for("s-dots-runtime")["backend"] == "dots"


def test_readiness_does_not_probe_cli_or_credentials(monkeypatch):
    from argus.core import backend_readiness

    monkeypatch.setattr(backend_readiness, "resolve_runner_bin", forbidden)
    monkeypatch.setattr(backend_readiness, "_run_text", forbidden)
    report = backend_readiness.check_backend_readiness("dots")
    assert report.profile.backend == "dots" and not report.ok
    assert report.executable == "" and report.auth_checked is False
    assert "native receiving transport" in report.problems[0].detail


@pytest.mark.parametrize("route", ["message", "message/stream"])
def test_web_submission_rejected_before_manager_transcript_or_daemon(tmp_path, monkeypatch, route):
    from fastapi.testclient import TestClient

    from argus.webapi.daemon_lifecycle import create_daemon
    from argus.webapi.server import create_app

    root = tmp_path / "state"
    created = create_daemon(name="fixture idle", global_root=root)
    sid = created["sid"]
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "dots")
    monkeypatch.setattr("argus.webapi.manager_bridge.manager_message", forbidden)
    monkeypatch.setattr("argus.adapters.agent_cli_backend.AgentCliBackend", forbidden)
    with TestClient(create_app(global_root=root)) as client:
        result = client.post(f"/api/projects/{sid}/{route}", json={"text": "Exact unsent fixture prompt"})
    assert result.status_code == 503
    assert "dots runtime unavailable" in result.json()["detail"]
    assert not (root / "projects" / sid / "transcript.jsonl").exists()


def test_web_daemon_start_refuses_without_spawn(tmp_path, monkeypatch):
    from argus.webapi import daemon_lifecycle

    root = tmp_path / "state"
    created = daemon_lifecycle.create_daemon(name="fixture idle", global_root=root)
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "dots")
    monkeypatch.setattr(daemon_lifecycle, "_worker_config_from_env", forbidden)
    result = daemon_lifecycle.start_project_daemon(created["sid"], global_root=root)
    assert result["rc"] == 3 and not result["daemon"]["alive"]
    assert "dots runtime unavailable" in result["error"]


@pytest.mark.parametrize("foreground", [False, True])
def test_cli_daemon_preflight_refuses_before_process_start(monkeypatch, capsys, foreground):
    from argus.apps.cli._core import _cmd_daemon_start
    from argus.apps.cli._parser import build_parser

    monkeypatch.setattr("argus.daemon.life_worker.run_foreground", forbidden)
    monkeypatch.setattr("argus.daemon.life_worker.spawn_detached_daemon", forbidden)
    args = build_parser().parse_args(["--backend", "dots"])
    assert _cmd_daemon_start(args, foreground=foreground) == 3
    assert "native receiving transport" in capsys.readouterr().err


def test_shared_dots_factory_preserves_explicit_model_and_effort(tmp_path, monkeypatch):
    from argus.apps._runtime_construction import build_life_runner
    from argus.core.models import RunnerOptions

    class RecordingHost(CapableFixtureHost):
        request = None

        def submit(self, request):
            self.request = request

        def poll(self, request_id, sequence):
            event = ({"type": "accepted", "worker_id": "fixture-worker"} if sequence == 1
                     else {"type": "completed"})
            return {"protocol_version": 1, "request_id": request_id, "sequence": sequence, **event}

        def finish(self, request_id):
            pass

    monkeypatch.setattr("argus.adapters.agent_cli_backend.AgentCliBackend", forbidden)
    host = RecordingHost()
    args = argparse.Namespace(backend="dots", workdir=str(tmp_path),
                              skills_dir=str(tmp_path / "skills"), dots_transport=host)
    runner = build_life_runner(args)
    result = runner.manager_backend.run_exec(prompt="Fixture only", run_label="fixture",
                                            options=RunnerOptions(model="explicit-model", reasoning_effort="xhigh"))
    assert result.exit_code == 0
    assert host.request.model == "explicit-model"
    assert host.request.reasoning_effort == "xhigh"


def test_queued_task_without_autostart_still_refuses_native_unavailable(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from argus.webapi.daemon_lifecycle import create_daemon
    from argus.webapi.server import create_app

    root = tmp_path / "state"
    created = create_daemon(name="fixture idle", global_root=root)
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "dots")
    monkeypatch.setattr("argus.adapters.agent_cli_backend.AgentCliBackend", forbidden)
    with TestClient(create_app(global_root=root)) as client:
        response = client.post(f"/api/projects/{created['sid']}/tasks", json={
            "text": "Unsubmitted fixture task", "autostart_daemon": False,
        })
    assert response.status_code == 503
    assert "dots runtime unavailable" in response.json()["detail"]
    assert not (root / "projects" / created["sid"] / "backlog.jsonl").exists()


def test_web_campaign_refuses_before_creating_a_ghost_project(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from argus.webapi.server import create_app

    root = tmp_path / "state"
    monkeypatch.setenv("ARGUS_SKILL_RUNNER_BACKEND", "dots")
    monkeypatch.setattr("argus.webapi.daemon_lifecycle.create_daemon", forbidden)
    with TestClient(create_app(global_root=root)) as client:
        response = client.post("/api/daemons", json={"objective": "Unsubmitted fixture objective"})
    assert response.status_code == 503
    assert "dots runtime unavailable" in response.json()["detail"]


@pytest.mark.parametrize("flag", ["--daemon", "--daemon-fg"])
def test_real_cli_entry_refuses_native_unavailable(monkeypatch, capsys, flag):
    from argus.apps.cli import main

    monkeypatch.setattr("argus.daemon.life_worker.run_foreground", forbidden)
    monkeypatch.setattr("argus.daemon.life_worker.spawn_detached_daemon", forbidden)
    assert main(["--backend", "dots", flag]) == 3
    output = capsys.readouterr().err
    assert "backend=dots" in output and "ready: no" in output
    assert "native receiving transport" in output


@pytest.mark.parametrize("route", ["plan", "prompt/rewrite"])
def test_real_config_transition_cannot_reuse_warm_cli(tmp_path, monkeypatch, route):
    from pathlib import Path
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from argus.webapi import manager_state
    from argus.webapi.daemon_lifecycle import create_daemon
    from argus.webapi.server import create_app

    root = tmp_path / "state"
    created = create_daemon(name="fixture idle", global_root=root)
    sid = created["sid"]
    state = manager_state._chat_state_for(sid)
    cached = SimpleNamespace(planner_backend=SimpleNamespace(run_exec=forbidden),
                             _backend=SimpleNamespace(run_exec=forbidden))
    state.update(manager_runner=cached, manager_runner_workdir=created["workdir"],
                 last_thread_id="stale-cli-session")
    monkeypatch.setattr("argus.manager.front_door._operator_workspace", lambda *args: Path(created["workdir"]))
    monkeypatch.setattr("argus.adapters.agent_cli_backend.AgentCliBackend", forbidden)
    with TestClient(create_app(global_root=root)) as client:
        changed = client.post(f"/api/projects/{sid}/config/set", json={
            "name": "ARGUS_SKILL_RUNNER_BACKEND", "value": "dots",
        })
        assert changed.status_code == 200
        response = client.post(f"/api/projects/{sid}/{route}", json={"text": "Fixture only"})
    assert response.status_code == 200
    assert "dots runtime unavailable" in response.json()["error"]
    assert state["backend"] == "dots"
    assert state.get("manager_runner") is not cached
    assert state["last_thread_id"] is None


def test_warm_manager_rebuilt_after_actual_model_config_change(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from argus.life.memory import MemoryBundle
    from argus.manager.front_door import _ensure_manager_runner
    from argus.webapi import manager_state
    from argus.webapi.daemon_lifecycle import create_daemon
    from argus.webapi.mission_items import set_operator_config

    root = tmp_path / "state"
    created = create_daemon(name="fixture idle", global_root=root)
    sid = created["sid"]
    mem = MemoryBundle.for_cwd(fingerprint=sid, global_root=root)
    state = manager_state._chat_state_for(sid)
    built = []

    def fixture_factory(args):
        runner = SimpleNamespace()
        built.append(runner)
        return runner

    monkeypatch.setattr("argus.apps._runtime.build_life_runner", fixture_factory)
    first = _ensure_manager_runner(state, mem)
    assert first is not None and _ensure_manager_runner(state, mem) is first
    set_operator_config("ARGUS_SKILL_MODEL", "explicit-model-change", global_root=root)
    second = _ensure_manager_runner(state, mem)
    assert second is not None and second is not first and len(built) == 2


def test_native_setup_does_not_enter_cli_install_or_login(monkeypatch, capsys):
    from argus.apps.cli import main
    from argus.core.backend_readiness import resolve_backend_profile

    monkeypatch.setattr("argus.tools.setup.run_setup", forbidden)
    assert main(["--setup", "--backend", "dots", "--non-interactive"]) == 3
    assert "native receiving transport" in capsys.readouterr().err
    assert resolve_backend_profile("dots", env={}).auth_mode == "native_host"
    # Preserve an explicitly configured auth mode as evidence, without probing it.
    assert resolve_backend_profile("dots", "model_api", env={}).auth_mode == "model_api"
