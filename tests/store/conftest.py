from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from metaswarm.store import Database, NewRunEvent, Transaction, append_run_event
from metaswarm.store.repo import (
    AttemptCompletion,
    AttemptRepository,
    CampaignRepository,
    NewBranch,
    NewLaneAssignment,
    NewReviewCampaign,
    NewReviewLane,
    NewReviewRound,
    NewReviewSubject,
    NewRun,
    NewStageExecution,
    NewStepAttempt,
    RunRepository,
)


@dataclass(frozen=True, slots=True)
class ReviewGraph:
    run_id: int
    branch_id: int
    stage_id: int
    subject_id: int
    campaign_id: int
    lane_id: int
    assignment_id: int
    round_id: int
    attempt_id: int
    event_id: int


def build_review_graph(
    tx: Transaction,
    *,
    suffix: str = "1",
    round_kind: str = "discovery",
    attempt_outcome: str | None = "succeeded",
    lane_index: int = 0,
    expected_lane_count: int = 1,
) -> ReviewGraph:
    runs = RunRepository()
    attempts = AttemptRepository()
    campaigns = CampaignRepository()
    run = runs.create_run(
        tx,
        NewRun(
            public_id=f"R-{suffix}",
            flow_id="flow",
            flow_hash="flow-hash",
            project_config_hash="project-hash",
            profiles_config_hash="profiles-hash",
            core_version="test-core",
            schema_version=1,
            instance_profile="test",
            code_repo_path="/code",
            code_sha="code-sha",
            task_text="task",
            created_at=1,
            pause_requested_at=None,
            cancel_requested_at=None,
            finished_at=None,
            terminal_state=None,
        ),
    )
    branch = runs.create_branch(
        tx,
        NewBranch(
            run_id=run.id,
            public_id=f"B-{suffix}",
            kind="pipeline",
            task_id=None,
            state="running",
            created_at=2,
        ),
    )
    stage = runs.create_stage(
        tx,
        NewStageExecution(
            run_id=run.id,
            branch_id=branch.id,
            stage_key="review",
            ordinal=1,
            state="running",
            max_author_revisions=3,
            severity_threshold="high",
            started_at=3,
            finished_at=None,
        ),
    )
    subject = campaigns.create_subject(
        tx,
        NewReviewSubject(
            run_id=run.id,
            kind="code",
            target_ref="HEAD",
            revision="rev-1",
            parent_subject_id=None,
            created_at=4,
        ),
    )
    campaign = campaigns.create_campaign(
        tx,
        NewReviewCampaign(
            public_id=f"C-{suffix}",
            run_id=run.id,
            stage_id=stage.id,
            subject_id=subject.id,
            ordinal=1,
            severity_threshold="high",
            policy_version="v1",
            expected_lane_count=expected_lane_count,
            opened_at=5,
        ),
    )
    profile_id = f"reviewer-{suffix}"
    tx.execute(
        "INSERT INTO run_profile_resolution(run_id, profile_id, provider, model, resolved_at) "
        "VALUES (?, ?, 'openai', 'gpt-test', 6)",
        (run.id, profile_id),
    )
    event_id = append_run_event(
        tx,
        NewRunEvent(
            run_id=run.id,
            branch_id=branch.id,
            stage_id=stage.id,
            kind="review_fixture.v1",
            payload={"suffix": suffix},
            created_at=7,
        ),
    )
    lane = campaigns.create_lane(
        tx,
        NewReviewLane(campaign_id=campaign.id, run_id=run.id, lane_index=lane_index),
    )
    assignment = campaigns.create_lane_assignment(
        tx,
        NewLaneAssignment(
            lane_id=lane.id,
            run_id=run.id,
            generation=1,
            profile_id=profile_id,
            replaces_id=None,
            session_id=None,
            human_answer_id=None,
            event_id=event_id,
            assigned_at=8,
        ),
    )
    round_record = campaigns.create_round(
        tx,
        NewReviewRound(
            campaign_id=campaign.id,
            round_no=1,
            kind=round_kind,
            preceding_revision_id=None,
            opened_at=9,
        ),
    )
    attempt = attempts.create_attempt(
        tx,
        NewStepAttempt(
            public_id=f"A-{suffix}",
            run_id=run.id,
            stage_id=stage.id,
            role="reviewer",
            campaign_id=campaign.id,
            round_id=round_record.id,
            lane_id=lane.id,
            lane_assignment_id=assignment.id,
            subject_revision="rev-1",
            session_id=None,
            profile_id=profile_id,
            requested_model="gpt-test",
            prompt_template_id="review.v1",
            prompt_hash="prompt-hash",
            rubric_id="rubric",
            rubric_hash="rubric-hash",
            input_sha="input-sha",
            input_refs_json="[]",
            manifest_json="{}",
            started_at=10,
        ),
    )
    if attempt_outcome is not None:
        attempt = attempts.complete_attempt(
            tx,
            attempt.id,
            AttemptCompletion(
                outcome=attempt_outcome,
                outcome_detail=None,
                actual_model="gpt-test",
                output_sha="output-sha" if attempt_outcome == "succeeded" else None,
                finished_at=11,
                transcript_path=None,
                transcript_digest=None,
            ),
        )
    return ReviewGraph(
        run_id=run.id,
        branch_id=branch.id,
        stage_id=stage.id,
        subject_id=subject.id,
        campaign_id=campaign.id,
        lane_id=lane.id,
        assignment_id=assignment.id,
        round_id=round_record.id,
        attempt_id=attempt.id,
        event_id=event_id,
    )


@pytest.fixture
def database_factory(tmp_path: Path) -> Callable[[], Awaitable[Database]]:
    counter = 0

    async def factory() -> Database:
        nonlocal counter
        counter += 1
        return await Database.open(tmp_path / f"state-{counter}.sqlite3", core_version="test-core")

    return factory


@pytest.fixture
def review_graph_builder() -> Callable[..., ReviewGraph]:
    return build_review_graph
