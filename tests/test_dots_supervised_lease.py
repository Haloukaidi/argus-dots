"""Explicit protocol-4 lease duration, with no native worker or timer."""
from __future__ import annotations

import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from argus.adapters import dots_admission, dots_coordinator, dots_file_transport, dots_supervised
from argus.adapters.dots_backend import DotsBridgeError
from argus.core.dots_profile import SupervisedDotsProfile
from tests.test_dots_supervised import OWNER, profile, setup


@pytest.fixture
def clock(monkeypatch):
    now = [time.time()]
    fake = SimpleNamespace(time=lambda: now[0], monotonic=time.monotonic, sleep=time.sleep)
    for module in (dots_admission, dots_coordinator, dots_file_transport, dots_supervised):
        monkeypatch.setattr(module, "time", fake)
    return now


def lease_record(host, sid):
    with host.transport._root_fd() as root:
        return host.transport._read(root, host._lease_name(sid))


def write_lease(host, sid, value):
    with host.transport._root_fd() as root:
        host.transport._write(root, host._lease_name(sid), value, replace=True)


def test_old_profile_encoding_and_default_stay_unchanged(tmp_path, clock):
    policy = profile()
    legacy = policy.to_dict()
    assert "lease_duration_seconds" not in legacy
    assert SupervisedDotsProfile.from_dict(legacy).to_dict() == legacy
    bound, host, sid = setup(tmp_path, policy=policy)
    lease = host.heartbeat(sid, **OWNER)
    assert lease["expires_at"] - lease["observed_at"] == 30
    assert bound.assert_ready()["lease_duration_seconds"] == 30
    assert host.status(sid)["session"]["execution_profile"] == legacy


@pytest.mark.parametrize("duration", [1, 30, 60, 90])
def test_explicit_profile_duration_survives_heartbeat_next_and_recreated_host(tmp_path, clock, duration):
    policy = replace(profile(), lease_duration_seconds=duration)
    assert SupervisedDotsProfile.from_dict(policy.to_dict()) == policy
    bound, host, sid = setup(tmp_path, policy=policy)
    host.heartbeat(sid, **OWNER)
    for renew in (lambda: host.heartbeat(sid, **OWNER), lambda: host.next(sid, **OWNER),
                  lambda: bound._host().heartbeat(sid, **OWNER)):
        clock[0] += 0.5
        renew()
        saved = lease_record(host, sid)
        assert saved["lease_duration_seconds"] == duration
        assert saved["expires_at"] - saved["observed_at"] == duration
    assert host.heartbeat(sid, lease_seconds=duration, **OWNER)["lease_duration_seconds"] == duration
    before = lease_record(host, sid)
    with pytest.raises(DotsBridgeError, match="supervised lease"):
        host.heartbeat(sid, lease_seconds=30 if duration != 30 else 60, **OWNER)
    assert lease_record(host, sid) == before


@pytest.mark.parametrize("value", [0, -1, 91, True, False, "90", 90.0, float("nan"), float("inf")])
def test_invalid_explicit_duration_rejects_before_host_creation(value):
    with pytest.raises(ValueError, match="lease_duration_seconds"):
        replace(profile(), lease_duration_seconds=value)
    raw = profile().to_dict()
    raw["lease_duration_seconds"] = value
    with pytest.raises(ValueError, match="lease_duration_seconds"):
        SupervisedDotsProfile.from_dict(raw)


def test_explicit_null_does_not_silently_become_legacy_profile():
    raw = profile().to_dict()
    raw["lease_duration_seconds"] = None
    with pytest.raises(ValueError, match="exact versioned"):
        SupervisedDotsProfile.from_dict(raw)


@pytest.mark.parametrize("duration", [1, 30, 60])
def test_legacy_explicit_duration_persists_for_same_owner_after_lease_expiry(tmp_path, clock, duration):
    bound, host, sid = setup(tmp_path)
    host.heartbeat(sid, lease_seconds=duration, **OWNER)
    clock[0] += duration + 1
    with pytest.raises(DotsBridgeError, match="stale"):
        bound.assert_ready()
    host.next(sid, **OWNER)
    assert bound.assert_ready()["lease_duration_seconds"] == duration
    clock[0] += 1
    assert host.heartbeat(sid, **OWNER)["lease_duration_seconds"] == duration
    before = lease_record(host, sid)
    with pytest.raises(DotsBridgeError, match="legacy 1..60"):
        host.heartbeat(sid, lease_seconds=90, **OWNER)
    assert lease_record(host, sid) == before


def test_metadata_less_old_lease_remains_readable_and_renews_conservatively(tmp_path, clock):
    bound, host, sid = setup(tmp_path)
    legacy = host.heartbeat(sid, lease_seconds=60, **OWNER)
    del legacy["lease_duration_seconds"]
    write_lease(host, sid, legacy)
    assert bound.assert_ready()["expires_at"] == legacy["expires_at"]
    clock[0] += 61
    assert host.heartbeat(sid, **OWNER)["lease_duration_seconds"] == 30


