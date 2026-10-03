"""Controlled fixtures: detection only, never native worker or Docker execution."""
from __future__ import annotations

import hashlib
import json
import os

import pytest

from argus.adapters.dots_supervised_guard import SupervisedReviewGuard
from argus.core.models import RunnerOptions
from argus.reviewer.review_file import ReviewFileStore
from argus.reviewer.tools import ReviewActions


def fixture(tmp_path, *, output=True, **kwargs):
    project = tmp_path / "project"
    project.mkdir()
    (project / "paper").mkdir()
    (project / "paper" / "candidate.tex").write_text("original candidate")
    (project / "data.csv").write_text("x,y\n1,2\n")
    tracking = tmp_path / "tracking"
    tracking.mkdir()
    report = project / "paper" / "REVIEW.md"
    receipt = tracking / "written.json"
    options = RunnerOptions(
        working_dir=str(project), sandbox_mode="read-only",
        review_output={"path": str(report), "receipt": str(receipt)} if output else None,
        **kwargs,
    )
    guard = SupervisedReviewGuard(options, review_store_factory=ReviewFileStore)
    return project, report, receipt, options, guard


def prepared(tmp_path, **kwargs):
    result = fixture(tmp_path, **kwargs)
    result[-1].prepare()
    return result


def test_host_report_tools_preserve_original_store_and_typed_decision(tmp_path, monkeypatch):
    project, report, receipt, _, guard = prepared(tmp_path)
    actions = ReviewActions()
    calls = []
    original = ReviewFileStore.write_review

    def write(store, text):
        calls.append((store.path, store.receipt, text))
        return original(store, text)

    monkeypatch.setattr(ReviewFileStore, "write_review", write)
    tools, dispatch = guard.bind_tools(actions.tools, actions.dispatch)
    assert [tool["name"] for tool in tools] == [
        *[tool["name"] for tool in actions.tools], "read_review", "write_review",
    ]
    assert dispatch("read_review", {}) == {"text": ""}
    assert dispatch("write_review", {"text": "First current review"})["path"] == str(report)
    assert actions.decision is None
    dispatch("write_review", {"text": "Updated complete review"})
    assert dispatch("read_review", {}) == {"text": "Updated complete review"}
    assert calls[-1] == (report, receipt, "Updated complete review")
    assert dispatch("approve_review", {"review": "Independent typed judgment"}) == {"recorded": "approve_review"}
    assert actions.decision.status == "done"
    guard.verify()
    assert (project / "paper" / "candidate.tex").read_text() == "original candidate"
    assert ReviewFileStore(str(report), str(receipt)).authored_review() == "Updated complete review"


def test_no_output_keeps_original_dispatch_and_never_parses_prose(tmp_path):
    _, _, _, _, guard = prepared(tmp_path, output=False)
    actions = ReviewActions()
    tools, dispatch = guard.bind_tools(actions.tools, actions.dispatch)
    assert tools is actions.tools
    assert dispatch == actions.dispatch
    guard.verify()
    assert actions.decision is None
    with pytest.raises(ValueError, match="Unknown"):
        dispatch("write_review", {"text": "approved"})


@pytest.mark.parametrize("name,payload", [
    ("read_review", {"path": "/outside"}),
    ("write_review", {"text": "review", "path": "/outside"}),
    ("write_review", {"text": {"status": "done"}}),
    ("write_review", {"text": ""}),
    ("write_review", {"text": "  "}),
    ("write_review", {}),
])
def test_report_tool_payload_is_typed_and_pathless(tmp_path, name, payload):
    _, report, receipt, _, guard = prepared(tmp_path)
    actions = ReviewActions()
    _, dispatch = guard.bind_tools(actions.tools, actions.dispatch)
    with pytest.raises(ValueError):
        dispatch(name, payload)
    assert not report.exists() and not receipt.exists()
    assert actions.decision is None
    # Ordinary rejected tool input is feedback; it does not invent a decision
    # and does not irreversibly poison an otherwise intact review.
    guard.verify()


