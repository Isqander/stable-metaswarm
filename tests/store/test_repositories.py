from __future__ import annotations

import asyncio
import inspect
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import FrozenInstanceError

import pytest

from metaswarm.store import (
    NewRunEvent,
    RepositoryAlreadyTerminal,
    RepositoryPreconditionFailed,
    RepositoryRecordNotFound,
    ReviewerExposureConflict,
    Transaction,
    append_run_event,
)
from metaswarm.store.repo import (
    AttemptCompletion,
    AttemptRepository,
    CampaignRepository,
    FindingRepository,
    NewBlocker,
    NewFinding,
    NewFindingResolution,
    NewFindingRound,
    NewHumanQuestion,
    NewLaneAssignment,
    NewNotificationOutbox,
    NewObservationLink,
    NewReviewCampaign,
    NewReviewLane,
    NewReviewObservation,
    NewReviewRound,
    NewStepAttempt,
    OpenFindingLedgerRecord,
    QuestionRepository,
    RunRepository,
)


def _public_methods(repository: type[object]) -> set[str]:
    return {
        name
        for name, value in repository.__dict__.items()
        if callable(value) and not name.startswith("_")
    }


def test_public_repository_surface_is_the_frozen_t1_3_api() -> None:
    assert _public_methods(RunRepository) == {
        "create_run",
        "create_branch",
        "create_stage",
        "get_run",
        "get_branch",
        "get_stage",
        "read_run_state",
        "read_open_blockers",
        "find_human_blockers_on_unblocked_branches",
        "find_blocked_branches_without_blocker",
        "find_questions_without_matching_blocker",
        "set_branch_state",
    }
    assert _public_methods(AttemptRepository) == {
        "create_attempt",
        "get_attempt",
        "complete_attempt",
        "reserve_reviewer_exposure",
    }
    assert _public_methods(CampaignRepository) == {
        "get_campaign",
        "get_round",
        "create_subject",
        "create_campaign",
        "create_lane",
        "create_lane_assignment",
        "create_round",
        "create_author_revision",
        "read_effective_roster",
        "read_campaign_counters",
        "transition_campaign_state",
        "replace_lane_assignment",
        "waive_lane_for_round",
        "close_round",
        "find_missing_discovery_lane_participation",
        "find_discovery_without_successful_opinion",
        "find_discovery_roster_cardinality_mismatch",
        "find_unlinked_observations",
        "find_missing_finding_participation",
        "find_incomplete_issued",
    }
    assert _public_methods(FindingRepository) == {
        "create_observation",
        "create_finding",
        "create_observation_link",
        "create_finding_round",
        "record_reviewer_decision",
        "create_resolution",
        "create_severity_override",
        "read_reconciliation_observations",
        "read_scoped_finding_ledger",
        "read_current_finding_rounds",
        "read_dispute_candidates",
        "read_finding_status",
        "read_finding_period",
        "read_finding_severity",
        "read_finding_round_history",
        "find_links_with_foreign_finding_run",
    }
    assert _public_methods(QuestionRepository) == {
        "create_question",
        "create_question_observation",
        "create_outbox_message",
        "create_blocker",
        "get_question",
        "read_question_observations",
    }
    assert list(inspect.signature(AttemptRepository.complete_attempt).parameters) == [
        "self",
        "tx",
        "attempt_id",
        "completion",
    ]
    assert list(
        inspect.signature(CampaignRepository.replace_lane_assignment).parameters
    ) == [
        "self",
        "tx",
        "round_id",
        "lane_id",
        "human_answer_id",
        "profile_id",
        "event_id",
        "assigned_at",
    ]
    for name in (
        "find_missing_discovery_lane_participation",
        "find_discovery_without_successful_opinion",
        "find_discovery_roster_cardinality_mismatch",
        "find_unlinked_observations",
        "find_missing_finding_participation",
        "find_incomplete_issued",
    ):
        assert list(inspect.signature(getattr(CampaignRepository, name)).parameters) == [
            "self",
            "db",
            "round_id",
        ]


