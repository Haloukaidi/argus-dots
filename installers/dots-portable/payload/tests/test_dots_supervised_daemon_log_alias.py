"""Real daemon log creation, controlled guard fixtures; no native model calls."""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from argus.adapters.dots_supervised_guard import SupervisedReviewGuard
from argus.core.daemon_log_alias import created_daemon_log_aliases
from argus.core.models import RunnerOptions
from argus.daemon.state import _daemon_log_path, _point_active_daemon_log
from argus.reviewer.review_file import ReviewFileStore

pytestmark = pytest.mark.skipif(os.name != "posix", reason="protocol-4 native host is POSIX only")


def daemon_fixture(tmp_path, *, create_target=True):
    project = tmp_path / "project"
    project.mkdir()
    (project / "candidate.py").write_text("candidate = 1\n")
    state = tmp_path / "state"
    state.mkdir()
    target = _daemon_log_path(state, boot_id="fixture-boot")
    # This is the production helper and ordering: alias first, then log open.
    _point_active_daemon_log(state, target)
    if create_target:
        target.parent.mkdir()
        target.write_text("boot started\nengineer completed\n")
    options = RunnerOptions(working_dir=str(project), add_dirs=[str(state)],
                            sandbox_mode="read-only")
    return project, state, target, options


def guard_for(options):
    return SupervisedReviewGuard(options, host_aliases=created_daemon_log_aliases())


def test_original_daemon_alias_reproduces_old_failure_and_explicit_provenance_fixes_it(tmp_path):
    _, state, target, options = daemon_fixture(tmp_path)
    with pytest.raises(ValueError, match="regular, non-linked file.*daemon.log"):
        SupervisedReviewGuard(options).prepare()
    guard = guard_for(options)
    guard.prepare()
    guard.verify()
    assert (state / "daemon.log").read_text() == target.read_text()
    assert target in guard._baseline and state / "daemon.log" in guard._baseline
    assert guard._baseline[target].digest is not None


def test_original_helper_can_register_before_target_and_directory_exist(tmp_path):
    _, state, target, options = daemon_fixture(tmp_path, create_target=False)
    assert (state / "daemon.log").is_symlink() and not target.parent.exists()
    descriptor = next(alias for alias in created_daemon_log_aliases() if alias.path == state / "daemon.log")
    assert descriptor.target == target
    target.parent.mkdir()
    target.write_text("opened after alias creation")
    guard = guard_for(options)
    guard.prepare()
    guard.verify()


def test_provenance_is_inherited_by_original_posix_fork(tmp_path):
    _, _, _, options = daemon_fixture(tmp_path)
    pid = os.fork()
    if pid == 0:
        try:
            guard = guard_for(options)
            guard.prepare()
            guard.verify()
            os._exit(0)
        except Exception:
            os._exit(1)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0


def test_preplanted_same_name_shape_and_target_has_no_host_provenance(tmp_path):
    project = tmp_path / "project"
    target = project / "daemons" / "boot-forged.log"
    target.parent.mkdir(parents=True)
    target.write_text("forged")
    (project / "daemon.log").symlink_to("daemons/boot-forged.log")
    with pytest.raises(ValueError, match="regular, non-linked"):
        guard_for(RunnerOptions(working_dir=str(project))).prepare()


@pytest.mark.parametrize("change", ["retarget", "delete", "recreate", "regular", "host_refresh"])
def test_link_integrity_drift_blocks_terminal_verification(tmp_path, change):
    _, state, target, options = daemon_fixture(tmp_path)
    guard = guard_for(options)
    guard.prepare()
    alias = state / "daemon.log"
    original_text = os.readlink(alias)
    if change == "host_refresh":
        second = target.with_name("boot-second.log")
        second.write_text("second daemon")
        _point_active_daemon_log(state, second)
    else:
        alias.unlink()
        if change == "retarget":
            alias.symlink_to("../outside")
        elif change == "recreate":
            alias.symlink_to(original_text)
        elif change == "regular":
            alias.write_text(target.read_text())
    with pytest.raises(ValueError, match="verification failed"):
        guard.verify()
    # An updated registry is never adopted by the already-prepared guard.
    if change == "host_refresh":
        assert guard._aliases[alias].target == target