@pytest.mark.parametrize("change", ["bytes", "create", "delete", "rename", "mode", "symlink", "hardlink", "fifo", "directory"])
def test_candidate_changes_reject_even_after_typed_approval_without_repair(tmp_path, change):
    project, _, _, _, guard = prepared(tmp_path)
    actions = ReviewActions()
    _, dispatch = guard.bind_tools(actions.tools, actions.dispatch)
    dispatch("approve_review", {"review": "typed approval before terminal"})
    candidate = project / "paper" / "candidate.tex"
    if change == "bytes":
        before = candidate.stat()
        candidate.write_text("modified candidate")
        os.utime(candidate, ns=(before.st_atime_ns, before.st_mtime_ns))
    elif change == "create":
        (project / "new-evidence").write_text("fabricated")
    elif change == "delete":
        candidate.unlink()
    elif change == "rename":
        candidate.rename(candidate.with_name("renamed.tex"))
    elif change == "mode":
        candidate.chmod(0o700)
    elif change == "symlink":
        candidate.unlink()
        candidate.symlink_to(project / "data.csv")
    elif change == "hardlink":
        os.link(candidate, project / "alias")
    elif change == "fifo":
        os.mkfifo(project / "pipe")
    else:
        (project / "extra-directory").mkdir()
    with pytest.raises(ValueError, match="supervised review|protected review"):
        guard.verify()
    assert actions.decision.status == "done"  # Caller must reject its RunnerResult.
    if change == "bytes":
        assert candidate.read_text() == "modified candidate"  # No rollback.
    elif change == "delete":
        assert not candidate.exists()
    # Once a violation was observed, subsequent calls cannot bless a restore.
    with pytest.raises(ValueError, match="verification failed"):
        guard.verify()


@pytest.mark.parametrize("which", ["report", "receipt"])
@pytest.mark.parametrize("moment", ["before_first_write", "after_write", "without_write"])
def test_direct_output_changes_cannot_be_forged_or_overwritten(tmp_path, which, moment):
    _, report, receipt, _, guard = prepared(tmp_path)
    _, dispatch = guard.bind_tools([], lambda name, payload: {})
    if moment == "after_write":
        dispatch("write_review", {"text": "authored review"})
    target = report if which == "report" else receipt
    target.write_text("forged report or receipt")
    with pytest.raises(ValueError, match="changed outside"):
        if moment == "before_first_write":
            dispatch("write_review", {"text": "would conceal modification"})
        else:
            guard.verify()
    assert target.read_text() == "forged report or receipt"


def test_forged_report_and_matching_receipt_rejected_against_host_memory(tmp_path):
    _, report, receipt, _, guard = prepared(tmp_path)
    _, dispatch = guard.bind_tools([], lambda name, payload: {})
    dispatch("write_review", {"text": "real host-authored report"})
    report.write_text("forged report")
    receipt.write_text(json.dumps({"sha256": hashlib.sha256(b"forged report").hexdigest()}))
    with pytest.raises(ValueError, match="changed outside"):
        guard.verify()


@pytest.mark.parametrize("which", ["report", "receipt"])
def test_output_deletion_after_write_rejected(tmp_path, which):
    _, report, receipt, _, guard = prepared(tmp_path)
    _, dispatch = guard.bind_tools([], lambda name, payload: {})
    dispatch("write_review", {"text": "host-authored review"})
    (report if which == "report" else receipt).unlink()
    with pytest.raises(ValueError, match="changed outside"):
        guard.verify()


def test_preexisting_review_without_host_write_must_remain_unchanged(tmp_path):
    _, report, receipt, _, guard = fixture(tmp_path)
    report.write_text("old review")
    receipt.write_text("old receipt, not proof of this turn")
    guard.prepare()
    guard.verify()
    assert report.read_text() == "old review"


@pytest.mark.parametrize("path_kind", ["root_link", "file_link", "hardlink", "fifo", "missing", "report_link", "receipt_link", "report_parent_link"])
def test_initial_snapshot_rejects_unsafe_or_missing_inputs(tmp_path, path_kind):
    project, report, receipt, options, guard = fixture(tmp_path)
    if path_kind == "root_link":
        alias = tmp_path / "alias"
        alias.symlink_to(project, target_is_directory=True)
        guard = SupervisedReviewGuard(RunnerOptions(working_dir=str(alias)))
    elif path_kind == "file_link":
        (project / "link").symlink_to(project / "data.csv")
    elif path_kind == "hardlink":
        os.link(project / "data.csv", project / "alias")
    elif path_kind == "fifo":
        os.mkfifo(project / "pipe")
    elif path_kind == "missing":
        options.add_dirs = [str(tmp_path / "missing")]
        guard = SupervisedReviewGuard(options, review_store_factory=ReviewFileStore)
    elif path_kind == "report_link":
        report.symlink_to(project / "data.csv")
    elif path_kind == "receipt_link":
        receipt.symlink_to(project / "data.csv")
    else:
        (project / "paper").rename(project / "old-paper")
        (project / "paper").symlink_to(project / "old-paper", target_is_directory=True)
    with pytest.raises(ValueError, match="verification failed"):
        guard.prepare()
    with pytest.raises(ValueError):
        guard.verify()


