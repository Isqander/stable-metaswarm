from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable

import pytest

from metaswarm.store import NewRunEvent, Transaction, append_run_event
from metaswarm.store.repo import (
    AttemptCompletion,
    AttemptRepository,
    FindingRepository,
    NewFinding,
    NewFindingResolution,
    NewFindingRound,
    NewHumanQuestion,
    NewHumanQuestionObservation,
    NewObservationLink,
    NewReviewObservation,
    NewSeverityOverride,
    NewStepAttempt,
    QuestionRepository,
    ReviewerDecisionWrite,
)

CLOSED_ENUM_FOREIGN_KEYS = frozenset(
    {
        ("artifact_revision", "kind", "artifact_kind"),
        ("artifact_revision", "produced_by", "artifact_producer"),
        ("attempt_liveness", "heartbeat_source", "heartbeat_source"),
        ("blocker", "kind", "blocker_kind"),
        ("branch", "kind", "branch_kind"),
        ("branch", "state", "branch_state"),
        ("finding", "title_authority", "title_authority"),
        ("finding_observation_link", "link_type", "link_type"),
        ("finding_resolution", "resolution", "resolution_kind"),
        ("finding_resolution", "resolution_authority", "resolution_authority"),
        ("finding_round", "disposition", "disposition"),
        ("finding_round", "entry_kind", "finding_round_entry_kind"),
        ("finding_round", "reviewer_decision", "reviewer_decision"),
        ("human_answer", "transport", "transport_kind"),
        ("human_question", "reason", "question_reason"),
        ("human_question_observation", "reason", "question_reason"),
        ("notification_outbox", "transport", "transport_kind"),
        ("review_campaign", "severity_threshold", "severity_scale"),
        ("review_campaign", "state", "campaign_state"),
        ("review_observation", "severity_effective", "severity_scale"),
        ("review_observation", "severity_suggested", "severity_scale"),
        ("review_round", "kind", "review_round_kind"),
        ("review_round", "result", "round_result"),
        ("review_subject", "kind", "subject_kind"),
        ("run", "terminal_state", "run_terminal_state"),
        ("severity_override", "new_severity", "severity_scale"),
        ("severity_override", "old_severity", "severity_scale"),
        ("stage_execution", "severity_threshold", "severity_scale"),
        ("stage_execution", "state", "branch_state"),
        ("step_attempt", "outcome", "attempt_outcome"),
        ("step_attempt", "role", "attempt_role"),
        ("task", "state", "task_state"),
        ("telegram_cursor", "transport", "transport_kind"),
        ("telegram_inbox", "transport", "transport_kind"),
        ("verification_run", "plan_source", "verification_plan_source"),
        ("verification_run", "purpose", "verification_purpose"),
        ("verification_run", "status", "verification_status"),
    }
)

_LOOKUP_TABLES = frozenset(item[2] for item in CLOSED_ENUM_FOREIGN_KEYS)


