"""Original Reviewer integration using controlled typed tools, no native dispatch."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from argus.adapters.dots_supervised_guard import SupervisedReviewGuard
from argus.core.dots_profile import SupervisedDotsProfile
from argus.core.models import RunnerResult
from argus.core.pipeline_state import read_pipeline_state, write_pipeline_state
from argus.reviewer import Reviewer, ReviewerConfig
from argus.skills.vertical_select import persist_vertical


class FixtureReviewer:
    backend = "dots"

    def __init__(self, profile, *, mutate=None, submit_action=True):
        self.execution_profile = profile
        self.calls = []
        self.mutate = mutate
        self.submit_action = submit_action
        self.failure = None

    @contextmanager
    def bind_role_tools(self, tools, dispatch, *, review_store_factory=None):
        self.bound = tools, dispatch
        self.review_store_factory = review_store_factory
        yield

    def run_exec(self, **kwargs):
        self.calls.append(kwargs)
        if type(self.execution_profile) is not SupervisedDotsProfile:
            return RunnerResult(exit_code=1, fatal_error="strict fixture has no execution capability")
        options = kwargs["options"]
        guard = SupervisedReviewGuard(options, review_store_factory=self.review_store_factory)
        try:
            guard.prepare()
            tools, dispatch = guard.bind_tools(*self.bound)
            if options.review_output:
                assert {"read_review", "write_review"} <= {tool["name"] for tool in tools}
                dispatch("write_review", {"text": "Complete host-authored review of current evidence"})
            if self.submit_action:
                dispatch("approve_review", {"review": "typed decision", "recommendation": "weak_accept"})
            if self.mutate:
                self.mutate(options)
            guard.verify()
        except ValueError as exc:
            self.failure = str(exc)
            return RunnerResult(exit_code=1, fatal_error=self.failure)
        return RunnerResult(exit_code=0, agent_messages=["Report saved"], input_tokens=10, output_tokens=5)


@pytest.fixture
def paper(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    persist_vertical(project, "research", target_venue="ICLR")
    state = read_pipeline_state(project)
    state["current_stage"] = "review"
    write_pipeline_state(project, state)
    (project / "paper").mkdir()
    (project / "paper" / "main.tex").write_text("Current manuscript")
    (project / "paper" / "main.pdf").write_bytes(b"Current PDF")
    return project


def evaluate(paper, runner, *, config=None, **kwargs):
    reviewer = Reviewer(runner)
    # Protect only these fixture inputs, not unrelated locally installed skills.
    reviewer.mission.libraries = lambda: SimpleNamespace(block="", native_paths=[], library_roots=[])
    return reviewer.evaluate(
        objective="Review the current paper", round_index=1, session_id=None,
        main_summary="Current candidate is ready", main_error=None, scope="final_submission",
        config=config or ReviewerConfig(active_vertical="research", working_dir=str(paper),
                                        vertical_state_root=str(paper)),
        **kwargs,
    )


def test_original_reviewer_accepts_only_host_written_report_and_typed_action(paper):
    runner = FixtureReviewer(SupervisedDotsProfile())
    decision = evaluate(paper, runner)
    assert decision.status == "done" and decision.final_submission_certified
    assert decision.reason == "Complete host-authored review of current evidence"
    assert (paper / "paper" / "REVIEW.md").read_text() == decision.reason
    assert decision.input_tokens == 10 and decision.output_tokens == 5
    from argus.reviewer.review_file import ReviewFileStore
    assert runner.review_store_factory is ReviewFileStore
    options = runner.calls[0]["options"]
    assert options.force_safe_mode and options.sandbox_mode == "read-only"
    assert "read_review and write_review" in runner.calls[0]["prompt"]
    assert not Path(options.review_output["receipt"]).parent.exists()  # Original cleanup.


@pytest.mark.parametrize("profile", [None, "supervised-approx-v1", {"name": "supervised-approx-v1"}])
def test_strict_dots_and_profile_lookalikes_keep_original_behavior(paper, profile):
    runner = FixtureReviewer(profile)
    decision = evaluate(paper, runner)
    assert decision.backend_unavailable
    assert runner.review_store_factory is None
    assert runner.calls[0]["options"].review_output is None
    assert runner.calls[0]["options"].add_dirs is None
    assert "read_review and write_review" not in runner.calls[0]["prompt"]


def test_approval_cannot_bypass_original_candidate_drift_failure(paper):
    runner = FixtureReviewer(SupervisedDotsProfile(), mutate=lambda options: (
        paper / "paper" / "main.tex").write_text("Worker changed candidate"))
    decision = evaluate(paper, runner)
    assert decision.status == "blocked" and decision.backend_unavailable
    assert not decision.final_submission_certified
    assert "protected review evidence changed" in decision.reason
    assert (paper / "paper" / "main.tex").read_text() == "Worker changed candidate"
    assert read_pipeline_state(paper).get("current_verdict") != "done"


def test_no_typed_action_remains_unjudged_even_with_report(paper):
    runner = FixtureReviewer(SupervisedDotsProfile(), submit_action=False)
    decision = evaluate(paper, runner)
    assert decision.status == "blocked" and decision.backend_unavailable
    assert not decision.final_submission_certified
    assert "without submitting a review action" in decision.reason


def test_host_config_binds_external_artifacts_state_and_evidence(paper, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    persist_vertical(state, "research", target_venue="ICLR")
    state_value = read_pipeline_state(state)
    state_value["current_stage"] = "review"
    write_pipeline_state(state, state_value)
    snapshot = tmp_path / "prior-candidate"
    snapshot.mkdir()
    checkpoint = tmp_path / "CHECKPOINT.md"
    checkpoint.write_text("Original checkpoint")
    evidence = tmp_path / "engineer.log"
    evidence.write_text("Original engineer evidence")
    config = ReviewerConfig(active_vertical="research", working_dir=str(work),
                            artifact_root=str(paper), vertical_state_root=str(state),
                            narrative_snapshot_root=str(snapshot))
    runner = FixtureReviewer(SupervisedDotsProfile(), mutate=lambda options: evidence.write_text("Changed evidence"))
    decision = evaluate(paper, runner, config=config, checkpoint_path=str(checkpoint), engineer_log_path=str(evidence))
    assert set(runner.calls[0]["options"].add_dirs) == {
        str(paper), str(state), str(snapshot), str(checkpoint), str(evidence),
    }
    assert decision.backend_unavailable and "engineer.log" in decision.reason
    assert runner.calls[0]["options"].review_output["path"] == str(paper / "paper" / "REVIEW.md")