def test_snapshot_read_failure_is_fatal(tmp_path, monkeypatch):
    _, _, _, _, guard = fixture(tmp_path)
    def unreadable(path):
        raise PermissionError("fixture cannot read candidate")
    monkeypatch.setattr(os, "scandir", unreadable)
    with pytest.raises(ValueError, match="cannot read candidate"):
        guard.prepare()
    with pytest.raises(ValueError, match="verification failed"):
        guard.bind_tools([], lambda name, payload: {})


def test_postterminal_snapshot_failure_is_fatal(tmp_path, monkeypatch):
    _, _, _, _, guard = prepared(tmp_path)
    def unreadable(path):
        raise OSError("fixture snapshot unavailable")
    monkeypatch.setattr(os, "scandir", unreadable)
    with pytest.raises(ValueError, match="snapshot unavailable"):
        guard.verify()


@pytest.mark.parametrize("which", ["project", "report_parent", "receipt_parent"])
def test_parent_swaps_rejected_before_host_write_and_terminal(tmp_path, which):
    project, report, receipt, _, guard = prepared(tmp_path)
    _, dispatch = guard.bind_tools([], lambda name, payload: {})
    parent = project if which == "project" else report.parent if which == "report_parent" else receipt.parent
    moved = parent.with_name(parent.name + "-moved")
    parent.rename(moved)
    parent.mkdir()
    with pytest.raises(ValueError, match="verification failed"):
        dispatch("write_review", {"text": "must not write into substituted directory"})
    assert not report.exists() and not receipt.exists()


def test_external_project_artifact_skills_and_explicit_evidence_are_protected(tmp_path):
    project, report, receipt, options, _ = fixture(tmp_path)
    artifact = tmp_path / "artifact"
    (artifact / "paper").mkdir(parents=True)
    (artifact / "paper" / "candidate.tex").write_text("external candidate")
    options.review_output = {"path": str(artifact / "paper" / "REVIEW.md"), "receipt": str(receipt)}
    options.add_dirs = [str(artifact)]
    evidence = tmp_path / "engineer.log"
    evidence.write_text("engineer evidence")
    skill = tmp_path / "SKILL.md"
    skill.write_text("read-only skill")
    options.skill_paths = [str(skill)]
    guard = SupervisedReviewGuard(options, review_store_factory=ReviewFileStore, protected_paths=(evidence, project))
    guard.prepare()
    (artifact / "paper" / "candidate.tex").write_text("tampered external candidate")
    with pytest.raises(ValueError, match="protected review evidence changed"):
        guard.verify()


@pytest.mark.parametrize("which", ["add_dirs", "skill_paths", "protected_paths"])
def test_external_bound_inputs_changed_reject(tmp_path, which):
    _, _, _, options, _ = fixture(tmp_path)
    directory = tmp_path / "inputs"
    directory.mkdir()
    evidence = directory / "evidence.txt"
    evidence.write_text("original")
    extra = {}
    if which == "protected_paths":
        extra[which] = [evidence]
    else:
        setattr(options, which, [str(directory) if which == "add_dirs" else str(evidence)])
    guard = SupervisedReviewGuard(options, review_store_factory=ReviewFileStore, **extra)
    guard.prepare()
    evidence.write_text("changed")
    with pytest.raises(ValueError, match="protected review evidence changed"):
        guard.verify()


def test_no_broad_workflow_log_exclusion(tmp_path):
    project, _, _, _, guard = fixture(tmp_path)
    (project / ".argus").mkdir()
    state = project / ".argus" / "host-journal.json"
    state.write_text("before")
    guard.prepare()
    state.write_text("after, including legitimate concurrent host activity")
    with pytest.raises(ValueError, match="host-journal"):
        guard.verify()


@pytest.mark.parametrize("options", [
    RunnerOptions(),
    RunnerOptions(working_dir="relative"),
    RunnerOptions(working_dir="/tmp/../elsewhere"),
    RunnerOptions(working_dir="/"),
    RunnerOptions(working_dir="/tmp", review_output={"path": "/tmp/paper/REVIEW.md"}),
    RunnerOptions(working_dir="/tmp", review_output={"path": "/tmp/paper/REVIEW.md", "receipt": "/tmp/paper/REVIEW.md"}),
])
def test_malformed_host_paths_fail_closed(options):
    with pytest.raises(ValueError):
        SupervisedReviewGuard(options, review_store_factory=ReviewFileStore)


