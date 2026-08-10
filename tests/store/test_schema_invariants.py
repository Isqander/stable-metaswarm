from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable

import pytest

from metaswarm.store import NewRunEvent, Transaction, append_run_event
from metaswarm.store.repo import (
    AttemptCompletion,
    AttemptRepository,
    CampaignRepository,
    FindingRepository,
    NewAuthorRevision,
    NewFinding,
    NewFindingResolution,
    NewFindingRound,
    NewHumanQuestion,
    NewHumanQuestionObservation,
    NewLaneAssignment,
    NewObservationLink,
    NewReviewCampaign,
    NewReviewLane,
    NewReviewObservation,
    NewReviewRound,
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


def _seed_all_closed_enum_child_tables(tx: Transaction, graph: object) -> None:
    selectors = _seed_append_only_rows(tx, graph)
    assert len(selectors) == 10
    _, finding_round_id = _create_finding_with_issued_round(tx, graph, "enum")
    FindingRepository().record_reviewer_decision(
        tx,
        finding_round_id,
        ReviewerDecisionWrite("verified_fixed", graph.attempt_id, 129),
    )
    tx.execute(
        "INSERT INTO run(public_id,flow_id,flow_hash,project_config_hash,profiles_config_hash,"
        "core_version,schema_version,instance_profile,code_repo_path,code_sha,task_text,"
        "created_at,pause_requested_at,cancel_requested_at,finished_at,terminal_state) "
        "VALUES ('R-enum-terminal','flow','flow','project','profiles','test-core',1,'test',"
        "'/code','sha','task',129,NULL,NULL,129,'succeeded')"
    )
    epoch = tx.execute(
        "INSERT INTO service_epoch(started_at,ended_at,core_version,schema_version,pid,"
        "boot_id,host) VALUES (1,NULL,'test-core',1,1,NULL,'host')"
    )
    assert epoch.lastrowid is not None
    tx.execute(
        "INSERT INTO attempt_liveness(attempt_id,service_epoch_id,pid,pgid,proc_start_ticks,"
        "started_mono_ns,last_heartbeat_mono_ns,last_heartbeat_at,heartbeat_source) "
        "VALUES (?,?,1,1,1,1,1,1,'stdout')",
        (graph.attempt_id, epoch.lastrowid),
    )
    tx.execute(
        "INSERT INTO blocker(run_id,kind,branch_id,task_id,stage_id,question_id,detail,"
        "created_at,created_event_id,cleared_at,cleared_event_id) "
        "VALUES (?,'dependency',?,NULL,?,NULL,NULL,130,?,NULL,NULL)",
        (graph.run_id, graph.branch_id, graph.stage_id, graph.event_id),
    )
    imported = _create_task_import(tx, graph, "enum")
    _create_task(tx, graph, imported, "enum-task")
    question_id = tx.fetch_one("SELECT id FROM human_question ORDER BY id LIMIT 1")["id"]
    tx.execute(
        "INSERT INTO notification_outbox(run_id,question_id,transport,target_ref,body,"
        "reply_markup,created_at,sent_at,transport_message_id,attempts,last_error) "
        "VALUES (?,?,'cli',NULL,'body',NULL,130,NULL,NULL,0,NULL)",
        (graph.run_id, question_id),
    )
    tx.execute(
        "INSERT INTO telegram_inbox(transport,update_id,payload,received_at,handled_at) "
        "VALUES ('telegram',1,'{}',130,NULL)"
    )
    tx.execute("INSERT INTO telegram_cursor(transport,next_offset) VALUES ('telegram',1)")
    tx.execute(
        "INSERT INTO artifact_revision(run_id,stage_id,kind,logical_path,revision_no,"
        "content_digest,code_sha,repo_commit,produced_by_attempt_id,produced_by,"
        "manifest_json,created_at) VALUES (?,?,'notes','enum-notes',1,'digest','sha',"
        "NULL,NULL,'human','{}',130)",
        (graph.run_id, graph.stage_id),
    )
    tx.execute(
        "INSERT INTO verification_run(run_id,stage_id,purpose,code_sha,plan_json,plan_source,"
        "policy_allowed,policy_rejection_reason,result_json,status,failure_signature,"
        "started_at,finished_at) VALUES (?,?,'baseline','sha','{}','recipe',1,NULL,'{}',"
        "'green',NULL,130,131)",
        (graph.run_id, graph.stage_id),
    )
    CampaignRepository().close_round(tx, graph.round_id, "clean", 132)


@pytest.mark.parametrize(
    ("table", "column", "lookup"),
    sorted(CLOSED_ENUM_FOREIGN_KEYS),
    ids=[f"{table}.{column}" for table, column, _ in sorted(CLOSED_ENUM_FOREIGN_KEYS)],
)
def test_closed_enum_unknown_values_are_rejected_for_each_child_column(
    table: str,
    column: str,
    lookup: str,
    database_factory: Callable[[], Awaitable],
    review_graph_builder: Callable[..., object],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        try:
            graph = await database.transaction(review_graph_builder)
            await database.transaction(
                lambda tx: _seed_all_closed_enum_child_tables(tx, graph)
            )
            before = await database.read(
                lambda db: db.fetch_one(
                    f"SELECT {column} AS value FROM {table} WHERE {column} IS NOT NULL "
                    "ORDER BY rowid LIMIT 1"
                )["value"]
            )
            with pytest.raises(sqlite3.IntegrityError):
                await database.transaction(
                    lambda tx: tx.execute(
                        f"UPDATE {table} SET {column}='__unknown__' "
                        "WHERE rowid=(SELECT rowid FROM "
                        f"{table} WHERE {column} IS NOT NULL ORDER BY rowid LIMIT 1)"
                    )
                )
            after = await database.read(
                lambda db: db.fetch_one(
                    f"SELECT {column} AS value FROM {table} WHERE {column} IS NOT NULL "
                    "ORDER BY rowid LIMIT 1"
                )["value"]
            )
            assert after == before
            lookup_key = await database.read(
                lambda db: next(
                    row["name"]
                    for row in db.fetch_all(f"PRAGMA table_info({lookup})")
                    if row["pk"] == 1
                )
            )
            with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
                await database.transaction(
                    lambda tx: tx.execute(
                        f"UPDATE {lookup} SET {lookup_key}='__unknown_parent__' "
                        f"WHERE {lookup_key}=?",
                        (before,),
                    )
                )
            assert await database.read(
                lambda db: db.fetch_one(
                    f"SELECT 1 FROM {lookup} WHERE rowid IS NOT NULL LIMIT 1"
                )
            ) is not None
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


@pytest.mark.parametrize(
    ("table", "operation"),
    [
        (table, operation)
        for table in (
            "review_lane",
            "lane_waiver",
            "review_observation",
            "finding_observation_link",
            "finding_resolution",
            "severity_override",
            "reviewer_exposure",
            "run_profile_resolution",
            "human_question_observation",
            "human_answer",
        )
        for operation in ("update", "delete", "replace")
    ],
    ids=lambda value: value,
)
def test_all_ten_review_core_append_only_tables_reject_update_delete_and_replace(
    table: str,
    operation: str,
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
            where = selectors[table]
            statements = {
                "update": (
                    f"UPDATE {table} SET {where.split('=')[0]}={where.split('=')[0]} "
                    f"WHERE {where}"
                ),
                "delete": f"DELETE FROM {table} WHERE {where}",
                "replace": f"INSERT OR REPLACE INTO {table} SELECT * FROM {table} WHERE {where}",
            }
            with pytest.raises(sqlite3.IntegrityError):
                await database.transaction(
                    lambda tx: tx.execute(statements[operation])
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


_ATTEMPT_PROTECTED_MUTATIONS = (
    ("id", "id + 10000"),
    ("public_id", "public_id || '-changed'"),
    ("run_id", "run_id + 10000"),
    ("stage_id", "stage_id + 10000"),
    ("role", "'author'"),
    ("campaign_id", "NULL"),
    ("round_id", "NULL"),
    ("lane_id", "NULL"),
    ("lane_assignment_id", "NULL"),
    ("subject_revision", "'other-revision'"),
    ("session_id", "10000"),
    ("profile_id", "'other-profile'"),
    ("requested_model", "'other-model'"),
    ("prompt_template_id", "'other-template'"),
    ("prompt_hash", "'other-prompt-hash'"),
    ("rubric_id", "'other-rubric'"),
    ("rubric_hash", "'other-rubric-hash'"),
    ("input_sha", "'other-input'"),
    ("input_refs_json", "'[1]'"),
    ("manifest_json", "'{\"changed\":true}'"),
    ("started_at", "started_at + 1"),
)


@pytest.mark.parametrize(
    ("column", "expression"),
    _ATTEMPT_PROTECTED_MUTATIONS,
    ids=[column for column, _ in _ATTEMPT_PROTECTED_MUTATIONS],
)
def test_attempt_completion_cannot_rewrite_each_identity_scope_or_input_field(
    column: str,
    expression: str,
    database_factory: Callable[[], Awaitable],
    review_graph_builder: Callable[..., object],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        try:
            graph = await database.transaction(
                lambda tx: review_graph_builder(tx, attempt_outcome=None)
            )
            statement = (
                "UPDATE step_attempt SET outcome='succeeded', finished_at=100, "
                f"{column}={expression} WHERE id=?"
            )
            with pytest.raises(
                sqlite3.IntegrityError,
                match="step_attempt allows one active-to-terminal update",
            ):
                await database.transaction(
                    lambda tx: tx.execute(statement, (graph.attempt_id,))
                )
            row = await database.read(
                lambda db: db.fetch_one(
                    "SELECT outcome, finished_at FROM step_attempt WHERE id=?",
                    (graph.attempt_id,),
                )
            )
            assert row is not None and tuple(row) == (None, None)
        finally:
            await database.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "operation",
    (
        "terminal_insert",
        "outcome_without_finished_at",
        "finished_at_without_outcome",
        "delete_active",
        "replace_active",
        "outcome_back_to_null",
        "rewrite_terminal_payload",
        "change_terminal_outcome",
        "replace_terminal_with_active",
    ),
)
def test_attempt_lifecycle_rejects_every_bypass_on_the_real_schema(
    operation: str,
    database_factory: Callable[[], Awaitable],
    review_graph_builder: Callable[..., object],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        attempts = AttemptRepository()
        try:
            graph = await database.transaction(
                lambda tx: review_graph_builder(tx, attempt_outcome=None)
            )
            if operation in {
                "outcome_back_to_null",
                "rewrite_terminal_payload",
                "change_terminal_outcome",
                "replace_terminal_with_active",
            }:
                await database.transaction(
                    lambda tx: attempts.complete_attempt(
                        tx,
                        graph.attempt_id,
                        AttemptCompletion(
                            "succeeded", "done", "gpt-test", "output", 100, "t", "d"
                        ),
                    )
                )

            statements = {
                "terminal_insert": (
                    "INSERT INTO step_attempt("
                    "public_id,run_id,stage_id,role,campaign_id,round_id,lane_id,"
                    "lane_assignment_id,subject_revision,session_id,profile_id,"
                    "requested_model,prompt_template_id,prompt_hash,rubric_id,rubric_hash,"
                    "input_sha,input_refs_json,manifest_json,started_at,outcome,finished_at) "
                    "SELECT public_id||'-terminal',run_id,stage_id,role,campaign_id,round_id,"
                    "lane_id,lane_assignment_id,subject_revision,session_id,profile_id,"
                    "requested_model,prompt_template_id,prompt_hash,rubric_id,rubric_hash,"
                    "input_sha,input_refs_json,manifest_json,started_at,'succeeded',100 "
                    "FROM step_attempt WHERE id=?"
                ),
                "outcome_without_finished_at": (
                    "UPDATE step_attempt SET outcome='succeeded' WHERE id=?"
                ),
                "finished_at_without_outcome": (
                    "UPDATE step_attempt SET finished_at=100 WHERE id=?"
                ),
                "delete_active": "DELETE FROM step_attempt WHERE id=?",
                "replace_active": (
                    "INSERT OR REPLACE INTO step_attempt SELECT * FROM step_attempt WHERE id=?"
                ),
                "outcome_back_to_null": (
                    "UPDATE step_attempt SET outcome=NULL, finished_at=NULL WHERE id=?"
                ),
                "rewrite_terminal_payload": (
                    "UPDATE step_attempt SET output_sha='rewritten' WHERE id=?"
                ),
                "change_terminal_outcome": (
                    "UPDATE step_attempt SET outcome='failed' WHERE id=?"
                ),
                "replace_terminal_with_active": (
                    "INSERT OR REPLACE INTO step_attempt SELECT id,public_id,run_id,stage_id,"
                    "role,campaign_id,round_id,lane_id,lane_assignment_id,subject_revision,"
                    "session_id,profile_id,requested_model,prompt_template_id,prompt_hash,"
                    "rubric_id,rubric_hash,input_sha,input_refs_json,manifest_json,started_at,"
                    "NULL,NULL,NULL,NULL,NULL,NULL,NULL FROM step_attempt WHERE id=?"
                ),
            }
            with pytest.raises(sqlite3.IntegrityError):
                await database.transaction(
                    lambda tx: tx.execute(statements[operation], (graph.attempt_id,))
                )
            row = await database.read(
                lambda db: db.fetch_one(
                    "SELECT outcome, finished_at, output_sha FROM step_attempt WHERE id=?",
                    (graph.attempt_id,),
                )
            )
            assert row is not None
            if operation in {
                "outcome_back_to_null",
                "rewrite_terminal_payload",
                "change_terminal_outcome",
                "replace_terminal_with_active",
            }:
                assert tuple(row) == ("succeeded", 100, "output")
            else:
                assert tuple(row) == (None, None, None)
        finally:
            await database.close()

    asyncio.run(scenario())


_ROUND_PROTECTED_MUTATIONS = (
    ("id", "id + 10000"),
    ("campaign_id", "campaign_id + 10000"),
    ("round_no", "round_no + 1"),
    ("kind", "'fix_check'"),
    ("preceding_revision_id", "10000"),
    ("opened_at", "opened_at + 1"),
)


@pytest.mark.parametrize(
    ("column", "expression"),
    _ROUND_PROTECTED_MUTATIONS,
    ids=[column for column, _ in _ROUND_PROTECTED_MUTATIONS],
)
def test_round_close_cannot_rewrite_each_identity_or_input_field(
    column: str,
    expression: str,
    database_factory: Callable[[], Awaitable],
    review_graph_builder: Callable[..., object],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        try:
            graph = await database.transaction(review_graph_builder)
            with pytest.raises(
                sqlite3.IntegrityError,
                match="review_round allows one open-to-closed update",
            ):
                await database.transaction(
                    lambda tx: tx.execute(
                        "UPDATE review_round SET result='clean', closed_at=100, "
                        f"{column}={expression} WHERE id=?",
                        (graph.round_id,),
                    )
                )
            row = await database.read(
                lambda db: db.fetch_one(
                    "SELECT result, closed_at FROM review_round WHERE id=?",
                    (graph.round_id,),
                )
            )
            assert row is not None and tuple(row) == (None, None)
        finally:
            await database.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "operation",
    (
        "terminal_insert",
        "result_without_closed_at",
        "closed_at_without_result",
        "delete_open",
        "replace_open",
        "change_result",
        "reopen",
        "replace_closed_with_open",
    ),
)
def test_round_lifecycle_rejects_every_bypass_on_the_real_schema(
    operation: str,
    database_factory: Callable[[], Awaitable],
    review_graph_builder: Callable[..., object],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        campaigns = CampaignRepository()
        try:
            graph = await database.transaction(review_graph_builder)
            if operation in {"change_result", "reopen", "replace_closed_with_open"}:
                await database.transaction(
                    lambda tx: campaigns.close_round(tx, graph.round_id, "clean", 100)
                )
            statements = {
                "terminal_insert": (
                    "INSERT INTO review_round(campaign_id,round_no,kind,"
                    "preceding_revision_id,result,opened_at,closed_at) "
                    "SELECT campaign_id,round_no+1,kind,preceding_revision_id,'clean',"
                    "opened_at+1,100 FROM review_round WHERE id=?"
                ),
                "result_without_closed_at": (
                    "UPDATE review_round SET result='clean' WHERE id=?"
                ),
                "closed_at_without_result": (
                    "UPDATE review_round SET closed_at=100 WHERE id=?"
                ),
                "delete_open": "DELETE FROM review_round WHERE id=?",
                "replace_open": (
                    "INSERT OR REPLACE INTO review_round SELECT * FROM review_round WHERE id=?"
                ),
                "change_result": (
                    "UPDATE review_round SET result='needs_revision' WHERE id=?"
                ),
                "reopen": (
                    "UPDATE review_round SET result=NULL, closed_at=NULL WHERE id=?"
                ),
                "replace_closed_with_open": (
                    "INSERT OR REPLACE INTO review_round "
                    "SELECT id,campaign_id,round_no,kind,preceding_revision_id,NULL,opened_at,NULL "
                    "FROM review_round WHERE id=?"
                ),
            }
            with pytest.raises(sqlite3.IntegrityError):
                await database.transaction(
                    lambda tx: tx.execute(statements[operation], (graph.round_id,))
                )
            row = await database.read(
                lambda db: db.fetch_one(
                    "SELECT result, closed_at FROM review_round WHERE id=?",
                    (graph.round_id,),
                )
            )
            assert row is not None
            expected = (
                ("clean", 100)
                if operation in {"change_result", "reopen", "replace_closed_with_open"}
                else (None, None)
            )
            assert tuple(row) == expected
        finally:
            await database.close()

    asyncio.run(scenario())


_QUESTION_CONTENT_COLUMNS = (
    "id",
    "public_id",
    "run_id",
    "branch_id",
    "stage_id",
    "campaign_id",
    "round_id",
    "finding_id",
    "reason",
    "question_text",
    "options_json",
    "snapshot_json",
    "asked_at",
)


@pytest.mark.parametrize("column", _QUESTION_CONTENT_COLUMNS)
def test_question_rejects_each_content_field_update(
    column: str,
    database_factory: Callable[[], Awaitable],
    review_graph_builder: Callable[..., object],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        questions = QuestionRepository()
        try:
            graph = await database.transaction(review_graph_builder)
            question = await database.transaction(
                lambda tx: questions.create_question(
                    tx,
                    NewHumanQuestion(
                        graph.run_id,
                        graph.branch_id,
                        graph.stage_id,
                        graph.campaign_id,
                        graph.round_id,
                        None,
                        "lane_failure",
                        "question",
                        '["continue"]',
                        "{}",
                        100,
                    ),
                )
            )
            with pytest.raises(sqlite3.IntegrityError, match="content is immutable"):
                await database.transaction(
                    lambda tx: tx.execute(
                        f"UPDATE human_question SET {column}={column} WHERE id=?",
                        (question.id,),
                    )
                )
            unchanged = await database.read(lambda db: questions.get_question(db, question.id))
            assert unchanged == question
        finally:
            await database.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ("delete", "replace"))
def test_question_rejects_delete_and_replace_but_allows_lifecycle_updates(
    operation: str,
    database_factory: Callable[[], Awaitable],
    review_graph_builder: Callable[..., object],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        questions = QuestionRepository()
        try:
            graph = await database.transaction(review_graph_builder)
            question = await database.transaction(
                lambda tx: questions.create_question(
                    tx,
                    NewHumanQuestion(
                        graph.run_id,
                        graph.branch_id,
                        graph.stage_id,
                        None,
                        None,
                        None,
                        "open_question",
                        "question",
                        None,
                        None,
                        100,
                    ),
                )
            )
            await database.transaction(
                lambda tx: tx.execute(
                    "UPDATE human_question SET answered_at=101, reask_count=1 WHERE id=?",
                    (question.id,),
                )
            )
            statements = {
                "delete": "DELETE FROM human_question WHERE id=?",
                "replace": (
                    "INSERT OR REPLACE INTO human_question "
                    "SELECT * FROM human_question WHERE id=?"
                ),
            }
            with pytest.raises(sqlite3.IntegrityError):
                await database.transaction(
                    lambda tx: tx.execute(statements[operation], (question.id,))
                )
            row = await database.read(
                lambda db: db.fetch_one(
                    "SELECT answered_at, reask_count FROM human_question WHERE id=?",
                    (question.id,),
                )
            )
            assert row is not None and tuple(row) == (101, 1)
        finally:
            await database.close()

    asyncio.run(scenario())


def _create_finding_with_issued_round(
    tx: Transaction,
    graph: object,
    suffix: str,
) -> tuple[int, int]:
    attempts = AttemptRepository()
    findings = FindingRepository()
    observation = findings.create_observation(
        tx,
        NewReviewObservation(
            graph.campaign_id,
            graph.round_id,
            graph.lane_id,
            graph.attempt_id,
            graph.subject_id,
            "rev-1",
            f"finding-{suffix}",
            "body",
            None,
            None,
            None,
            None,
            "medium",
            None,
            "medium",
            f"dedup-{suffix}",
            110,
        ),
    )
    event_id = append_run_event(
        tx,
        NewRunEvent(
            run_id=graph.run_id,
            kind=f"finding_{suffix}.v1",
            payload={},
            created_at=110,
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
            f"finding-{suffix}",
            event_id,
            110,
        ),
    )
    author = attempts.create_attempt(
        tx,
        NewStepAttempt(
            f"A-author-{suffix}",
            graph.run_id,
            graph.stage_id,
            "author",
            None,
            None,
            None,
            None,
            None,
            None,
            "author",
            "author-model",
            "author.v1",
            f"author-{suffix}",
            None,
            None,
            "rev-1",
            "[]",
            "{}",
            111,
        ),
    )
    author = attempts.complete_attempt(
        tx,
        author.id,
        AttemptCompletion("succeeded", None, "author-model", "rev-2", 112, None, None),
    )
    finding_round = findings.create_finding_round(
        tx,
        NewFindingRound(
            graph.campaign_id,
            graph.run_id,
            finding.id,
            1,
            graph.round_id,
            graph.lane_id,
            "issued",
            "fixed",
            None,
            author.id,
        ),
    )
    return finding.id, finding_round.id


_FINDING_ROUND_INPUT_COLUMNS = (
    "id",
    "campaign_id",
    "run_id",
    "finding_id",
    "round_no",
    "round_id",
    "owner_lane_id",
    "entry_kind",
    "disposition",
    "disposition_reason",
    "author_attempt_id",
)


@pytest.mark.parametrize("column", _FINDING_ROUND_INPUT_COLUMNS)
def test_finding_round_rejects_each_input_field_update(
    column: str,
    database_factory: Callable[[], Awaitable],
    review_graph_builder: Callable[..., object],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        try:
            graph = await database.transaction(review_graph_builder)
            _, finding_round_id = await database.transaction(
                lambda tx: _create_finding_with_issued_round(tx, graph, column)
            )
            with pytest.raises(sqlite3.IntegrityError, match="input is immutable"):
                await database.transaction(
                    lambda tx: tx.execute(
                        f"UPDATE finding_round SET {column}={column} WHERE id=?",
                        (finding_round_id,),
                    )
                )
        finally:
            await database.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "violation",
    ("post_check_with_answer", "disposition_without_author", "decision_without_pair"),
)
def test_design_invariant_one_rejects_each_independent_shape_violation(
    violation: str,
    database_factory: Callable[[], Awaitable],
    review_graph_builder: Callable[..., object],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        try:
            graph = await database.transaction(review_graph_builder)

            def violate(tx: Transaction) -> None:
                _, finding_round_id = _create_finding_with_issued_round(
                    tx, graph, violation
                )
                if violation == "post_check_with_answer":
                    round_two = CampaignRepository().create_round(
                        tx, NewReviewRound(graph.campaign_id, 2, "discovery", None, 120)
                    )
                    tx.execute(
                        "INSERT INTO finding_round(campaign_id,run_id,finding_id,round_no,"
                        "round_id,owner_lane_id,entry_kind,disposition,disposition_reason,"
                        "author_attempt_id,reviewer_decision,reviewer_attempt_id,decided_at) "
                        "SELECT campaign_id,run_id,finding_id,2,?,owner_lane_id,'post_check',"
                        "disposition,disposition_reason,author_attempt_id,NULL,NULL,NULL "
                        "FROM finding_round WHERE id=?",
                        (round_two.id, finding_round_id),
                    )
                elif violation == "disposition_without_author":
                    round_two = CampaignRepository().create_round(
                        tx, NewReviewRound(graph.campaign_id, 2, "discovery", None, 120)
                    )
                    tx.execute(
                        "INSERT INTO finding_round(campaign_id,run_id,finding_id,round_no,"
                        "round_id,owner_lane_id,entry_kind,disposition,disposition_reason,"
                        "author_attempt_id,reviewer_decision,reviewer_attempt_id,decided_at) "
                        "SELECT campaign_id,run_id,finding_id,2,?,owner_lane_id,'issued',"
                        "'fixed',NULL,NULL,NULL,NULL,NULL FROM finding_round WHERE id=?",
                        (round_two.id, finding_round_id),
                    )
                else:
                    tx.execute(
                        "UPDATE finding_round SET reviewer_decision='verified_fixed' WHERE id=?",
                        (finding_round_id,),
                    )

            with pytest.raises(sqlite3.IntegrityError):
                await database.transaction(violate)
            assert await database.read(
                lambda db: db.fetch_one(
                    "SELECT 1 FROM finding WHERE title=?",
                    (f"finding-{violation}",),
                )
            ) is None
        finally:
            await database.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "operation",
    (
        "terminal_insert",
        "repeat_decision",
        "clear_decision",
        "delete",
        "replace",
    ),
)
def test_finding_round_reviewer_decision_lifecycle_rejects_every_bypass(
    operation: str,
    database_factory: Callable[[], Awaitable],
    review_graph_builder: Callable[..., object],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        findings = FindingRepository()
        try:
            graph = await database.transaction(review_graph_builder)
            _, finding_round_id = await database.transaction(
                lambda tx: _create_finding_with_issued_round(tx, graph, operation)
            )
            if operation != "terminal_insert":
                await database.transaction(
                    lambda tx: findings.record_reviewer_decision(
                        tx,
                        finding_round_id,
                        ReviewerDecisionWrite("verified_fixed", graph.attempt_id, 120),
                    )
                )
            statements = {
                "repeat_decision": (
                    "UPDATE finding_round SET reviewer_decision='still_present' WHERE id=?"
                ),
                "clear_decision": (
                    "UPDATE finding_round SET reviewer_decision=NULL,reviewer_attempt_id=NULL,"
                    "decided_at=NULL WHERE id=?"
                ),
                "delete": "DELETE FROM finding_round WHERE id=?",
                "replace": (
                    "INSERT OR REPLACE INTO finding_round SELECT * FROM finding_round WHERE id=?"
                ),
            }
            with pytest.raises(sqlite3.IntegrityError):
                if operation == "terminal_insert":
                    def insert_terminal(tx: Transaction) -> None:
                        round_two = CampaignRepository().create_round(
                            tx, NewReviewRound(graph.campaign_id, 2, "discovery", None, 120)
                        )
                        reviewer = AttemptRepository().create_attempt(
                            tx,
                            NewStepAttempt(
                                "A-terminal-reviewer",
                                graph.run_id,
                                graph.stage_id,
                                "reviewer",
                                graph.campaign_id,
                                round_two.id,
                                graph.lane_id,
                                graph.assignment_id,
                                "rev-1",
                                None,
                                "reviewer-1",
                                "gpt-test",
                                "review.v1",
                                "terminal-reviewer",
                                "rubric",
                                "rubric-hash",
                                "input",
                                "[]",
                                "{}",
                                121,
                            ),
                        )
                        reviewer = AttemptRepository().complete_attempt(
                            tx,
                            reviewer.id,
                            AttemptCompletion(
                                "succeeded", None, "gpt-test", "out", 122, None, None
                            ),
                        )
                        tx.execute(
                            "INSERT INTO finding_round(campaign_id,run_id,finding_id,round_no,"
                            "round_id,owner_lane_id,entry_kind,disposition,disposition_reason,"
                            "author_attempt_id,reviewer_decision,reviewer_attempt_id,decided_at) "
                            "SELECT campaign_id,run_id,finding_id,2,?,owner_lane_id,entry_kind,"
                            "disposition,disposition_reason,author_attempt_id,'verified_fixed',?,122 "
                            "FROM finding_round WHERE id=?",
                            (round_two.id, reviewer.id, finding_round_id),
                        )

                    await database.transaction(insert_terminal)
                else:
                    await database.transaction(
                        lambda tx: tx.execute(statements[operation], (finding_round_id,))
                    )
        finally:
            await database.close()

    asyncio.run(scenario())


def _create_task_import(tx: Transaction, graph: object, suffix: str) -> int:
    artifact = tx.execute(
        "INSERT INTO artifact_revision(run_id,stage_id,kind,logical_path,revision_no,"
        "content_digest,code_sha,repo_commit,produced_by_attempt_id,produced_by,"
        "manifest_json,created_at) VALUES (?,?,'breakdown',?,1,?,'sha',NULL,NULL,"
        "'human','{}',130)",
        (graph.run_id, graph.stage_id, f"tasks-{suffix}.json", f"digest-{suffix}"),
    )
    assert artifact.lastrowid is not None
    imported = tx.execute(
        "INSERT INTO task_graph_import(run_id,source_artifact_revision,imported_at,event_id) "
        "VALUES (?,?,130,?)",
        (graph.run_id, artifact.lastrowid, graph.event_id),
    )
    assert imported.lastrowid is not None
    return imported.lastrowid


def _create_task(
    tx: Transaction,
    graph: object,
    import_id: int,
    semantic_id: str,
    state: str = "pending",
) -> int:
    result = tx.execute(
        "INSERT INTO task(run_id,semantic_task_id,import_id,title,body,state,carry_over_of,"
        "created_at,closed_at) VALUES (?,?,?,'title','body',?,NULL,131,NULL)",
        (graph.run_id, semantic_id, import_id, state),
    )
    assert result.lastrowid is not None
    return result.lastrowid


@pytest.mark.parametrize(
    "violation",
    ("same_import_semantic", "second_active_semantic", "self_edge", "duplicate_edge"),
)
def test_task_graph_constraints_reject_each_design_23_violation(
    violation: str,
    database_factory: Callable[[], Awaitable],
    review_graph_builder: Callable[..., object],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        try:
            graph = await database.transaction(review_graph_builder)

            def seed(tx: Transaction) -> tuple[int, int, int | None]:
                first_import = _create_task_import(tx, graph, f"{violation}-one")
                first_task = _create_task(tx, graph, first_import, "semantic-a")
                second_task = None
                if violation == "duplicate_edge":
                    second_task = _create_task(tx, graph, first_import, "semantic-b")
                    tx.execute(
                        "INSERT INTO task_dependency(parent_task_id,child_task_id) VALUES (?,?)",
                        (first_task, second_task),
                    )
                return first_import, first_task, second_task

            first_import, first_task, second_task = await database.transaction(seed)

            def violate(tx: Transaction) -> None:
                if violation == "same_import_semantic":
                    _create_task(tx, graph, first_import, "semantic-a")
                elif violation == "second_active_semantic":
                    other_import = _create_task_import(tx, graph, "active-two")
                    _create_task(tx, graph, other_import, "semantic-a")
                elif violation == "self_edge":
                    tx.execute(
                        "INSERT INTO task_dependency(parent_task_id,child_task_id) VALUES (?,?)",
                        (first_task, first_task),
                    )
                else:
                    assert second_task is not None
                    tx.execute(
                        "INSERT INTO task_dependency(parent_task_id,child_task_id) VALUES (?,?)",
                        (first_task, second_task),
                    )

            with pytest.raises(sqlite3.IntegrityError):
                await database.transaction(violate)
            assert await database.read(
                lambda db: db.fetch_one("PRAGMA foreign_key_check")
            ) is None
        finally:
            await database.close()

    asyncio.run(scenario())


def test_task_graph_allows_two_invalidated_versions_and_one_active_version(
    database_factory: Callable[[], Awaitable],
    review_graph_builder: Callable[..., object],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        try:
            graph = await database.transaction(review_graph_builder)

            def seed(tx: Transaction) -> tuple[str, ...]:
                states = ("invalidated", "pending", "invalidated")
                for index, state in enumerate(states, start=1):
                    imported = _create_task_import(tx, graph, f"history-{index}")
                    _create_task(tx, graph, imported, "semantic-history", state)
                return tuple(
                    row["state"]
                    for row in tx.fetch_all(
                        "SELECT state FROM task WHERE semantic_task_id='semantic-history' "
                        "ORDER BY id"
                    )
                )

            assert await database.transaction(seed) == (
                "invalidated",
                "pending",
                "invalidated",
            )
        finally:
            await database.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "violation",
    ("active_with_closed_at", "terminal_without_closed_at"),
)
def test_campaign_closed_at_shape_is_enforced_in_both_directions(
    violation: str,
    database_factory: Callable[[], Awaitable],
    review_graph_builder: Callable[..., object],
) -> None:
    async def scenario() -> None:
        database = await database_factory()
        campaigns = CampaignRepository()
        try:
            graph = await database.transaction(review_graph_builder)
            if violation == "terminal_without_closed_at":
                await database.transaction(
                        lambda tx: campaigns.transition_campaign_state(
                            tx, graph.campaign_id, "discovery", "reconciliation", None
                        )
                )
                statement = (
                    "UPDATE review_campaign SET state='closed_clean',closed_at=NULL WHERE id=?"
                )
            else:
                statement = "UPDATE review_campaign SET closed_at=100 WHERE id=?"
            with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"):
                await database.transaction(
                    lambda tx: tx.execute(statement, (graph.campaign_id,))
                )
            campaign = await database.read(
                lambda db: campaigns.get_campaign(db, graph.campaign_id)
            )
            assert campaign is not None
            assert campaign.closed_at is None
            assert campaign.state == (
                "reconciliation" if violation == "terminal_without_closed_at" else "discovery"
            )
        finally:
            await database.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "violation",
    (
        "campaign_foreign_stage",
        "campaign_foreign_subject",
        "observation_foreign_round",
        "observation_foreign_lane",
        "finding_round_foreign_run",
        "resolution_foreign_run",
        "author_revision_foreign_stage",
    ),
)
def test_c06_scope_matrix_changes_one_coordinate_per_case(
    violation: str,
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
                lambda tx: review_graph_builder(
                    tx,
                    suffix="scope-a",
                    expected_lane_count=2 if violation == "observation_foreign_lane" else 1,
                )
            )
            other = await database.transaction(
                lambda tx: review_graph_builder(tx, suffix="scope-b")
            )

            def violate(tx: Transaction) -> None:
                if violation in {"campaign_foreign_stage", "campaign_foreign_subject"}:
                    campaigns.create_campaign(
                        tx,
                        NewReviewCampaign(
                            f"C-{violation}",
                            graph.run_id,
                            other.stage_id
                            if violation == "campaign_foreign_stage"
                            else graph.stage_id,
                            other.subject_id
                            if violation == "campaign_foreign_subject"
                            else graph.subject_id,
                            2,
                            "high",
                            "v1",
                            1,
                            140,
                        ),
                    )
                    return

                if violation == "observation_foreign_round":
                    round_two = campaigns.create_round(
                        tx, NewReviewRound(graph.campaign_id, 2, "discovery", None, 140)
                    )
                    foreign_attempt = attempts.create_attempt(
                        tx,
                        NewStepAttempt(
                            "A-scope-round",
                            graph.run_id,
                            graph.stage_id,
                            "reviewer",
                            graph.campaign_id,
                            round_two.id,
                            graph.lane_id,
                            graph.assignment_id,
                            "rev-1",
                            None,
                            "reviewer-scope-a",
                            "gpt-test",
                            "review.v1",
                            "scope-round",
                            "rubric",
                            "rubric-hash",
                            "input",
                            "[]",
                            "{}",
                            141,
                        ),
                    )
                    foreign_attempt = attempts.complete_attempt(
                        tx,
                        foreign_attempt.id,
                        AttemptCompletion("succeeded", None, "gpt-test", "out", 142, None, None),
                    )
                    findings.create_observation(
                        tx,
                        NewReviewObservation(
                            graph.campaign_id,
                            graph.round_id,
                            graph.lane_id,
                            foreign_attempt.id,
                            graph.subject_id,
                            "rev-1",
                            "scope-round",
                            "body",
                            None,
                            None,
                            None,
                            None,
                            "medium",
                            None,
                            "medium",
                            "scope-round",
                            143,
                        ),
                    )
                    return

                if violation == "observation_foreign_lane":
                    tx.execute(
                        "INSERT INTO run_profile_resolution(run_id,profile_id,provider,model,"
                        "resolved_at) VALUES (?,'scope-lane-2','openai','gpt-test',140)",
                        (graph.run_id,),
                    )
                    lane_two = campaigns.create_lane(
                        tx, NewReviewLane(graph.campaign_id, graph.run_id, 1)
                    )
                    assignment_two = campaigns.create_lane_assignment(
                        tx,
                        NewLaneAssignment(
                            lane_two.id,
                            graph.run_id,
                            1,
                            "scope-lane-2",
                            None,
                            None,
                            None,
                            graph.event_id,
                            141,
                        ),
                    )
                    foreign_attempt = attempts.create_attempt(
                        tx,
                        NewStepAttempt(
                            "A-scope-lane",
                            graph.run_id,
                            graph.stage_id,
                            "reviewer",
                            graph.campaign_id,
                            graph.round_id,
                            lane_two.id,
                            assignment_two.id,
                            "rev-1",
                            None,
                            "scope-lane-2",
                            "gpt-test",
                            "review.v1",
                            "scope-lane",
                            "rubric",
                            "rubric-hash",
                            "input",
                            "[]",
                            "{}",
                            142,
                        ),
                    )
                    foreign_attempt = attempts.complete_attempt(
                        tx,
                        foreign_attempt.id,
                        AttemptCompletion("succeeded", None, "gpt-test", "out", 143, None, None),
                    )
                    findings.create_observation(
                        tx,
                        NewReviewObservation(
                            graph.campaign_id,
                            graph.round_id,
                            graph.lane_id,
                            foreign_attempt.id,
                            graph.subject_id,
                            "rev-1",
                            "scope-lane",
                            "body",
                            None,
                            None,
                            None,
                            None,
                            "medium",
                            None,
                            "medium",
                            "scope-lane",
                            144,
                        ),
                    )
                    return

                other_finding_id, _ = _create_finding_with_issued_round(
                    tx, other, violation
                )
                if violation == "finding_round_foreign_run":
                    tx.execute(
                        "INSERT INTO finding_round(campaign_id,run_id,finding_id,round_no,"
                        "round_id,owner_lane_id,entry_kind) VALUES (?,?,?,?,?,?,'post_check')",
                        (
                            graph.campaign_id,
                            graph.run_id,
                            other_finding_id,
                            1,
                            graph.round_id,
                            graph.lane_id,
                        ),
                    )
                elif violation == "resolution_foreign_run":
                    event_id = append_run_event(
                        tx,
                        NewRunEvent(
                            run_id=graph.run_id,
                            kind="scope_resolution.v1",
                            payload={},
                            created_at=145,
                        ),
                    )
                    findings.create_resolution(
                        tx,
                        NewFindingResolution(
                            graph.run_id,
                            other_finding_id,
                            "policy_closed",
                            "policy",
                            graph.campaign_id,
                            None,
                            None,
                            0,
                            event_id,
                            145,
                        ),
                    )
                else:
                    foreign_author = attempts.create_attempt(
                        tx,
                        NewStepAttempt(
                            "A-scope-author",
                            other.run_id,
                            other.stage_id,
                            "author",
                            None,
                            None,
                            None,
                            None,
                            None,
                            None,
                            "author",
                            "author-model",
                            "author.v1",
                            "scope-author",
                            None,
                            None,
                            "input",
                            "[]",
                            "{}",
                            146,
                        ),
                    )
                    foreign_author = attempts.complete_attempt(
                        tx,
                        foreign_author.id,
                        AttemptCompletion("succeeded", None, "author-model", "out", 147, None, None),
                    )
                    campaigns.create_author_revision(
                        tx,
                        NewAuthorRevision(
                            graph.campaign_id,
                            graph.stage_id,
                            1,
                            foreign_author.id,
                            "author",
                            "succeeded",
                            "input",
                            "out",
                            None,
                            147,
                        ),
                    )

            with pytest.raises(sqlite3.IntegrityError):
                await database.transaction(violate)
            assert await database.read(
                lambda db: db.fetch_one("PRAGMA foreign_key_check")
            ) is None
        finally:
            await database.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "violation",
    (
        "duplicate_finding_public_id",
        "duplicate_finding_first_observation",
        "incompatible_reviewer_decision",
        "second_active_attempt",
        "duplicate_human_answer",
        "duplicate_transport_update",
        "second_observation_link",
        "author_revision_wrong_role",
        "author_revision_wrong_outcome",
        "observation_has_both_severity_forms",
        "observation_changes_inherited_severity",
        "duplicate_finding_round",
        "duplicate_reviewer_exposure",
        "resolution_wrong_authority",
        "resolution_wrong_closes_period",
        "resolution_missing_human_answer",
    ),
)
def test_remaining_constraint_invariants_each_have_an_isolated_negative_case(
    violation: str,
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
            await database.transaction(lambda tx: _seed_append_only_rows(tx, graph))

            def violate(tx: Transaction) -> None:
                finding = tx.fetch_one("SELECT * FROM finding ORDER BY id LIMIT 1")
                observation = tx.fetch_one(
                    "SELECT * FROM review_observation ORDER BY id LIMIT 1"
                )
                assert finding is not None and observation is not None

                if violation in {
                    "duplicate_finding_public_id",
                    "duplicate_finding_first_observation",
                }:
                    new_observation = findings.create_observation(
                        tx,
                        NewReviewObservation(
                            graph.campaign_id,
                            graph.round_id,
                            graph.lane_id,
                            graph.attempt_id,
                            graph.subject_id,
                            "rev-1",
                            f"{violation}-observation",
                            "body",
                            None,
                            None,
                            None,
                            None,
                            "medium",
                            None,
                            "medium",
                            violation,
                            200,
                        ),
                    )
                    event_id = append_run_event(
                        tx,
                        NewRunEvent(
                            run_id=graph.run_id,
                            kind=f"{violation}.v1",
                            payload={},
                            created_at=200,
                        ),
                    )
                    first_observation_id = (
                        finding["first_observation_id"]
                        if violation == "duplicate_finding_first_observation"
                        else new_observation.id
                    )
                    public_id = (
                        finding["public_id"]
                        if violation == "duplicate_finding_public_id"
                        else "F-isolated-duplicate"
                    )
                    tx.execute(
                        "INSERT INTO finding(public_id,run_id,subject_id,first_campaign_id,"
                        "first_round_id,first_observation_id,first_revision,first_owner_lane_id,"
                        "title,title_authority,title_changed_reason,event_id,created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,'runtime',NULL,?,200)",
                        (
                            public_id,
                            graph.run_id,
                            graph.subject_id,
                            graph.campaign_id,
                            graph.round_id,
                            first_observation_id,
                            "rev-1",
                            graph.lane_id,
                            violation,
                            event_id,
                        ),
                    )
                elif violation == "incompatible_reviewer_decision":
                    _, finding_round_id = _create_finding_with_issued_round(
                        tx, graph, violation
                    )
                    tx.execute(
                        "UPDATE finding_round SET reviewer_decision='insists',"
                        "reviewer_attempt_id=?,decided_at=200 WHERE id=?",
                        (graph.attempt_id, finding_round_id),
                    )
                elif violation == "second_active_attempt":
                    def active(public_id: str) -> None:
                        attempts.create_attempt(
                            tx,
                            NewStepAttempt(
                                public_id,
                                graph.run_id,
                                graph.stage_id,
                                "reviewer",
                                graph.campaign_id,
                                graph.round_id,
                                graph.lane_id,
                                graph.assignment_id,
                                "rev-1",
                                None,
                                "reviewer-1",
                                "gpt-test",
                                "review.v1",
                                public_id,
                                "rubric",
                                "rubric-hash",
                                "input",
                                "[]",
                                "{}",
                                200,
                            ),
                        )

                    active("A-active-one")
                    active("A-active-two")
                elif violation == "duplicate_human_answer":
                    question_id = tx.fetch_one(
                        "SELECT question_id FROM human_answer ORDER BY id LIMIT 1"
                    )["question_id"]
                    tx.execute(
                        "INSERT INTO human_answer(question_id,raw_text,chosen_option,"
                        "interpreted_json,transport,update_id,received_at) "
                        "VALUES (?,'again',NULL,'{}','cli',NULL,200)",
                        (question_id,),
                    )
                elif violation == "duplicate_transport_update":
                    tx.execute(
                        "INSERT INTO telegram_inbox(transport,update_id,payload,received_at) "
                        "VALUES ('telegram',1,'{}',200)"
                    )
                    tx.execute(
                        "INSERT INTO telegram_inbox(transport,update_id,payload,received_at) "
                        "VALUES ('telegram',1,'{}',201)"
                    )
                elif violation == "second_observation_link":
                    link = tx.fetch_one(
                        "SELECT * FROM finding_observation_link ORDER BY observation_id LIMIT 1"
                    )
                    assert link is not None
                    tx.execute(
                        "INSERT INTO finding_observation_link SELECT * FROM "
                        "finding_observation_link WHERE observation_id=?",
                        (link["observation_id"],),
                    )
                elif violation.startswith("author_revision_wrong_"):
                    if violation.endswith("role"):
                        attempt_id = graph.attempt_id
                        attempt_role = "reviewer"
                        attempt_outcome = "succeeded"
                    else:
                        author = attempts.create_attempt(
                            tx,
                            NewStepAttempt(
                                "A-failed-author",
                                graph.run_id,
                                graph.stage_id,
                                "author",
                                None,
                                None,
                                None,
                                None,
                                None,
                                None,
                                "author",
                                "author-model",
                                "author.v1",
                                "failed-author",
                                None,
                                None,
                                "input",
                                "[]",
                                "{}",
                                200,
                            ),
                        )
                        author = attempts.complete_attempt(
                            tx,
                            author.id,
                            AttemptCompletion("failed", None, None, None, 201, None, None),
                        )
                        attempt_id = author.id
                        attempt_role = "author"
                        attempt_outcome = "failed"
                    campaigns.create_author_revision(
                        tx,
                        NewAuthorRevision(
                            graph.campaign_id,
                            graph.stage_id,
                            1,
                            attempt_id,
                            attempt_role,
                            attempt_outcome,
                            "input",
                            "output",
                            None,
                            201,
                        ),
                    )
                elif violation.startswith("observation_"):
                    findings.create_observation(
                        tx,
                        NewReviewObservation(
                            graph.campaign_id,
                            graph.round_id,
                            graph.lane_id,
                            graph.attempt_id,
                            graph.subject_id,
                            "rev-1",
                            violation,
                            "body",
                            None,
                            None,
                            None,
                            None,
                            "high"
                            if violation == "observation_has_both_severity_forms"
                            else None,
                            observation["id"],
                            "high"
                            if violation == "observation_has_both_severity_forms"
                            else "medium",
                            violation,
                            200,
                        ),
                    )
                elif violation == "duplicate_finding_round":
                    finding_id, _ = _create_finding_with_issued_round(tx, graph, violation)
                    findings.create_finding_round(
                        tx,
                        NewFindingRound(
                            graph.campaign_id,
                            graph.run_id,
                            finding_id,
                            1,
                            graph.round_id,
                            graph.lane_id,
                            "post_check",
                            None,
                            None,
                            None,
                        ),
                    )
                elif violation == "duplicate_reviewer_exposure":
                    tx.execute(
                        "INSERT INTO reviewer_exposure(run_id,subject_id,revision,provider,model,"
                        "campaign_id,first_attempt_id,profile_id,created_at) "
                        "SELECT run_id,subject_id,revision,provider,model,campaign_id,"
                        "first_attempt_id,profile_id,200 FROM reviewer_exposure LIMIT 1"
                    )
                else:
                    event_id = append_run_event(
                        tx,
                        NewRunEvent(
                            run_id=graph.run_id,
                            kind=f"{violation}.v1",
                            payload={},
                            created_at=200,
                        ),
                    )
                    if violation == "resolution_wrong_authority":
                        resolution, authority, closes, answer = (
                            "verified_fixed",
                            "policy",
                            1,
                            None,
                        )
                    elif violation == "resolution_wrong_closes_period":
                        resolution, authority, closes, answer = (
                            "verified_fixed",
                            "reviewer",
                            0,
                            None,
                        )
                    else:
                        resolution, authority, closes, answer = (
                            "human_decision",
                            "human",
                            1,
                            None,
                        )
                    findings.create_resolution(
                        tx,
                        NewFindingResolution(
                            graph.run_id,
                            finding["id"],
                            resolution,
                            authority,
                            graph.campaign_id,
                            None,
                            answer,
                            closes,
                            event_id,
                            200,
                        ),
                    )

            with pytest.raises(sqlite3.IntegrityError):
                await database.transaction(violate)
            await database.transaction(
                lambda tx: append_run_event(
                    tx,
                    NewRunEvent(
                        run_id=graph.run_id,
                        kind=f"after_{violation}.v1",
                        payload={},
                        created_at=300,
                    ),
                )
            )
            assert await database.read(
                lambda db: db.fetch_one("PRAGMA foreign_key_check")
            ) is None
        finally:
            await database.close()

    asyncio.run(scenario())