@pytest.mark.parametrize("change", ["append", "truncate", "rewrite", "replace", "hardlink", "symlink", "delete", "parent_swap"])
def test_target_evidence_is_never_excluded_as_live_telemetry(tmp_path, change):
    _, state, target, options = daemon_fixture(tmp_path)
    guard = guard_for(options)
    guard.prepare()
    if change == "append":
        with target.open("a") as handle:
            handle.write("legitimate host progress is still observable drift\n")
    elif change == "truncate":
        target.write_text("")
    elif change == "rewrite":
        target.write_text("rewritten evidence\n")
    elif change == "replace":
        replacement = state / "replacement"
        replacement.write_text(target.read_text())
        replacement.replace(target)
    elif change == "hardlink":
        os.link(target, state / "alias-copy")
    elif change == "symlink":
        other = state / "other.log"
        other.write_text("different")
        target.unlink()
        target.symlink_to(other)
    elif change == "delete":
        target.unlink()
    else:
        moved = state / "old-daemons"
        target.parent.rename(moved)
        target.parent.symlink_to(moved, target_is_directory=True)
    with pytest.raises(ValueError, match="verification failed"):
        guard.verify()


def test_target_outside_bound_roots_grants_no_scope(tmp_path):
    project, state, _, options = daemon_fixture(tmp_path)
    outside = tmp_path / "outside.log"
    outside.write_text("not part of authorized evidence")
    _point_active_daemon_log(state, outside)
    with pytest.raises(ValueError, match="outside protected evidence"):
        guard_for(options)
    assert outside.read_text() == "not part of authorized evidence"
    assert (project / "candidate.py").read_text() == "candidate = 1\n"


@pytest.mark.parametrize("which", ["report", "receipt"])
def test_alias_to_permitted_output_is_not_an_evidence_exception(tmp_path, which):
    project, state, _, options = daemon_fixture(tmp_path)
    (project / "paper").mkdir()
    report = project / "paper" / "REVIEW.md"
    receipt = project / "receipt.json"
    report.write_text("report")
    receipt.write_text("receipt")
    options.review_output = {"path": str(report), "receipt": str(receipt)}
    _point_active_daemon_log(state, report if which == "report" else receipt)
    with pytest.raises(ValueError, match="overlaps report output"):
        SupervisedReviewGuard(options, host_aliases=created_daemon_log_aliases(),
                              review_store_factory=ReviewFileStore)


