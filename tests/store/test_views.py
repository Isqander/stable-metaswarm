from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from metaswarm.store import NewRunEvent, Transaction, append_run_event
from metaswarm.store.repo import (
    AttemptCompletion,
    AttemptRepository,
    CampaignRepository,
    FindingRepository,
    NewAuthorRevision,
    NewBlocker,
    NewBranch,
    NewFinding,
    NewFindingResolution,
    NewObservationLink,
    NewReviewObservation,
    NewRun,
    NewSeverityOverride,
    NewStepAttempt,
    QuestionRepository,
    RunRepository,
)


def test_campaign_counters_count_revisions_and_closed_rounds_independently(
    database_factory: Callable[[], Awaitable],
    review_graph_builder: Callable[..., object],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        attempts = AttemptRepository()
        campaigns = CampaignRepository()
        try:
            graph = await database.transaction(review_graph_builder)

            def add_revision(tx: Transaction) -> None:
                author = attempts.create_attempt(
                    tx,
                    NewStepAttempt(
                        public_id="A-counter-author",
                        run_id=graph.run_id,
                        stage_id=graph.stage_id,
                        role="author",
                        campaign_id=None,
                        round_id=None,
                        lane_id=None,
                        lane_assignment_id=None,
                        subject_revision=None,
                        session_id=None,
                        profile_id="author",
                        requested_model="author-model",
                        prompt_template_id="author.v1",
                        prompt_hash="author",
                        rubric_id=None,
                        rubric_hash=None,
                        input_sha="rev-1",
                        input_refs_json="[]",
                        manifest_json="{}",
                        started_at=20,
                    ),
                )
                author = attempts.complete_attempt(
                    tx,
                    author.id,
                    AttemptCompletion(
                        "succeeded", None, "author-model", "rev-2", 21, None, None
                    ),
                )
                campaigns.create_author_revision(
                    tx,
                    NewAuthorRevision(
                        campaign_id=graph.campaign_id,
                        stage_id=graph.stage_id,
                        revision_no=1,
                        attempt_id=author.id,
                        attempt_role="author",
                        attempt_outcome="succeeded",
                        input_sha="rev-1",
                        output_sha="rev-2",
                        artifact_revision_id=None,
                        completed_at=21,
                    ),
                )

            await database.transaction(add_revision)
            before = await database.read(
                lambda db: campaigns.read_campaign_counters(db, graph.campaign_id)
            )
            assert before is not None
            assert (before.author_revision_count, before.review_check_count) == (1, 0)
            await database.transaction(
                lambda tx: campaigns.close_round(tx, graph.round_id, "clean", 22)
            )
            after = await database.read(
                lambda db: campaigns.read_campaign_counters(db, graph.campaign_id)
            )
            assert after is not None
            assert (after.author_revision_count, after.review_check_count) == (1, 1)
            columns = await database.read(
                lambda db: tuple(row["name"] for row in db.fetch_all("PRAGMA table_info(review_campaign)"))
            )
            assert "author_revision_count" not in columns
            assert "review_check_count" not in columns
        finally:
            await database.close()

    asyncio.run(scenario())


def test_finding_views_keep_status_period_and_severity_as_distinct_lifecycles(
    database_factory: Callable[[], Awaitable],
    review_graph_builder: Callable[..., object],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        attempts = AttemptRepository()
        findings = FindingRepository()
        try:
            graph = await database.transaction(review_graph_builder)

            def seed(tx: Transaction) -> tuple[int, int]:
                reconciler = attempts.create_attempt(
                    tx,
                    NewStepAttempt(
                        public_id="A-view-reconciler",
                        run_id=graph.run_id,
                        stage_id=graph.stage_id,
                        role="reconciler",
                        campaign_id=graph.campaign_id,
                        round_id=graph.round_id,
                        lane_id=None,
                        lane_assignment_id=None,
                        subject_revision="rev-1",
                        session_id=None,
                        profile_id="reviewer-1",
                        requested_model="gpt-test",
                        prompt_template_id="reconcile.v1",
                        prompt_hash="views",
                        rubric_id=None,
                        rubric_hash=None,
                        input_sha=None,
                        input_refs_json="[]",
                        manifest_json="{}",
                        started_at=20,
                    ),
                )
                reconciler = attempts.complete_attempt(
                    tx,
                    reconciler.id,
                    AttemptCompletion("succeeded", None, "gpt-test", "out", 21, None, None),
                )

                def observation(title: str, severity: str, created_at: int):
                    return findings.create_observation(
                        tx,
                        NewReviewObservation(
                            campaign_id=graph.campaign_id,
                            round_id=graph.round_id,
                            lane_id=graph.lane_id,
                            attempt_id=graph.attempt_id,
                            subject_id=graph.subject_id,
                            revision="rev-1",
                            title=title,
                            body="body",
                            file_path=None,
                            line_start=None,
                            line_end=None,
                            evidence=None,
                            severity_suggested=severity,
                            unchanged_from_id=None,
                            severity_effective=severity,
                            dedup_key=title,
                            created_at=created_at,
                        ),
                    )

                first_observation = observation("first", "high", 22)
                first_event = append_run_event(
                    tx,
                    NewRunEvent(
                        run_id=graph.run_id,
                        kind="first_seen.v1",
                        payload={},
                        created_at=22,
                    ),
                )
                finding = findings.create_finding(
                    tx,
                    NewFinding(
                        run_id=graph.run_id,
                        subject_id=graph.subject_id,
                        first_campaign_id=graph.campaign_id,
                        first_round_id=graph.round_id,
                        first_observation_id=first_observation.id,
                        first_revision="rev-1",
                        first_owner_lane_id=graph.lane_id,
                        title="first",
                        event_id=first_event,
                        created_at=22,
                    ),
                )

                def link(observation_id: int, link_type: str, event_id: int) -> None:
                    findings.create_observation_link(
                        tx,
                        NewObservationLink(
                            observation_id=observation_id,
                            campaign_id=graph.campaign_id,
                            round_id=graph.round_id,
                            finding_id=finding.id,
                            link_type=link_type,
                            decided_by_attempt_id=reconciler.id,
                            decided_by_role="reconciler",
                            decided_by_outcome="succeeded",
                            decided_by_human_answer_id=None,
                            reason="reopened" if link_type == "reopening" else None,
                            event_id=event_id,
                            created_at=event_id,
                        ),
                    )

                link(first_observation.id, "first_seen", first_event)
                accepted_event = append_run_event(
                    tx,
                    NewRunEvent(
                        run_id=graph.run_id,
                        kind="accepted.v1",
                        payload={},
                        created_at=23,
                    ),
                )
                findings.create_resolution(
                    tx,
                    NewFindingResolution(
                        run_id=graph.run_id,
                        finding_id=finding.id,
                        resolution="accepted_reason",
                        resolution_authority="reviewer",
                        campaign_id=graph.campaign_id,
                        round_no=1,
                        human_answer_id=None,
                        closes_severity_period=0,
                        event_id=accepted_event,
                        created_at=23,
                    ),
                )
                reopened_observation = observation("reopen-one", "low", 24)
                reopen_event = append_run_event(
                    tx,
                    NewRunEvent(
                        run_id=graph.run_id,
                        kind="reopened.v1",
                        payload={},
                        created_at=24,
                    ),
                )
                link(reopened_observation.id, "reopening", reopen_event)
                fixed_event = append_run_event(
                    tx,
                    NewRunEvent(
                        run_id=graph.run_id,
                        kind="fixed.v1",
                        payload={},
                        created_at=25,
                    ),
                )
                findings.create_resolution(
                    tx,
                    NewFindingResolution(
                        run_id=graph.run_id,
                        finding_id=finding.id,
                        resolution="verified_fixed",
                        resolution_authority="reviewer",
                        campaign_id=graph.campaign_id,
                        round_no=1,
                        human_answer_id=None,
                        closes_severity_period=1,
                        event_id=fixed_event,
                        created_at=25,
                    ),
                )
                current_observation = observation("reopen-two", "low", 26)
                current_open_event = append_run_event(
                    tx,
                    NewRunEvent(
                        run_id=graph.run_id,
                        kind="reopened.v1",
                        payload={},
                        created_at=26,
                    ),
                )
                link(current_observation.id, "reopening", current_open_event)
                override_event = append_run_event(
                    tx,
                    NewRunEvent(
                        run_id=graph.run_id,
                        kind="override.v1",
                        payload={},
                        created_at=27,
                    ),
                )
                findings.create_severity_override(
                    tx,
                    NewSeverityOverride(
                        finding_id=finding.id,
                        old_severity="low",
                        new_severity="medium",
                        reason="human downgrade",
                        human_answer_id=None,
                        event_id=override_event,
                        created_at=27,
                    ),
                )
                return finding.id, current_open_event

            finding_id, period_start = await database.transaction(seed)
            status, period, severity = await database.read(
                lambda db: (
                    findings.read_finding_status(db, finding_id),
                    findings.read_finding_period(db, finding_id),
                    findings.read_finding_severity(db, finding_id),
                )
            )
            assert status is not None and status.status == "open"
            assert status.last_resolution == "verified_fixed"
            assert period is not None and period.period_start_event_id == period_start
            assert severity is not None
            assert severity.escalation_severity == "medium"
            assert severity.historical_max == "high"

            def out_of_order_resolutions(tx: Transaction) -> tuple[int, int]:
                lower = append_run_event(
                    tx,
                    NewRunEvent(
                        run_id=graph.run_id,
                        kind="historical_resolution.v1",
                        payload={},
                        created_at=28,
                    ),
                )
                higher = append_run_event(
                    tx,
                    NewRunEvent(
                        run_id=graph.run_id,
                        kind="latest_resolution.v1",
                        payload={},
                        created_at=29,
                    ),
                )
                findings.create_resolution(
                    tx,
                    NewFindingResolution(
                        graph.run_id,
                        finding_id,
                        "policy_closed",
                        "policy",
                        graph.campaign_id,
                        None,
                        None,
                        0,
                        higher,
                        29,
                    ),
                )
                findings.create_resolution(
                    tx,
                    NewFindingResolution(
                        graph.run_id,
                        finding_id,
                        "accepted_reason",
                        "reviewer",
                        graph.campaign_id,
                        1,
                        None,
                        0,
                        lower,
                        28,
                    ),
                )
                return lower, higher

            lower, higher = await database.transaction(out_of_order_resolutions)
            assert lower < higher
            final_status = await database.read(
                lambda db: findings.read_finding_status(db, finding_id)
            )
            assert final_status is not None
            assert final_status.last_resolution == "policy_closed"
            assert final_status.status == "closed"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_run_state_priority_covers_terminal_to_idle_and_all_blocker_classes(
    database_factory: Callable[[], Awaitable],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        runs = RunRepository()
        questions = QuestionRepository()
        try:
            def seed(tx: Transaction) -> dict[str, int]:
                ids: dict[str, int] = {}
                configurations = (
                    ("terminal", "succeeded", None, None, "done", None),
                    ("cancelling", None, None, 2, "done", None),
                    ("paused", None, 3, None, "done", None),
                    ("running", None, None, None, "ready", "awaiting_continue"),
                    ("waiting_human", None, None, None, "blocked", "awaiting_continue"),
                    ("stalled", None, None, None, "blocked", "dependency"),
                    ("idle", None, None, None, "done", None),
                )
                for index, (name, terminal, paused, cancelled, branch_state, blocker) in enumerate(
                    configurations, start=1
                ):
                    run = runs.create_run(
                        tx,
                        NewRun(
                            public_id=f"R-state-{name}",
                            flow_id="flow",
                            flow_hash="f",
                            project_config_hash="p",
                            profiles_config_hash="profiles",
                            core_version="core",
                            schema_version=1,
                            instance_profile="test",
                            code_repo_path="/code",
                            code_sha="sha",
                            task_text="task",
                            created_at=index,
                            pause_requested_at=paused,
                            cancel_requested_at=cancelled,
                            finished_at=index if terminal else None,
                            terminal_state=terminal,
                        ),
                    )
                    branch = runs.create_branch(
                        tx,
                        NewBranch(
                            run_id=run.id,
                            public_id="B-main",
                            kind="pipeline",
                            task_id=None,
                            state=branch_state,
                            created_at=index,
                        ),
                    )
                    event = append_run_event(
                        tx,
                        NewRunEvent(
                            run_id=run.id,
                            branch_id=branch.id,
                            kind="state_fixture.v1",
                            payload={},
                            created_at=index,
                        ),
                    )
                    if blocker is not None:
                        questions.create_blocker(
                            tx,
                            NewBlocker(
                                run_id=run.id,
                                kind=blocker,
                                branch_id=branch.id,
                                task_id=None,
                                stage_id=None,
                                question_id=None,
                                detail=None,
                                created_at=index,
                                created_event_id=event,
                                cleared_at=None,
                                cleared_event_id=None,
                            ),
                        )
                    ids[name] = run.id
                return ids

            ids = await database.transaction(seed)
            actual = await database.read(
                lambda db: {
                    name: runs.read_run_state(db, run_id).state  # type: ignore[union-attr]
                    for name, run_id in ids.items()
                }
            )
            assert actual == {**{name: name for name in ids}, "terminal": "succeeded"}
        finally:
            await database.close()

    asyncio.run(scenario())
