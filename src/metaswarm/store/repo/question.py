from __future__ import annotations

from dataclasses import dataclass

from ..db import StoreError, Transaction
from ._mapping import (
    ReadContext,
    insert_record,
    map_optional,
    map_row,
    map_rows,
    next_autoincrement_id,
    record_from_value,
)


@dataclass(frozen=True, slots=True)
class HumanQuestionRecord:
    id: int
    public_id: str
    run_id: int
    branch_id: int | None
    stage_id: int | None
    campaign_id: int | None
    round_id: int | None
    finding_id: int | None
    reason: str
    question_text: str
    options_json: str | None
    snapshot_json: str | None
    asked_at: int
    answered_at: int | None
    reask_count: int


@dataclass(frozen=True, slots=True)
class NewHumanQuestion:
    run_id: int
    branch_id: int | None
    stage_id: int | None
    campaign_id: int | None
    round_id: int | None
    finding_id: int | None
    reason: str
    question_text: str
    options_json: str | None
    snapshot_json: str | None
    asked_at: int


@dataclass(frozen=True, slots=True)
class HumanQuestionObservationRecord:
    question_id: int
    observation_id: int
    campaign_id: int
    round_id: int
    run_id: int
    reason: str
    finding_id: int | None


@dataclass(frozen=True, slots=True)
class NewHumanQuestionObservation:
    question_id: int
    observation_id: int
    campaign_id: int
    round_id: int
    run_id: int
    reason: str
    finding_id: int | None


@dataclass(frozen=True, slots=True)
class NotificationOutboxRecord:
    id: int
    run_id: int
    question_id: int | None
    transport: str
    target_ref: str | None
    body: str
    reply_markup: str | None
    created_at: int
    sent_at: int | None
    transport_message_id: str | None
    attempts: int
    last_error: str | None


@dataclass(frozen=True, slots=True)
class NewNotificationOutbox:
    run_id: int
    question_id: int | None
    transport: str
    target_ref: str | None
    body: str
    reply_markup: str | None
    created_at: int
    sent_at: int | None
    transport_message_id: str | None
    last_error: str | None


@dataclass(frozen=True, slots=True)
class BlockerRecord:
    id: int
    run_id: int
    kind: str
    branch_id: int | None
    task_id: int | None
    stage_id: int | None
    question_id: int | None
    detail: str | None
    created_at: int
    created_event_id: int
    cleared_at: int | None
    cleared_event_id: int | None


@dataclass(frozen=True, slots=True)
class NewBlocker:
    run_id: int
    kind: str
    branch_id: int | None
    task_id: int | None
    stage_id: int | None
    question_id: int | None
    detail: str | None
    created_at: int
    created_event_id: int
    cleared_at: int | None
    cleared_event_id: int | None


class QuestionRepository:
    def create_question(
        self, tx: Transaction, value: NewHumanQuestion
    ) -> HumanQuestionRecord:
        next_id = next_autoincrement_id(tx, "human_question")
        result = tx.execute(
            "INSERT INTO human_question("
            "public_id, run_id, branch_id, stage_id, campaign_id, round_id, finding_id, "
            "reason, question_text, options_json, snapshot_json, asked_at, answered_at, "
            "reask_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0)",
            (
                f"Q-{next_id}",
                value.run_id,
                value.branch_id,
                value.stage_id,
                value.campaign_id,
                value.round_id,
                value.finding_id,
                value.reason,
                value.question_text,
                value.options_json,
                value.snapshot_json,
                value.asked_at,
            ),
        )
        if result.lastrowid != next_id:
            raise StoreError("human_question allocator did not return the reserved ID")
        row = tx.fetch_one("SELECT * FROM human_question WHERE id = ?", (next_id,))
        assert row is not None
        return map_row(HumanQuestionRecord, row)

    def create_question_observation(
        self, tx: Transaction, value: NewHumanQuestionObservation
    ) -> HumanQuestionObservationRecord:
        tx.execute(
            "INSERT INTO human_question_observation("
            "question_id, observation_id, campaign_id, round_id, run_id, reason, finding_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                value.question_id,
                value.observation_id,
                value.campaign_id,
                value.round_id,
                value.run_id,
                value.reason,
                value.finding_id,
            ),
        )
        return record_from_value(HumanQuestionObservationRecord, value)

    def create_outbox_message(
        self, tx: Transaction, value: NewNotificationOutbox
    ) -> NotificationOutboxRecord:
        result = tx.execute(
            "INSERT INTO notification_outbox("
            "run_id, question_id, transport, target_ref, body, reply_markup, created_at, "
            "sent_at, transport_message_id, attempts, last_error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (
                value.run_id,
                value.question_id,
                value.transport,
                value.target_ref,
                value.body,
                value.reply_markup,
                value.created_at,
                value.sent_at,
                value.transport_message_id,
                value.last_error,
            ),
        )
        assert result.lastrowid is not None
        row = tx.fetch_one(
            "SELECT * FROM notification_outbox WHERE id = ?", (result.lastrowid,)
        )
        assert row is not None
        return map_row(NotificationOutboxRecord, row)

    def create_blocker(self, tx: Transaction, value: NewBlocker) -> BlockerRecord:
        return insert_record(tx, "blocker", value, BlockerRecord)

    def get_question(
        self, db: ReadContext, question_id: int
    ) -> HumanQuestionRecord | None:
        return map_optional(
            HumanQuestionRecord,
            db.fetch_one("SELECT * FROM human_question WHERE id = ?", (question_id,)),
        )

    def read_question_observations(
        self, db: ReadContext, question_id: int
    ) -> tuple[HumanQuestionObservationRecord, ...]:
        return map_rows(
            HumanQuestionObservationRecord,
            db.fetch_all(
                "SELECT * FROM human_question_observation WHERE question_id = ? "
                "ORDER BY observation_id",
                (question_id,),
            ),
        )