def test_failed_original_creator_does_not_record_a_link_grant(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    target = _daemon_log_path(state, boot_id="failure")
    target.parent.mkdir()
    target.write_text("existing evidence")
    original = os.symlink
    def denied(*args, **kwargs):
        raise OSError("fixture original creator cannot create link")
    monkeypatch.setattr(os, "symlink", denied)
    _point_active_daemon_log(state, target)
    assert not any(alias.path == state / "daemon.log" for alias in created_daemon_log_aliases())
    original("daemons/boot-failure.log", state / "daemon.log")
    with pytest.raises(ValueError, match="regular, non-linked"):
        guard_for(RunnerOptions(working_dir=str(state))).prepare()


def test_descriptor_parent_swap_before_prepare_is_rejected(tmp_path):
    _, state, target, options = daemon_fixture(tmp_path)
    descriptors = created_daemon_log_aliases()
    moved = state.with_name("moved-state")
    state.rename(moved)
    state.mkdir()
    target.parent.mkdir()
    target.write_text("copied evidence")
    (state / "daemon.log").symlink_to("daemons/boot-fixture-boot.log")
    with pytest.raises(ValueError, match="alias changed"):
        SupervisedReviewGuard(options, host_aliases=descriptors).prepare()


def test_target_alias_chains_fail_even_when_each_alias_has_host_provenance(tmp_path):
    project, state, _, options = daemon_fixture(tmp_path)
    _point_active_daemon_log(project, state / "daemon.log")
    with pytest.raises(ValueError, match="alias chains"):
        guard_for(options)


def test_alias_does_not_change_declared_snapshot_budget_or_permissions(tmp_path, monkeypatch):
    from argus.adapters import dots_supervised_guard
    _, _, target, options = daemon_fixture(tmp_path)
    permissions = target.stat().st_mode
    monkeypatch.setattr(dots_supervised_guard, "SNAPSHOT_MAX_BYTES", 1)
    with pytest.raises(ValueError, match="budget exceeded"):
        guard_for(options).prepare()
    assert target.stat().st_mode == permissions


def test_target_is_hashed_once_even_when_directory_traversal_reaches_it(tmp_path, monkeypatch):
    from argus.adapters import dots_supervised_guard
    _, _, target, options = daemon_fixture(tmp_path)
    observed = []
    original = dots_supervised_guard._file
    def read(path, **kwargs):
        observed.append(path)
        return original(path, **kwargs)
    monkeypatch.setattr(dots_supervised_guard, "_file", read)
    guard_for(options).prepare()
    assert observed.count(target) == 1


def test_no_worker_json_or_prose_can_supply_a_descriptor(tmp_path):
    _, state, _, options = daemon_fixture(tmp_path)
    with pytest.raises(ValueError, match="exact host-owned alias descriptor"):
        SupervisedReviewGuard(options, host_aliases=({"path": str(state / "daemon.log")},))
    descriptor = next(alias for alias in created_daemon_log_aliases() if alias.path == state / "daemon.log")
    with pytest.raises(ValueError, match="absolute without parent traversal"):
        SupervisedReviewGuard(options, host_aliases=(replace(descriptor, target=Path("relative.log")),))


@pytest.mark.parametrize("change", [None, "host_append", "retarget"])
def test_real_protocol4_reviewer_publication_and_typed_terminal_guard(tmp_path, monkeypatch, change):
    import time
    from concurrent.futures import ThreadPoolExecutor

    from argus.adapters.dots_backend import DotsBackend
    from argus.reviewer.tools import ReviewActions
    from tests.test_dots_supervised import OWNER, reviewer_fixture_state, settled_review_tool, setup

    bound, host, sid = setup(tmp_path)
    state = Path(bound.project_root) / "state"
    state.mkdir()
    target = _daemon_log_path(state, boot_id="protocol-fixture")
    _point_active_daemon_log(state, target)
    target.parent.mkdir()
    target.write_text("original real daemon log mechanism\n")
    host.heartbeat(sid, **OWNER)
    backend = DotsBackend(bound, role="reviewer", execution_profile=bound.execution_profile,
                          timeout_seconds=5, poll_interval=0.001)
    actions = ReviewActions()

    def producer():
        with backend.bind_role_tools(actions.tools, actions.dispatch):
            return backend.run_exec(prompt="Controlled review fixture, no native model", run_label="reviewer",
                options=RunnerOptions(working_dir=bound.project_root, add_dirs=[str(state)],
                                      sandbox_mode="read-only", reasoning_effort="high"))

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(producer)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            action = host.next(sid, **OWNER)
            if action["action"] == "spawn":
                break
            if future.done():
                pytest.fail("registered original log alias still blocked publication: " + str(future.result().fatal_error))
            time.sleep(0.001)
        else:
            pytest.fail("review fixture request was not published")
        rid = action["request_id"]
        assert "host_aliases" not in bound.inspect(rid)["request"]
        assert "host_aliases" not in bound.inspect(rid)["request"]["options"]
        binding = host.bind(sid, rid, worker_id="fixture-reviewer", worker_task=action["claim"]["worker_task"], **OWNER)
        assert binding["action"] == "bound", reviewer_fixture_state(host, sid, rid, future)
        settled_review_tool(monkeypatch, bound, host, sid, rid, future, deadline,
            worker_id="fixture-reviewer", call_id="fixture-approve", name="approve_review",
            arguments={"review": "Original typed review action"})
        assert actions.decision.status == "done"
        if change == "host_append":
            with target.open("a") as handle:
                handle.write("legitimate concurrent log append still blocks acceptance\n")
        elif change == "retarget":
            (state / "daemon.log").unlink()
            (state / "daemon.log").symlink_to("../another.log")
        host.record(sid, rid, turn_request_id=rid, kind="completed", worker_id="fixture-reviewer",
                    worker_status="completed", text=f"ARGUS_DOTS_CALL:{rid}\nfixture complete", **OWNER)
        result = future.result(timeout=1)
    if change is None:
        assert result.exit_code == 0
        assert bound.inspect(rid)["closed"] == {"status": "consumed"}
    else:
        assert result.exit_code != 0 and "verification failed" in result.fatal_error