def test_original_command_dispatch_is_retained_without_subprocess_fallback(tmp_path):
    _, _, _, _, guard = prepared(tmp_path)
    calls = []
    def original(name, payload):
        calls.append((name, payload))
        raise ValueError("Docker unavailable; original read-only validation refused")
    _, dispatch = guard.bind_tools([{"name": "run_review_command"}], original)
    with pytest.raises(ValueError, match="Docker unavailable"):
        dispatch("run_review_command", {"argv": ["echo", "fixture"]})
    assert calls == [("run_review_command", {"argv": ["echo", "fixture"]})]
    guard.verify()


def test_report_redaction_uses_existing_store_semantics(tmp_path):
    _, report, _, _, guard = prepared(tmp_path)
    _, dispatch = guard.bind_tools([], lambda name, payload: {})
    dispatch("write_review", {"text": "Review includes api_key=abcdefghijklmnop and findings"})
    assert "abcdefghijklmnop" not in report.read_text()
    assert "<REDACTED:" in report.read_text()
    guard.verify()


def test_store_write_mismatch_is_fatal_even_if_receipt_forged(tmp_path, monkeypatch):
    _, report, receipt, _, guard = prepared(tmp_path)
    def forged(store, text):
        report.write_text("different")
        receipt.write_text(json.dumps({"sha256": hashlib.sha256(b"different").hexdigest()}))
        return {"path": str(report), "characters": len("different")}
    monkeypatch.setattr(ReviewFileStore, "write_review", forged)
    _, dispatch = guard.bind_tools([], lambda name, payload: {})
    with pytest.raises(ValueError, match="mismatches"):
        dispatch("write_review", {"text": "expected host-authored text"})
    with pytest.raises(ValueError, match="verification failed"):
        guard.verify()


def test_report_tool_collision_and_unprepared_reuse_rejected(tmp_path):
    _, _, _, _, guard = fixture(tmp_path)
    with pytest.raises(ValueError, match="not been prepared"):
        guard.bind_tools([], lambda name, payload: {})
    guard.prepare()
    with pytest.raises(ValueError, match="already bound"):
        guard.bind_tools([{"name": "read_review"}], lambda name, payload: {})
    with pytest.raises(ValueError, match="reused"):
        guard.prepare()


@pytest.mark.parametrize("limit", ["SNAPSHOT_MAX_ENTRIES", "SNAPSHOT_MAX_BYTES", "SNAPSHOT_MAX_DEPTH", "SNAPSHOT_MAX_SECONDS"])
def test_incomplete_snapshot_budget_never_silently_skips_inputs(tmp_path, monkeypatch, limit):
    from argus.adapters import dots_supervised_guard
    _, _, _, _, guard = fixture(tmp_path)
    monkeypatch.setattr(dots_supervised_guard, limit, 0)
    with pytest.raises(ValueError, match="budget exceeded.*incomplete"):
        guard.prepare()
    with pytest.raises(ValueError, match="verification failed"):
        guard.verify()


def test_postterminal_snapshot_budget_failure_blocks_success(tmp_path, monkeypatch):
    from argus.adapters import dots_supervised_guard
    _, _, _, _, guard = prepared(tmp_path)
    monkeypatch.setattr(dots_supervised_guard, "SNAPSHOT_MAX_BYTES", 0)
    with pytest.raises(ValueError, match="budget exceeded.*incomplete"):
        guard.verify()


def test_report_path_does_not_grant_arbitrary_external_evidence_scope(tmp_path):
    _, _, receipt, options, _ = fixture(tmp_path)
    options.review_output = {"path": str(tmp_path / "unbound" / "paper" / "REVIEW.md"),
                             "receipt": str(receipt)}
    with pytest.raises(ValueError, match="outside explicitly protected"):
        SupervisedReviewGuard(options, review_store_factory=ReviewFileStore)


def test_report_output_requires_explicit_host_store_injection(tmp_path):
    _, _, _, options, _ = fixture(tmp_path)
    guard = SupervisedReviewGuard(options)
    with pytest.raises(ValueError, match="requires the original host-bound store factory"):
        guard.prepare()
    with pytest.raises(ValueError, match="verification failed"):
        guard.verify()


def test_injected_store_cannot_retarget_host_selected_report(tmp_path):
    _, _, _, options, _ = fixture(tmp_path)
    def wrong_store(*, path, receipt):
        return ReviewFileStore(str(tmp_path / "outside" / "paper" / "REVIEW.md"), receipt)
    guard = SupervisedReviewGuard(options, review_store_factory=wrong_store)
    with pytest.raises(ValueError, match="escaped its host-selected path"):
        guard.prepare()