def test_closed_enum_manifest_matches_all_37_runtime_child_columns(
    database_factory: Callable[[], Awaitable],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        try:
            def inspect(db) -> frozenset[tuple[str, str, str]]:
                tables = tuple(
                    row["name"]
                    for row in db.fetch_all(
                        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
                    )
                )
                actual: set[tuple[str, str, str]] = set()
                for table in tables:
                    for foreign_key in db.fetch_all(f"PRAGMA foreign_key_list({table})"):
                        if foreign_key["table"] in _LOOKUP_TABLES:
                            actual.add((table, foreign_key["from"], foreign_key["table"]))
                # campaign_transition and resolution_kind are migration-owned lookup data,
                # not runtime child columns from the plan's 37-row manifest.
                actual -= {
                    ("campaign_transition", "from_state", "campaign_state"),
                    ("campaign_transition", "to_state", "campaign_state"),
                    ("resolution_kind", "resolution_authority", "resolution_authority"),
                }
                return frozenset(actual)

            assert await database.read(inspect) == CLOSED_ENUM_FOREIGN_KEYS
            assert len(CLOSED_ENUM_FOREIGN_KEYS) == 37
        finally:
            await database.close()

    asyncio.run(scenario())


def test_closed_enum_unknown_values_are_rejected_on_connected_review_rows(
    database_factory: Callable[[], Awaitable],
    review_graph_builder: Callable[..., object],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        try:
            graph = await database.transaction(review_graph_builder)
            statements = (
                ("UPDATE branch SET kind='unknown' WHERE id=?", graph.branch_id),
                ("UPDATE branch SET state='unknown' WHERE id=?", graph.branch_id),
                (
                    "UPDATE stage_execution SET severity_threshold='unknown' WHERE id=?",
                    graph.stage_id,
                ),
                ("UPDATE stage_execution SET state='unknown' WHERE id=?", graph.stage_id),
            )
            for sql, id_value in statements:
                with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
                    await database.transaction(
                        lambda tx, sql=sql, id_value=id_value: tx.execute(sql, (id_value,))
                    )
            assert await database.read(
                lambda db: db.fetch_one("PRAGMA foreign_key_check")
            ) is None
        finally:
            await database.close()

    asyncio.run(scenario())


def _seed_append_only_rows(tx: Transaction, graph: object) -> dict[str, str]:
    attempts = AttemptRepository()
    findings = FindingRepository()
    questions = QuestionRepository()
    exposure = attempts.reserve_reviewer_exposure(tx, graph.attempt_id)
    observation = findings.create_observation(
        tx,
        NewReviewObservation(
            campaign_id=graph.campaign_id,
            round_id=graph.round_id,
            lane_id=graph.lane_id,
            attempt_id=graph.attempt_id,
            subject_id=graph.subject_id,
            revision="rev-1",
            title="append-only",
            body="body",
            file_path=None,
            line_start=None,
            line_end=None,
            evidence=None,
            severity_suggested="high",
            unchanged_from_id=None,
            severity_effective="high",
            dedup_key="append-only",
            created_at=20,
        ),
    )
    reconciler = attempts.create_attempt(
        tx,
        NewStepAttempt(
            public_id="A-schema-reconciler",
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
            started_at=21,
        ),
    )
    reconciler = attempts.complete_attempt(
        tx,
        reconciler.id,
        AttemptCompletion("succeeded", None, "gpt-test", "out", 22, None, None),
    )
    event_id = append_run_event(
        tx,
        NewRunEvent(
            run_id=graph.run_id,
            kind="schema_rows.v1",
            payload={},
            created_at=23,
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
            title="append-only",
            event_id=event_id,
            created_at=23,
        ),
    )
    link_event = append_run_event(
        tx,
        NewRunEvent(
            run_id=graph.run_id,
            kind="schema_link.v1",
            payload={},
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
    resolution_event = append_run_event(
        tx,
        NewRunEvent(
            run_id=graph.run_id,
            kind="schema_resolution.v1",
            payload={},
            created_at=25,
        ),
    )
    resolution = findings.create_resolution(
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
            event_id=resolution_event,
            created_at=25,
        ),
    )
    override_event = append_run_event(
        tx,
        NewRunEvent(
            run_id=graph.run_id,
            kind="schema_override.v1",
            payload={},
            created_at=26,
        ),
    )
    override = findings.create_severity_override(
        tx,
        NewSeverityOverride(
            finding_id=finding.id,
            old_severity="high",
            new_severity="medium",
            reason="human",
            human_answer_id=None,
            event_id=override_event,
            created_at=26,
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
            reason="reconcile_failed",
            question_text="classify",
            options_json=None,
            snapshot_json="{}",
            asked_at=27,
        ),
    )
    membership = questions.create_question_observation(
        tx,
        NewHumanQuestionObservation(
            question_id=question.id,
            observation_id=observation.id,
            campaign_id=graph.campaign_id,
            round_id=graph.round_id,
            run_id=graph.run_id,
            reason="reconcile_failed",
            finding_id=None,
        ),
    )
    answer_result = tx.execute(
        "INSERT INTO human_answer(question_id, raw_text, chosen_option, interpreted_json, "
        "transport, update_id, received_at) VALUES (?, 'answer', NULL, '{}', 'cli', NULL, 28)",
        (question.id,),
    )
    assert answer_result.lastrowid is not None
    waiver_question = questions.create_question(
        tx,
        NewHumanQuestion(
            run_id=graph.run_id,
            branch_id=graph.branch_id,
            stage_id=graph.stage_id,
            campaign_id=graph.campaign_id,
            round_id=graph.round_id,
            finding_id=None,
            reason="lane_failure",
            question_text="waive",
            options_json='["continue"]',
            snapshot_json="{}",
            asked_at=29,
        ),
    )
    waiver_answer = tx.execute(
        "INSERT INTO human_answer(question_id, raw_text, chosen_option, interpreted_json, "
        "transport, update_id, received_at) VALUES (?, 'continue', 'continue', NULL, "
        "'cli', NULL, 30)",
        (waiver_question.id,),
    )
    assert waiver_answer.lastrowid is not None
    waiver = tx.execute(
        "INSERT INTO lane_waiver(campaign_id, round_no, lane_id, human_answer_id, "
        "event_id, created_at) VALUES (?, 1, ?, ?, ?, 30)",
        (graph.campaign_id, graph.lane_id, waiver_answer.lastrowid, override_event),
    )
    assert waiver.lastrowid is not None
    return {
        "review_lane": f"id={graph.lane_id}",
        "lane_waiver": f"id={waiver.lastrowid}",
        "review_observation": f"id={observation.id}",
        "finding_observation_link": f"observation_id={observation.id}",
        "finding_resolution": f"id={resolution.id}",
        "severity_override": f"id={override.id}",
        "reviewer_exposure": f"id={exposure.id}",
        "run_profile_resolution": (
            f"run_id={graph.run_id} AND profile_id='reviewer-1'"
        ),
        "human_question_observation": (
            f"question_id={membership.question_id} AND observation_id={membership.observation_id}"
        ),
        "human_answer": f"id={answer_result.lastrowid}",
    }


def test_all_ten_review_core_append_only_tables_reject_update_delete_and_replace(
    database_factory: Callable[[], Awaitable],
    review_graph_builder: Callable[..., object],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        try:
            graph = await database.transaction(review_graph_builder)
            selectors = await database.transaction(
                lambda tx: _seed_append_only_rows(tx, graph)
            )
            assert len(selectors) == 10
            for table, where in selectors.items():
                for statement in (
                    f"UPDATE {table} SET {where.split('=')[0]}={where.split('=')[0]} WHERE {where}",
                    f"DELETE FROM {table} WHERE {where}",
                    f"INSERT OR REPLACE INTO {table} SELECT * FROM {table} WHERE {where}",
                ):
                    with pytest.raises(sqlite3.IntegrityError):
                        await database.transaction(
                            lambda tx, statement=statement: tx.execute(statement)
                        )
            assert await database.read(
                lambda db: db.fetch_one("PRAGMA foreign_key_check")
            ) is None
        finally:
            await database.close()

    asyncio.run(scenario())


def test_constraint_and_lifecycle_guards_run_on_the_real_schema(
    database_factory: Callable[[], Awaitable],
    review_graph_builder: Callable[..., object],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        attempts = AttemptRepository()
        findings = FindingRepository()
        try:
            graph = await database.transaction(review_graph_builder)
            invalid_statements = (
                ("UPDATE stage_execution SET max_author_revisions=0 WHERE id=?", graph.stage_id),
                ("UPDATE review_campaign SET expected_lane_count=2 WHERE id=?", graph.campaign_id),
                ("DELETE FROM step_attempt WHERE id=?", graph.attempt_id),
                ("DELETE FROM review_round WHERE id=?", graph.round_id),
            )
            for sql, id_value in invalid_statements:
                with pytest.raises(sqlite3.IntegrityError):
                    await database.transaction(
                        lambda tx, sql=sql, id_value=id_value: tx.execute(sql, (id_value,))
                    )

            def create_issued(tx: Transaction) -> int:
                observation = findings.create_observation(
                    tx,
                    NewReviewObservation(
                        campaign_id=graph.campaign_id,
                        round_id=graph.round_id,
                        lane_id=graph.lane_id,
                        attempt_id=graph.attempt_id,
                        subject_id=graph.subject_id,
                        revision="rev-1",
                        title="issued",
                        body="body",
                        file_path=None,
                        line_start=None,
                        line_end=None,
                        evidence=None,
                        severity_suggested="medium",
                        unchanged_from_id=None,
                        severity_effective="medium",
                        dedup_key="issued",
                        created_at=40,
                    ),
                )
                event_id = append_run_event(
                    tx,
                    NewRunEvent(
                        run_id=graph.run_id,
                        kind="issued.v1",
                        payload={},
                        created_at=40,
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
                        title="issued",
                        event_id=event_id,
                        created_at=40,
                    ),
                )
                author = attempts.create_attempt(
                    tx,
                    NewStepAttempt(
                        public_id="A-schema-author",
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
                        input_sha=None,
                        input_refs_json="[]",
                        manifest_json="{}",
                        started_at=41,
                    ),
                )
                author = attempts.complete_attempt(
                    tx,
                    author.id,
                    AttemptCompletion("succeeded", None, "author-model", "rev-2", 42, None, None),
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
                        entry_kind="issued",
                        disposition="fixed",
                        disposition_reason=None,
                        author_attempt_id=author.id,
                    ),
                )
                findings.record_reviewer_decision(
                    tx,
                    finding_round.id,
                    ReviewerDecisionWrite("verified_fixed", graph.attempt_id, 43),
                )
                return finding_round.id

            finding_round_id = await database.transaction(create_issued)
            with pytest.raises(sqlite3.IntegrityError, match="terminal"):
                await database.transaction(
                    lambda tx: findings.record_reviewer_decision(
                        tx,
                        finding_round_id,
                        ReviewerDecisionWrite("verified_fixed", graph.attempt_id, 44),
                    )
                )
            question_id = await database.transaction(
                lambda tx: QuestionRepository()
                .create_question(
                    tx,
                    NewHumanQuestion(
                        run_id=graph.run_id,
                        branch_id=graph.branch_id,
                        stage_id=graph.stage_id,
                        campaign_id=None,
                        round_id=None,
                        finding_id=None,
                        reason="open_question",
                        question_text="original",
                        options_json=None,
                        snapshot_json=None,
                        asked_at=50,
                    ),
                )
                .id
            )
            await database.transaction(
                lambda tx: tx.execute(
                    "UPDATE human_question SET reask_count = reask_count + 1 WHERE id = ?",
                    (question_id,),
                )
            )
            with pytest.raises(sqlite3.IntegrityError, match="content"):
                await database.transaction(
                    lambda tx: tx.execute(
                        "UPDATE human_question SET question_text='changed' WHERE id=?",
                        (question_id,),
                    )
                )
        finally:
            await database.close()

    asyncio.run(scenario())
