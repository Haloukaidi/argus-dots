"""Non-CLI host model defaults through real reflection and Curator callers."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from argus.adapters.dots_backend import DotsBackend
from argus.agent_cli.runner_backend import SUPPORTED_BACKENDS, normalize_runner_backend
from argus.core.knobs import resolve_manager_classify_model, resolve_role_model
from argus.core.models import RunnerOptions
from argus.core.run_gateway import run_exec
from argus.daemon.config import LifeWorkerConfig
from argus.daemon.life_worker import LifeWorker
from argus.life.reflection import reflect_after_mission
from tests.test_dots_backend import Host


@pytest.mark.parametrize("role", ["manager", "planner", "engineer", "reviewer", "curator"])
def test_host_default_model_has_no_cli_catalog(role):
    assert resolve_role_model(role, backend="dots", env={}) == ""
    assert resolve_manager_classify_model(backend="dots", env={}) == ""
    assert len(SUPPORTED_BACKENDS) == 9 and "dots" not in SUPPORTED_BACKENDS
    with pytest.raises(ValueError, match="unknown"):
        normalize_runner_backend("dots")


@pytest.mark.parametrize("model", ["custom-explicit", "auto", "inherit", "default"])
def test_explicit_model_precedence_remains_unchanged(model):
    expected = "custom-explicit" if model == "custom-explicit" else ""
    assert resolve_role_model("curator", backend="dots", role_env="ARGUS_SKILL_CURATOR_MODEL",
                              env={"ARGUS_SKILL_CURATOR_MODEL": model}) == expected


def test_persisted_model_and_unknown_backend_remain_checked(monkeypatch):
    monkeypatch.setattr("argus.core.knob_store.read_persisted_knobs",
                        lambda: {"ARGUS_SKILL_CURATOR_MODEL": "persisted-model"})
    assert resolve_role_model("curator", backend="dots", role_env="ARGUS_SKILL_CURATOR_MODEL", env={}) == "persisted-model"
    with pytest.raises(ValueError, match="unknown"):
        resolve_role_model("manager", backend="not-a-backend", env={})


@pytest.mark.parametrize("model", [None, "", "explicit-model"])
def test_gateway_serializes_default_model_as_null_without_changing_effort(model):
    host = Host()
    result = run_exec(DotsBackend(host), prompt="fixture", run_label="default-model",
                      options=RunnerOptions(model=model, reasoning_effort="high"))
    assert result.exit_code == 0
    assert host.requests[0].model == (model or None)
    assert host.requests[0].reasoning_effort == "high"


@pytest.mark.parametrize("model", [False, 0, [], {}, " "])
def test_other_invalid_model_values_still_fail_before_submission(model):
    host = Host()
    result = run_exec(DotsBackend(host), prompt="fixture", run_label="invalid-model",
                      options=RunnerOptions(model=model))
    assert result.exit_code != 0 and "model must be nonempty" in result.fatal_error
    assert not host.requests


def test_actual_reflection_preserves_write_scope_and_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_REFLECTION", "1")
    project = tmp_path / "project"
    project.mkdir()
    life = tmp_path / "life"
    host = Host()
    host.capabilities = replace(host.capabilities, options=frozenset({
        "working_dir", "add_dirs", "sandbox_mode:workspace-write",
    }))
    backend = DotsBackend(host, role="manager")
    kwargs = dict(
        runner=SimpleNamespace(_backend=backend), workspace=project, life_dir=life,
        global_root=tmp_path / "global", vertical="software", project_id="fixture-project",
        mission_id="fixture-mission", title="Verified sample", objective="Verify the sample",
        acceptance="Both cases pass", review_status="done", review_reason="Both cases passed",
        stop_reason="completed", host_round_log="Controlled offline fixture passed",
        run_reality="fixture", emit=lambda *_: None,
    )
    result = reflect_after_mission(**kwargs)
    assert result.get("failure") == "", result
    assert len(host.requests) == 1
    request = host.requests[0]
    assert request.role == "manager" and request.run_label == "reflection"
    assert request.model is None and request.reasoning_effort == "low"
    assert request.options["sandbox_mode"] == "workspace-write"
    assert request.options["working_dir"] == str(project)
    assert request.options["add_dirs"]
    assert reflect_after_mission(**kwargs)["skipped"] == "already reflected on this mission"
    assert len(host.requests) == 1


def test_actual_curator_callback_uses_curator_binding_and_host_default(tmp_path):
    host = Host()
    host.capabilities = replace(host.capabilities, options=frozenset({"working_dir", "full_auto"}))
    backend = DotsBackend(host, role="curator")
    worker = LifeWorker.__new__(LifeWorker)
    worker.config = LifeWorkerConfig(life_dir=tmp_path / "life", project_workdir=tmp_path)
    callback = worker._curator_distill_fn(SimpleNamespace(curator_backend=backend))
    assert callback("Fixture-only curation; do not edit files") == "done"
    assert host.requests[0].role == "curator"
    assert host.requests[0].model is None
    assert host.requests[0].options == {"working_dir": str(tmp_path), "full_auto": True}


def test_missing_execution_capability_still_blocks_before_reflection_dispatch(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_SKILL_REFLECTION", "1")
    host = Host()
    result = reflect_after_mission(
        runner=SimpleNamespace(_backend=DotsBackend(host)), workspace=tmp_path,
        life_dir=tmp_path / "life", global_root=tmp_path / "global", vertical="software",
        project_id="fixture", mission_id="fixture", title="fixture", objective="fixture",
        acceptance="fixture", review_status="done", review_reason="fixture", stop_reason="done",
        host_round_log="fixture", run_reality="fixture", emit=lambda *_: None,
    )
    assert "lacks required execution capabilities" in result["failure"]
    assert not host.requests