@pytest.mark.parametrize("duration", [None, 90])
def test_handoff_keeps_explicit_profile_and_resets_legacy_selection(tmp_path, clock, duration):
    bound, host, sid = setup(tmp_path, policy=replace(profile(), lease_duration_seconds=duration))
    host.heartbeat(sid, **({"lease_seconds": 60} if duration is None else {}), **OWNER)
    host.handoff(sid, parent_task="/root", previous_generation=1, coordinator_task="/root/replacement")
    with pytest.raises(DotsBridgeError, match="stale|invalid"):
        bound.assert_ready()
    new_owner = {"coordinator_task": "/root/replacement", "generation": 2}
    lease = host.heartbeat(sid, **new_owner)
    assert lease["lease_duration_seconds"] == (duration or 30)
    with pytest.raises(DotsBridgeError):
        host.heartbeat(sid, **OWNER)


@pytest.mark.parametrize("changes", [
    {"generation": 2}, {"generation": True}, {"coordinator_task": "/root/wrong"},
    {"lease_duration_seconds": 90}, {"lease_duration_seconds": True},
    {"lease_duration_seconds": "60"}, {"lease_duration_seconds": 0},
    {"lease_duration_seconds": None}, {"unexpected": 1}, {"session_id": "f" * 32},
])
def test_malformed_or_wrong_owner_lease_is_never_adopted_by_pulse(tmp_path, clock, changes):
    _, host, sid = setup(tmp_path)
    saved = host.heartbeat(sid, **OWNER)
    saved.update(changes)
    write_lease(host, sid, saved)
    with pytest.raises(DotsBridgeError):
        host.heartbeat(sid, **OWNER)
    assert lease_record(host, sid) == saved


def test_selected_90_is_capped_to_original_session_and_never_renews_expired(tmp_path, clock):
    bound, host, sid = setup(tmp_path, policy=replace(profile(), lease_duration_seconds=90))
    deadline = host.status(sid)["session"]["expires_at"]
    clock[0] = deadline - 2
    saved = host.heartbeat(sid, **OWNER)
    assert saved["lease_duration_seconds"] == 90 and saved["expires_at"] == deadline
    assert bound.assert_ready()["session_expires_at"] == deadline
    clock[0] += 1
    host.next(sid, **OWNER)
    assert lease_record(host, sid)["expires_at"] == deadline
    clock[0] = deadline
    before = lease_record(host, sid)
    with pytest.raises(DotsBridgeError, match="expired"):
        host.heartbeat(sid, **OWNER)
    assert host.next(sid, **OWNER)["action"] == "done"
    assert lease_record(host, sid) == before
    assert host.status(sid)["session"]["expires_at"] == deadline


def test_stopped_host_cannot_pulse_but_next_and_status_still_work(tmp_path, clock):
    _, host, sid = setup(tmp_path, policy=replace(profile(), lease_duration_seconds=90))
    host.heartbeat(sid, **OWNER)
    host.stop(sid, **OWNER)
    before = lease_record(host, sid)
    with pytest.raises(DotsBridgeError, match="stopped"):
        host.heartbeat(sid, **OWNER)
    assert host.next(sid, **OWNER)["action"] == "done"
    assert lease_record(host, sid) == before
    assert host.status(sid)["session"]["stopping"]


@pytest.mark.parametrize("duration", [None, 90])
def test_cli_omitted_heartbeat_and_next_preserve_selected_duration(tmp_path, capsys, clock, duration):
    from argus.apps.dots_supervised import main

    policy = replace(profile(), lease_duration_seconds=duration)
    bound, host, sid = setup(tmp_path, policy=policy)
    profile_file = tmp_path / "profile.json"
    profile_file.write_text(json.dumps(policy.to_dict()))
    common = ["--bridge-dir", str(bound.root), "--profile-file", str(profile_file)]
    owner = [sid, "--owner", OWNER["coordinator_task"], "--generation", "1"]
    if duration is None:
        assert main([*common, "heartbeat", *owner, "--lease-seconds", "60"]) == 0
        capsys.readouterr()
    for command in ("heartbeat", "next", "heartbeat"):
        assert main([*common, command, *owner]) == 0
        capsys.readouterr()
        assert lease_record(host, sid)["lease_duration_seconds"] == (duration or 60)
    if duration is not None:
        assert main([*common, "heartbeat", *owner, "--lease-seconds", "30"]) == 1
        assert "conflicts" in capsys.readouterr().err


def test_extended_lease_does_not_change_strict_or_protocol3_permissions(tmp_path, clock):
    from argus.adapters.dots_admission import BoundedDotsRoleHost, BoundedRoleFileDotsTransport
    from argus.adapters.dots_backend import DotsBackend
    from argus.core.models import RunnerOptions

    bound, host, sid = setup(tmp_path, policy=replace(profile(), lease_duration_seconds=90))
    host.heartbeat(sid, **OWNER)
    assert bound.capabilities.options == frozenset()
    result = DotsBackend(bound).run_exec(prompt="fixture", run_label="fixture",
                                        options=RunnerOptions(disable_tools=True))
    assert result.exit_code != 0 and not host.status(sid)["tasks"]
    old = BoundedDotsRoleHost(BoundedRoleFileDotsTransport(bound.root))
    assert not hasattr(old, "heartbeat")
    with pytest.raises(DotsBridgeError):
        old.status(sid)