@pytest.mark.parametrize(
    ("repository_type", "method_name"),
    (
        (RunRepository, "get_run"),
        (RunRepository, "get_branch"),
        (RunRepository, "get_stage"),
        (CampaignRepository, "get_campaign"),
        (CampaignRepository, "get_round"),
        (QuestionRepository, "get_question"),
    ),
    ids=lambda value: value if isinstance(value, str) else value.__name__,
)
def test_all_optional_getters_return_none_for_an_unknown_id(
    repository_type: type[object],
    method_name: str,
    database_factory: Callable[[], Awaitable],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        try:
            repository = repository_type()
            assert await database.read(
                lambda db: getattr(repository, method_name)(db, 404)
            ) is None
        finally:
            await database.close()

    asyncio.run(scenario())


def test_named_state_mutations_return_records_and_enforce_expected_state(
    database_factory: Callable[[], Awaitable],
    review_graph_builder: Callable[..., object],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        runs = RunRepository()
        campaigns = CampaignRepository()
        try:
            graph = await database.transaction(review_graph_builder)
            branch = await database.transaction(
                lambda tx: runs.set_branch_state(tx, graph.branch_id, "running", "blocked")
            )
            assert branch.state == "blocked"
            with pytest.raises(RepositoryPreconditionFailed):
                await database.transaction(
                    lambda tx: runs.set_branch_state(tx, graph.branch_id, "running", "ready")
                )
            campaign = await database.transaction(
                lambda tx: campaigns.transition_campaign_state(
                    tx, graph.campaign_id, "discovery", "reconciliation", None
                )
            )
            assert campaign.state == "reconciliation"
            with pytest.raises(RepositoryPreconditionFailed):
                await database.transaction(
                    lambda tx: campaigns.transition_campaign_state(
                        tx, graph.campaign_id, "discovery", "fix_cycle", None
                    )
                )
        finally:
            await database.close()

    asyncio.run(scenario())


def test_fix_check_skips_lane_gates_and_rejects_replacement_and_waiver_in_repository(
    database_factory: Callable[[], Awaitable],
    review_graph_builder: Callable[..., object],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        campaigns = CampaignRepository()
        findings = FindingRepository()
        try:
            graph = await database.transaction(
                lambda tx: review_graph_builder(
                    tx, suffix="fix-check", round_kind="fix_check", attempt_outcome="failed"
                )
            )
            lane_results = await database.read(
                lambda db: (
                    campaigns.find_missing_discovery_lane_participation(db, graph.round_id),
                    campaigns.find_discovery_without_successful_opinion(db, graph.round_id),
                    campaigns.find_discovery_roster_cardinality_mismatch(db, graph.round_id),
                )
            )
            assert lane_results == ((), (), ())
            assert await database.read(
                lambda db: findings.read_dispute_candidates(db, graph.round_id)
            ) == ()
            with pytest.raises(RepositoryPreconditionFailed, match="open discovery"):
                await database.transaction(
                    lambda tx: campaigns.replace_lane_assignment(
                        tx,
                        graph.round_id,
                        graph.lane_id,
                        404,
                        "reviewer-fix-check",
                        graph.event_id,
                        100,
                    )
                )
            with pytest.raises(RepositoryPreconditionFailed, match="open discovery"):
                await database.transaction(
                    lambda tx: campaigns.waive_lane_for_round(
                        tx,
                        graph.round_id,
                        graph.lane_id,
                        404,
                        graph.event_id,
                        100,
                    )
                )
        finally:
            await database.close()

    asyncio.run(scenario())


def test_foreign_finding_run_is_rejected_by_writer_and_reported_by_recovery_read(
    database_factory: Callable[[], Awaitable],
    review_graph_builder: Callable[..., object],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        attempts = AttemptRepository()
        findings = FindingRepository()
        try:
            graph = await database.transaction(
                lambda tx: review_graph_builder(tx, suffix="link-a")
            )
            other = await database.transaction(
                lambda tx: review_graph_builder(tx, suffix="link-b")
            )

            def seed(tx: Transaction) -> tuple[int, int, int, int]:
                observation = findings.create_observation(
                    tx,
                    NewReviewObservation(
                        graph.campaign_id,
                        graph.round_id,
                        graph.lane_id,
                        graph.attempt_id,
                        graph.subject_id,
                        "rev-1",
                        "local observation",
                        "body",
                        None,
                        None,
                        None,
                        None,
                        "medium",
                        None,
                        "medium",
                        "foreign-link-local",
                        100,
                    ),
                )
                other_observation = findings.create_observation(
                    tx,
                    NewReviewObservation(
                        other.campaign_id,
                        other.round_id,
                        other.lane_id,
                        other.attempt_id,
                        other.subject_id,
                        "rev-1",
                        "foreign finding",
                        "body",
                        None,
                        None,
                        None,
                        None,
                        "medium",
                        None,
                        "medium",
                        "foreign-link-other",
                        100,
                    ),
                )
                other_event = append_run_event(
                    tx,
                    NewRunEvent(
                        run_id=other.run_id,
                        kind="foreign_finding.v1",
                        payload={},
                        created_at=101,
                    ),
                )
                foreign_finding = findings.create_finding(
                    tx,
                    NewFinding(
                        other.run_id,
                        other.subject_id,
                        other.campaign_id,
                        other.round_id,
                        other_observation.id,
                        "rev-1",
                        other.lane_id,
                        "foreign finding",
                        other_event,
                        101,
                    ),
                )
                reconciler = attempts.create_attempt(
                    tx,
                    NewStepAttempt(
                        "A-link-reconciler",
                        graph.run_id,
                        graph.stage_id,
                        "reconciler",
                        graph.campaign_id,
                        graph.round_id,
                        None,
                        None,
                        "rev-1",
                        None,
                        "reviewer-link-a",
                        "gpt-test",
                        "reconcile.v1",
                        "foreign-link",
                        None,
                        None,
                        None,
                        "[]",
                        "{}",
                        102,
                    ),
                )
                reconciler = attempts.complete_attempt(
                    tx,
                    reconciler.id,
                    AttemptCompletion("succeeded", None, "gpt-test", "out", 103, None, None),
                )
                local_event = append_run_event(
                    tx,
                    NewRunEvent(
                        run_id=graph.run_id,
                        kind="foreign_link.v1",
                        payload={},
                        created_at=103,
                    ),
                )
                return observation.id, foreign_finding.id, reconciler.id, local_event

            observation_id, finding_id, reconciler_id, event_id = await database.transaction(
                seed
            )
            value = NewObservationLink(
                observation_id,
                graph.campaign_id,
                graph.round_id,
                finding_id,
                "first_seen",
                reconciler_id,
                "reconciler",
                "succeeded",
                None,
                None,
                event_id,
                103,
            )
            with pytest.raises(RepositoryPreconditionFailed, match="different runs"):
                await database.transaction(
                    lambda tx: findings.create_observation_link(tx, value)
                )

            await database.transaction(
                lambda tx: tx.execute(
                    "INSERT INTO finding_observation_link(observation_id,campaign_id,round_id,"
                    "finding_id,link_type,decided_by_attempt_id,decided_by_role,"
                    "decided_by_outcome,decided_by_human_answer_id,reason,event_id,created_at) "
                    "VALUES (?,?,?,?,'first_seen',?,'reconciler','succeeded',NULL,NULL,?,103)",
                    (
                        observation_id,
                        graph.campaign_id,
                        graph.round_id,
                        finding_id,
                        reconciler_id,
                        event_id,
                    ),
                )
            )
            assert await database.read(
                lambda db: findings.find_links_with_foreign_finding_run(db, graph.run_id)
            ) == (observation_id,)
        finally:
            await database.close()

    asyncio.run(scenario())


def test_attempt_and_round_cas_distinguish_missing_and_terminal(
    database_factory: Callable[[], Awaitable],
    review_graph_builder: Callable[..., object],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        attempts = AttemptRepository()
        campaigns = CampaignRepository()
        try:
            graph = await database.transaction(
                lambda tx: review_graph_builder(tx, attempt_outcome=None)
            )
            before = await database.read(lambda db: attempts.get_attempt(db, graph.attempt_id))
            assert before is not None and before.outcome is None
            completion = AttemptCompletion(
                outcome="interrupted",
                outcome_detail=None,
                actual_model=None,
                output_sha=None,
                finished_at=100,
                transcript_path=None,
                transcript_digest=None,
            )
            completed = await database.transaction(
                lambda tx: attempts.complete_attempt(tx, graph.attempt_id, completion)
            )
            assert completed.outcome == "interrupted"
            assert completed.subject_revision == before.subject_revision
            with pytest.raises(RepositoryAlreadyTerminal) as terminal:
                await database.transaction(
                    lambda tx: attempts.complete_attempt(tx, graph.attempt_id, completion)
                )
            assert terminal.value.terminal_value == "interrupted"
            with pytest.raises(RepositoryRecordNotFound):
                await database.transaction(lambda tx: attempts.complete_attempt(tx, 9999, completion))

            closed = await database.transaction(
                lambda tx: campaigns.close_round(tx, graph.round_id, "clean", 110)
            )
            assert closed.result == "clean" and closed.closed_at == 110
            with pytest.raises(RepositoryAlreadyTerminal):
                await database.transaction(
                    lambda tx: campaigns.close_round(tx, graph.round_id, "clean", 110)
                )
            with pytest.raises(RepositoryRecordNotFound):
                await database.transaction(lambda tx: campaigns.close_round(tx, 9999, "clean", 1))
        finally:
            await database.close()

    asyncio.run(scenario())


def test_lane_failure_replacement_and_waiver_are_discovery_only_audit_effects(
    database_factory: Callable[[], Awaitable],
    review_graph_builder: Callable[..., object],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        campaigns = CampaignRepository()
        questions = QuestionRepository()
        try:
            replacement_graph = await database.transaction(
                lambda tx: review_graph_builder(tx, suffix="replace", attempt_outcome="failed")
            )

            def replace(tx: Transaction):
                tx.execute(
                    "INSERT INTO run_profile_resolution(run_id, profile_id, provider, model, "
                    "resolved_at) VALUES (?, 'replacement', 'anthropic', 'replacement-model', 20)",
                    (replacement_graph.run_id,),
                )
                question = questions.create_question(
                    tx,
                    NewHumanQuestion(
                        run_id=replacement_graph.run_id,
                        branch_id=replacement_graph.branch_id,
                        stage_id=replacement_graph.stage_id,
                        campaign_id=replacement_graph.campaign_id,
                        round_id=replacement_graph.round_id,
                        finding_id=None,
                        reason="lane_failure",
                        question_text="replace lane",
                        options_json='["replace"]',
                        snapshot_json="{}",
                        asked_at=20,
                    ),
                )
                outbox = questions.create_outbox_message(
                    tx,
                    NewNotificationOutbox(
                        run_id=replacement_graph.run_id,
                        question_id=question.id,
                        transport="cli",
                        target_ref=None,
                        body="Need input",
                        reply_markup=None,
                        created_at=25,
                        sent_at=None,
                        transport_message_id=None,
                        last_error=None,
                    ),
                )
                assert outbox.question_id == question.id
                answer = tx.execute(
                    "INSERT INTO human_answer(question_id, raw_text, chosen_option, "
                    "interpreted_json, transport, update_id, received_at) "
                    "VALUES (?, 'replace', 'replace', NULL, 'cli', NULL, 21)",
                    (question.id,),
                )
                assert answer.lastrowid is not None
                event = append_run_event(
                    tx,
                    NewRunEvent(
                        run_id=replacement_graph.run_id,
                        kind="lane_replaced.v1",
                        payload={},
                        created_at=21,
                    ),
                )
                assignment = campaigns.replace_lane_assignment(
                    tx,
                    replacement_graph.round_id,
                    replacement_graph.lane_id,
                    answer.lastrowid,
                    "replacement",
                    event,
                    21,
                )
                return assignment, answer.lastrowid, event

            replacement, answer_id, event_id = await database.transaction(replace)
            assert replacement.generation == 2
            assert replacement.replaces_id == replacement_graph.assignment_id
            assert replacement.human_answer_id == answer_id
            with pytest.raises(sqlite3.IntegrityError):
                await database.transaction(
                    lambda tx: campaigns.waive_lane_for_round(
                        tx,
                        replacement_graph.round_id,
                        replacement_graph.lane_id,
                        answer_id,
                        event_id,
                        22,
                    )
                )

            waiver_graph = await database.transaction(
                lambda tx: review_graph_builder(tx, suffix="waive", attempt_outcome="failed")
            )

            def waive(tx: Transaction):
                question = questions.create_question(
                    tx,
                    NewHumanQuestion(
                        run_id=waiver_graph.run_id,
                        branch_id=waiver_graph.branch_id,
                        stage_id=waiver_graph.stage_id,
                        campaign_id=waiver_graph.campaign_id,
                        round_id=waiver_graph.round_id,
                        finding_id=None,
                        reason="lane_failure",
                        question_text="waive lane",
                        options_json='["continue"]',
                        snapshot_json="{}",
                        asked_at=30,
                    ),
                )
                answer = tx.execute(
                    "INSERT INTO human_answer(question_id, raw_text, chosen_option, "
                    "interpreted_json, transport, update_id, received_at) "
                    "VALUES (?, 'continue', 'continue', NULL, 'cli', NULL, 31)",
                    (question.id,),
                )
                assert answer.lastrowid is not None
                event = append_run_event(
                    tx,
                    NewRunEvent(
                        run_id=waiver_graph.run_id,
                        kind="lane_waived.v1",
                        payload={},
                        created_at=31,
                    ),
                )
                return campaigns.waive_lane_for_round(
                    tx,
                    waiver_graph.round_id,
                    waiver_graph.lane_id,
                    answer.lastrowid,
                    event,
                    31,
                )

            waiver = await database.transaction(waive)
            assert waiver.lane_id == waiver_graph.lane_id
            with pytest.raises(sqlite3.IntegrityError):
                await database.transaction(
                    lambda tx: tx.execute(
                        "INSERT INTO lane_waiver(campaign_id, round_no, lane_id, "
                        "human_answer_id, event_id, created_at) VALUES (?, 1, ?, ?, ?, 32)",
                        (
                            waiver_graph.campaign_id,
                            waiver_graph.lane_id,
                            waiver.human_answer_id,
                            waiver.event_id,
                        ),
                    )
                )
        finally:
            await database.close()

    asyncio.run(scenario())


def test_finding_round_history_is_caller_scoped_sorted_and_does_not_filter_closed(
    database_factory: Callable[[], Awaitable],
    review_graph_builder: Callable[..., object],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        attempts = AttemptRepository()
        campaigns = CampaignRepository()
        findings = FindingRepository()
        try:
            graph = await database.transaction(review_graph_builder)

            def seed_history(tx: Transaction) -> tuple[int, tuple[int, ...]]:
                observation = findings.create_observation(
                    tx,
                    NewReviewObservation(
                        campaign_id=graph.campaign_id,
                        round_id=graph.round_id,
                        lane_id=graph.lane_id,
                        attempt_id=graph.attempt_id,
                        subject_id=graph.subject_id,
                        revision="rev-1",
                        title="history",
                        body="body",
                        file_path=None,
                        line_start=None,
                        line_end=None,
                        evidence=None,
                        severity_suggested="medium",
                        unchanged_from_id=None,
                        severity_effective="medium",
                        dedup_key="history",
                        created_at=20,
                    ),
                )
                reconciler = attempts.create_attempt(
                    tx,
                    NewStepAttempt(
                        public_id="A-history-reconciler",
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
                        prompt_hash="history",
                        rubric_id=None,
                        rubric_hash=None,
                        input_sha=None,
                        input_refs_json="[]",
                        manifest_json="{}",
                        started_at=21,
                    ),
                )
                reconciler = attempts.complete_attempt(
                    tx,
                    reconciler.id,
                    AttemptCompletion("succeeded", None, "gpt-test", "out", 22, None, None),
                )
                event = append_run_event(
                    tx,
                    NewRunEvent(
                        run_id=graph.run_id,
                        kind="history_finding.v1",
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
                        first_observation_id=observation.id,
                        first_revision="rev-1",
                        first_owner_lane_id=graph.lane_id,
                        title="history",
                        event_id=event,
                        created_at=22,
                    ),
                )
                findings.create_observation_link(
                    tx,
                    NewObservationLink(
                        observation.id,
                        graph.campaign_id,
                        graph.round_id,
                        finding.id,
                        "first_seen",
                        reconciler.id,
                        "reconciler",
                        "succeeded",
                        None,
                        None,
                        event,
                        22,
                    ),
                )
                round_ids = [graph.round_id]
                for round_no in (2, 3, 4):
                    round_ids.append(
                        campaigns.create_round(
                            tx,
                            NewReviewRound(
                                campaign_id=graph.campaign_id,
                                round_no=round_no,
                                kind="discovery",
                                preceding_revision_id=None,
                                opened_at=20 + round_no,
                            ),
                        ).id
                    )
                for round_no, round_id in enumerate(round_ids, start=1):
                    findings.create_finding_round(
                        tx,
                        NewFindingRound(
                            campaign_id=graph.campaign_id,
                            run_id=graph.run_id,
                            finding_id=finding.id,
                            round_no=round_no,
                            round_id=round_id,
                            owner_lane_id=graph.lane_id,
                            entry_kind="post_check" if round_no in (1, 3) else "issued",
                            disposition=None,
                            disposition_reason=None,
                            author_attempt_id=None,
                        ),
                    )
                close_event = append_run_event(
                    tx,
                    NewRunEvent(
                        run_id=graph.run_id,
                        kind="history_closed.v1",
                        payload={},
                        created_at=30,
                    ),
                )
                findings.create_resolution(
                    tx,
                    NewFindingResolution(
                        graph.run_id,
                        finding.id,
                        "verified_fixed",
                        "reviewer",
                        graph.campaign_id,
                        2,
                        None,
                        1,
                        close_event,
                        30,
                    ),
                )
                return finding.id, tuple(round_ids)

            finding_id, round_ids = await database.transaction(seed_history)
            history = await database.read(
                lambda db: findings.read_finding_round_history(
                    db, graph.campaign_id, (finding_id,)
                )
            )
            assert [entry.round_no for entry in history] == [1, 2, 3, 4]
            assert [entry.round_id for entry in history] == list(round_ids)
            assert [entry.entry_kind for entry in history] == [
                "post_check",
                "issued",
                "post_check",
                "issued",
            ]
            closed_status = await database.read(
                lambda db: findings.read_finding_status(db, finding_id)
            )
            assert closed_status is not None and closed_status.status == "closed"
            assert await database.read(
                lambda db: findings.read_finding_round_history(db, graph.campaign_id, ())
            ) == ()
        finally:
            await database.close()

    asyncio.run(scenario())


def test_finding_round_history_sorts_multiple_findings_keeps_closed_and_excludes_neighbor_campaign(
    database_factory: Callable[[], Awaitable],
    review_graph_builder: Callable[..., object],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        attempts = AttemptRepository()
        campaigns = CampaignRepository()
        findings = FindingRepository()
        try:
            graph = await database.transaction(
                lambda tx: review_graph_builder(tx, suffix="history-many")
            )

            def seed(tx: Transaction) -> tuple[tuple[int, int, int], int]:
                reconciler = attempts.create_attempt(
                    tx,
                    NewStepAttempt(
                        "A-history-many-reconciler",
                        graph.run_id,
                        graph.stage_id,
                        "reconciler",
                        graph.campaign_id,
                        graph.round_id,
                        None,
                        None,
                        "rev-1",
                        None,
                        "reviewer-history-many",
                        "gpt-test",
                        "reconcile.v1",
                        "history-many",
                        None,
                        None,
                        None,
                        "[]",
                        "{}",
                        100,
                    ),
                )
                reconciler = attempts.complete_attempt(
                    tx,
                    reconciler.id,
                    AttemptCompletion("succeeded", None, "gpt-test", "out", 101, None, None),
                )
                finding_ids: list[int] = []
                for index in range(1, 4):
                    observation = findings.create_observation(
                        tx,
                        NewReviewObservation(
                            graph.campaign_id,
                            graph.round_id,
                            graph.lane_id,
                            graph.attempt_id,
                            graph.subject_id,
                            "rev-1",
                            f"history-{index}",
                            "body",
                            None,
                            None,
                            None,
                            None,
                            "medium",
                            None,
                            "medium",
                            f"history-many-{index}",
                            101 + index,
                        ),
                    )
                    event_id = append_run_event(
                        tx,
                        NewRunEvent(
                            run_id=graph.run_id,
                            kind=f"history_many_{index}.v1",
                            payload={},
                            created_at=101 + index,
                        ),
                    )
                    finding = findings.create_finding(
                        tx,
                        NewFinding(
                            graph.run_id,
                            graph.subject_id,
                            graph.campaign_id,
                            graph.round_id,
                            observation.id,
                            "rev-1",
                            graph.lane_id,
                            f"history-{index}",
                            event_id,
                            101 + index,
                        ),
                    )
                    findings.create_observation_link(
                        tx,
                        NewObservationLink(
                            observation.id,
                            graph.campaign_id,
                            graph.round_id,
                            finding.id,
                            "first_seen",
                            reconciler.id,
                            "reconciler",
                            "succeeded",
                            None,
                            None,
                            event_id,
                            101 + index,
                        ),
                    )
                    findings.create_finding_round(
                        tx,
                        NewFindingRound(
                            graph.campaign_id,
                            graph.run_id,
                            finding.id,
                            1,
                            graph.round_id,
                            graph.lane_id,
                            "post_check",
                            None,
                            None,
                            None,
                        ),
                    )
                    finding_ids.append(finding.id)

                for index, finding_id in enumerate(finding_ids[1:], start=1):
                    close_event = append_run_event(
                        tx,
                        NewRunEvent(
                            run_id=graph.run_id,
                            kind=f"history_many_close_{index}.v1",
                            payload={},
                            created_at=110 + index,
                        ),
                    )
                    findings.create_resolution(
                        tx,
                        NewFindingResolution(
                            graph.run_id,
                            finding_id,
                            "verified_fixed",
                            "reviewer",
                            graph.campaign_id,
                            1,
                            None,
                            1,
                            close_event,
                            110 + index,
                        ),
                    )

                reopened_observation = findings.create_observation(
                    tx,
                    NewReviewObservation(
                        graph.campaign_id,
                        graph.round_id,
                        graph.lane_id,
                        graph.attempt_id,
                        graph.subject_id,
                        "rev-1",
                        "history-reopened",
                        "body",
                        None,
                        None,
                        None,
                        None,
                        "medium",
                        None,
                        "medium",
                        "history-reopened",
                        120,
                    ),
                )
                reopen_event = append_run_event(
                    tx,
                    NewRunEvent(
                        run_id=graph.run_id,
                        kind="history_many_reopen.v1",
                        payload={},
                        created_at=120,
                    ),
                )
                findings.create_observation_link(
                    tx,
                    NewObservationLink(
                        reopened_observation.id,
                        graph.campaign_id,
                        graph.round_id,
                        finding_ids[1],
                        "reopening",
                        reconciler.id,
                        "reconciler",
                        "succeeded",
                        None,
                        "reopened",
                        reopen_event,
                        120,
                    ),
                )

                neighbor = campaigns.create_campaign(
                    tx,
                    NewReviewCampaign(
                        "C-history-neighbor",
                        graph.run_id,
                        graph.stage_id,
                        graph.subject_id,
                        2,
                        "high",
                        "v1",
                        1,
                        121,
                    ),
                )
                neighbor_lane = campaigns.create_lane(
                    tx, NewReviewLane(neighbor.id, graph.run_id, 0)
                )
                neighbor_round = campaigns.create_round(
                    tx, NewReviewRound(neighbor.id, 1, "discovery", None, 121)
                )
                findings.create_finding_round(
                    tx,
                    NewFindingRound(
                        neighbor.id,
                        graph.run_id,
                        finding_ids[0],
                        1,
                        neighbor_round.id,
                        neighbor_lane.id,
                        "post_check",
                        None,
                        None,
                        None,
                    ),
                )
                return tuple(finding_ids), neighbor.id

            finding_ids, neighbor_id = await database.transaction(seed)
            history = await database.read(
                lambda db: findings.read_finding_round_history(
                    db,
                    graph.campaign_id,
                    tuple(reversed(finding_ids)),
                )
            )
            assert [entry.finding_id for entry in history] == list(finding_ids)
            assert len(history) == 3
            neighbor_history = await database.read(
                lambda db: findings.read_finding_round_history(
                    db, neighbor_id, (finding_ids[0],)
                )
            )
            assert neighbor_history[0].round_id != history[0].round_id
            reopened = await database.read(
                lambda db: findings.read_finding_status(db, finding_ids[1])
            )
            closed = await database.read(
                lambda db: findings.read_finding_status(db, finding_ids[2])
            )
            assert reopened is not None and reopened.status == "open"
            assert closed is not None and closed.status == "closed"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_aggregate_round_trip_projections_and_completeness_reads(
    database_factory: Callable[[], Awaitable],
    review_graph_builder: Callable[..., object],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        attempts = AttemptRepository()
        campaigns = CampaignRepository()
        findings = FindingRepository()
        questions = QuestionRepository()
        try:
            graph = await database.transaction(review_graph_builder)

            def create_review_rows(tx: Transaction) -> tuple[int, int, int, int]:
                observation = findings.create_observation(
                    tx,
                    NewReviewObservation(
                        campaign_id=graph.campaign_id,
                        round_id=graph.round_id,
                        lane_id=graph.lane_id,
                        attempt_id=graph.attempt_id,
                        subject_id=graph.subject_id,
                        revision="rev-1",
                        title="problem",
                        body="details",
                        file_path="a.py",
                        line_start=1,
                        line_end=2,
                        evidence="evidence",
                        severity_suggested="high",
                        unchanged_from_id=None,
                        severity_effective="high",
                        dedup_key="dedup",
                        created_at=20,
                    ),
                )
                raw = findings.read_reconciliation_observations(tx, graph.round_id)
                assert [item.id for item in raw] == [observation.id]
                event_id = append_run_event(
                    tx,
                    NewRunEvent(
                        run_id=graph.run_id,
                        branch_id=graph.branch_id,
                        stage_id=graph.stage_id,
                        kind="finding_created.v1",
                        payload={"observation_id": observation.id},
                        created_at=21,
                    ),
                )
                finding = findings.create_finding(
                    tx,
                    NewFinding(
                        run_id=graph.run_id,
                        subject_id=graph.subject_id,
                        first_campaign_id=graph.campaign_id,
                        first_round_id=graph.round_id,
                        first_observation_id=observation.id,
                        first_revision="rev-1",
                        first_owner_lane_id=graph.lane_id,
                        title="problem",
                        event_id=event_id,
                        created_at=21,
                    ),
                )
                reconciler = attempts.create_attempt(
                    tx,
                    NewStepAttempt(
                        public_id="A-reconciler",
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
                        prompt_hash="reconcile-hash",
                        rubric_id=None,
                        rubric_hash=None,
                        input_sha=None,
                        input_refs_json="[]",
                        manifest_json="{}",
                        started_at=22,
                    ),
                )
                reconciler = attempts.complete_attempt(
                    tx,
                    reconciler.id,
                    AttemptCompletion(
                        outcome="succeeded",
                        outcome_detail=None,
                        actual_model="gpt-test",
                        output_sha="reconciled",
                        finished_at=23,
                        transcript_path=None,
                        transcript_digest=None,
                    ),
                )
                link_event = append_run_event(
                    tx,
                    NewRunEvent(
                        run_id=graph.run_id,
                        kind="observation_linked.v1",
                        payload={"finding_id": finding.id},
                        created_at=24,
                    ),
                )
                findings.create_observation_link(
                    tx,
                    NewObservationLink(
                        observation_id=observation.id,
                        campaign_id=graph.campaign_id,
                        round_id=graph.round_id,
                        finding_id=finding.id,
                        link_type="first_seen",
                        decided_by_attempt_id=reconciler.id,
                        decided_by_role="reconciler",
                        decided_by_outcome="succeeded",
                        decided_by_human_answer_id=None,
                        reason=None,
                        event_id=link_event,
                        created_at=24,
                    ),
                )
                finding_round = findings.create_finding_round(
                    tx,
                    NewFindingRound(
                        campaign_id=graph.campaign_id,
                        run_id=graph.run_id,
                        finding_id=finding.id,
                        round_no=1,
                        round_id=graph.round_id,
                        owner_lane_id=graph.lane_id,
                        entry_kind="post_check",
                        disposition=None,
                        disposition_reason=None,
                        author_attempt_id=None,
                    ),
                )
                question = questions.create_question(
                    tx,
                    NewHumanQuestion(
                        run_id=graph.run_id,
                        branch_id=graph.branch_id,
                        stage_id=graph.stage_id,
                        campaign_id=graph.campaign_id,
                        round_id=graph.round_id,
                        finding_id=None,
                        reason="open_question",
                        question_text="Need input",
                        options_json=None,
                        snapshot_json="{}",
                        asked_at=25,
                    ),
                )
                questions.create_blocker(
                    tx,
                    NewBlocker(
                        run_id=graph.run_id,
                        kind="human_question",
                        branch_id=graph.branch_id,
                        task_id=None,
                        stage_id=graph.stage_id,
                        question_id=question.id,
                        detail="waiting",
                        created_at=25,
                        created_event_id=link_event,
                        cleared_at=None,
                        cleared_event_id=None,
                    ),
                )
                return observation.id, finding.id, finding_round.id, question.id

            observation_id, finding_id, _, question_id = await database.transaction(
                create_review_rows
            )
            result = await database.read(
                lambda db: (
                    campaigns.read_effective_roster(db, graph.campaign_id),
                    campaigns.read_campaign_counters(db, graph.campaign_id),
                    findings.read_scoped_finding_ledger(db, graph.campaign_id),
                    findings.read_current_finding_rounds(db, graph.round_id),
                    findings.read_finding_status(db, finding_id),
                    findings.read_dispute_candidates(db, graph.round_id),
                    findings.find_links_with_foreign_finding_run(db, graph.run_id),
                    questions.get_question(db, question_id),
                    questions.read_question_observations(db, question_id),
                    RunRepository().read_open_blockers(db, graph.run_id),
                    campaigns.find_missing_discovery_lane_participation(db, graph.round_id),
                    campaigns.find_discovery_without_successful_opinion(db, graph.round_id),
                    campaigns.find_discovery_roster_cardinality_mismatch(db, graph.round_id),
                    campaigns.find_unlinked_observations(db, graph.round_id),
                    campaigns.find_missing_finding_participation(db, graph.round_id),
                    campaigns.find_incomplete_issued(db, graph.round_id),
                    RunRepository().find_human_blockers_on_unblocked_branches(
                        db, graph.run_id
                    ),
                    RunRepository().find_blocked_branches_without_blocker(db, graph.run_id),
                    RunRepository().find_questions_without_matching_blocker(db, graph.run_id),
                )
            )
            (
                roster,
                counters,
                ledger,
                current,
                status,
                disputes,
                foreign_links,
                question,
                question_observations,
                blockers,
                missing_lanes,
                no_opinion,
                roster_mismatch,
                unlinked,
                missing,
                issued,
                human_on_running,
                blocked_without_reason,
                question_without_blocker,
            ) = result
            assert roster[0].lane_index == 0
            assert counters is not None and counters.review_check_count == 0
            assert ledger.open == (
                OpenFindingLedgerRecord(
                    finding_id=finding_id,
                    public_id=f"F-{finding_id}",
                    subject_id=graph.subject_id,
                    title="problem",
                    last_resolution=None,
                    last_authority=None,
                    period_start_event_id=2,
                    escalation_severity="high",
                    historical_max="high",
                ),
            )
            assert current[0].entry_kind == "post_check"
            assert status is not None and status.status == "open"
            assert disputes == ()
            assert foreign_links == ()
            assert question is not None and question.id == question_id
            assert question_observations == ()
            assert blockers[0].branch_id == graph.branch_id
            assert (missing_lanes, no_opinion, roster_mismatch) == ((), (), ())
            assert (unlinked, missing, issued) == ((), (), ())
            assert human_on_running == (blockers[0].blocker_id,)
            assert blocked_without_reason == ()
            assert question_without_blocker == ()
            assert observation_id > 0
            with pytest.raises(FrozenInstanceError):
                roster[0].profile_id = "changed"  # type: ignore[misc]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_discovery_cardinality_detects_a_declared_but_unbuilt_roster(
    database_factory: Callable[[], Awaitable],
    review_graph_builder: Callable[..., object],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        campaigns = CampaignRepository()
        try:
            graph = await database.transaction(
                lambda tx: review_graph_builder(tx, expected_lane_count=2)
            )
            results = await database.read(
                lambda db: (
                    campaigns.find_missing_discovery_lane_participation(db, graph.round_id),
                    campaigns.find_discovery_without_successful_opinion(db, graph.round_id),
                    campaigns.find_discovery_roster_cardinality_mismatch(db, graph.round_id),
                )
            )
            # Existing lane 0 worked, so only the independent cardinality read can
            # see that the declared second slot was never materialized.
            assert results == ((), (), (graph.campaign_id,))

            lane = await database.transaction(
                lambda tx: campaigns.create_lane(
                    tx,
                    NewReviewLane(
                        campaign_id=graph.campaign_id,
                        run_id=graph.run_id,
                        lane_index=1,
                    ),
                )
            )
            results = await database.read(
                lambda db: (
                    campaigns.find_missing_discovery_lane_participation(db, graph.round_id),
                    campaigns.find_discovery_roster_cardinality_mismatch(db, graph.round_id),
                )
            )
            assert results == ((lane.id,), (graph.campaign_id,))
        finally:
            await database.close()

    asyncio.run(scenario())


def test_all_completeness_reads_require_existing_round(
    database_factory: Callable[[], Awaitable],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        campaigns = CampaignRepository()
        methods = (
            campaigns.find_missing_discovery_lane_participation,
            campaigns.find_discovery_without_successful_opinion,
            campaigns.find_discovery_roster_cardinality_mismatch,
            campaigns.find_unlinked_observations,
            campaigns.find_missing_finding_participation,
            campaigns.find_incomplete_issued,
        )
        try:
            for method in methods:
                with pytest.raises(RepositoryRecordNotFound):
                    await database.read(lambda db, method=method: method(db, 404))
        finally:
            await database.close()

    asyncio.run(scenario())


def test_exposure_membership_allows_retry_and_reconciler_but_not_second_lane(
    database_factory: Callable[[], Awaitable],
    review_graph_builder: Callable[..., object],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        attempts = AttemptRepository()
        campaigns = CampaignRepository()
        try:
            graph = await database.transaction(
                lambda tx: review_graph_builder(
                    tx, attempt_outcome="contract_error", expected_lane_count=2
                )
            )
            first = await database.transaction(
                lambda tx: attempts.reserve_reviewer_exposure(tx, graph.attempt_id)
            )

            def create_retry_and_reconciler(tx: Transaction) -> tuple[int, int]:
                retry = attempts.create_attempt(
                    tx,
                    NewStepAttempt(
                        public_id="A-retry",
                        run_id=graph.run_id,
                        stage_id=graph.stage_id,
                        role="reviewer",
                        campaign_id=graph.campaign_id,
                        round_id=graph.round_id,
                        lane_id=graph.lane_id,
                        lane_assignment_id=graph.assignment_id,
                        subject_revision="rev-1",
                        session_id=None,
                        profile_id="reviewer-1",
                        requested_model="gpt-test",
                        prompt_template_id="review.v1",
                        prompt_hash="retry",
                        rubric_id="rubric",
                        rubric_hash="rubric-hash",
                        input_sha="input",
                        input_refs_json="[]",
                        manifest_json="{}",
                        started_at=20,
                    ),
                )
                retry_exposure = attempts.reserve_reviewer_exposure(tx, retry.id)
                retry = attempts.complete_attempt(
                    tx,
                    retry.id,
                    AttemptCompletion("interrupted", None, None, None, 21, None, None),
                )
                reconciler = attempts.create_attempt(
                    tx,
                    NewStepAttempt(
                        public_id="A-reconcile-exposure",
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
                        prompt_hash="reconcile",
                        rubric_id=None,
                        rubric_hash=None,
                        input_sha=None,
                        input_refs_json="[]",
                        manifest_json="{}",
                        started_at=22,
                    ),
                )
                reconcile_exposure = attempts.reserve_reviewer_exposure(tx, reconciler.id)
                return retry_exposure.first_attempt_id, reconcile_exposure.first_attempt_id

            assert await database.transaction(create_retry_and_reconciler) == (
                first.first_attempt_id,
                first.first_attempt_id,
            )

            def conflicting_lane(tx: Transaction) -> None:
                event_id = append_run_event(
                    tx,
                    NewRunEvent(
                        run_id=graph.run_id,
                        kind="lane_added.v1",
                        payload={},
                        created_at=30,
                    ),
                )
                lane = campaigns.create_lane(
                    tx,
                    NewReviewLane(campaign_id=graph.campaign_id, run_id=graph.run_id, lane_index=1),
                )
                tx.execute(
                    "INSERT INTO run_profile_resolution(run_id, profile_id, provider, model, "
                    "resolved_at) VALUES (?, 'reviewer-other', 'openai', 'gpt-test', 30)",
                    (graph.run_id,),
                )
                assignment = campaigns.create_lane_assignment(
                    tx,
                    NewLaneAssignment(
                        lane_id=lane.id,
                        run_id=graph.run_id,
                        generation=1,
                        profile_id="reviewer-other",
                        replaces_id=None,
                        session_id=None,
                        human_answer_id=None,
                        event_id=event_id,
                        assigned_at=30,
                    ),
                )
                attempt = attempts.create_attempt(
                    tx,
                    NewStepAttempt(
                        public_id="A-other-lane",
                        run_id=graph.run_id,
                        stage_id=graph.stage_id,
                        role="reviewer",
                        campaign_id=graph.campaign_id,
                        round_id=graph.round_id,
                        lane_id=lane.id,
                        lane_assignment_id=assignment.id,
                        subject_revision="rev-1",
                        session_id=None,
                        profile_id="reviewer-other",
                        requested_model="gpt-test",
                        prompt_template_id="review.v1",
                        prompt_hash="other",
                        rubric_id="rubric",
                        rubric_hash="rubric-hash",
                        input_sha="input",
                        input_refs_json="[]",
                        manifest_json="{}",
                        started_at=31,
                    ),
                )
                attempts.reserve_reviewer_exposure(tx, attempt.id)

            with pytest.raises(ReviewerExposureConflict):
                await database.transaction(conflicting_lane)
            assert await database.read(
                lambda db: db.fetch_one(
                    "SELECT COUNT(*) FROM step_attempt WHERE public_id = 'A-other-lane'"
                )[0]
            ) == 0
        finally:
            await database.close()

    asyncio.run(scenario())
