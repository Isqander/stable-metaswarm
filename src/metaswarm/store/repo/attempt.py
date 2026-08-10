from __future__ import annotations

from dataclasses import dataclass

from ..db import Transaction
from ._mapping import (
    ReadContext,
    RepositoryAlreadyTerminal,
    RepositoryRecordNotFound,
    ReviewerExposureConflict,
    insert_record,
    map_optional,
    map_row,
    repository_precondition,
)


@dataclass(frozen=True, slots=True)
class StepAttemptRecord:
    id: int
    public_id: str
    run_id: int
    stage_id: int
    role: str
    campaign_id: int | None
    round_id: int | None
    lane_id: int | None
    lane_assignment_id: int | None
    subject_revision: str | None
    session_id: int | None
    profile_id: str
    requested_model: str
    prompt_template_id: str
    prompt_hash: str
    rubric_id: str | None
    rubric_hash: str | None
    input_sha: str | None
    input_refs_json: str
    manifest_json: str
    started_at: int
    outcome: str | None
    outcome_detail: str | None
    actual_model: str | None
    output_sha: str | None
    finished_at: int | None
    transcript_path: str | None
    transcript_digest: str | None


@dataclass(frozen=True, slots=True)
class NewStepAttempt:
    public_id: str
    run_id: int
    stage_id: int
    role: str
    campaign_id: int | None
    round_id: int | None
    lane_id: int | None
    lane_assignment_id: int | None
    subject_revision: str | None
    session_id: int | None
    profile_id: str
    requested_model: str
    prompt_template_id: str
    prompt_hash: str
    rubric_id: str | None
    rubric_hash: str | None
    input_sha: str | None
    input_refs_json: str
    manifest_json: str
    started_at: int


@dataclass(frozen=True, slots=True)
class AttemptCompletion:
    outcome: str
    outcome_detail: str | None
    actual_model: str | None
    output_sha: str | None
    finished_at: int
    transcript_path: str | None
    transcript_digest: str | None


@dataclass(frozen=True, slots=True)
class ReviewerExposureRecord:
    id: int
    run_id: int
    subject_id: int
    revision: str
    provider: str
    model: str
    campaign_id: int
    first_attempt_id: int
    profile_id: str
    created_at: int


_RETRYABLE_OUTCOMES = frozenset({"contract_error", "transient", "hung", "interrupted"})


class AttemptRepository:
    def create_attempt(
        self, tx: Transaction, value: NewStepAttempt
    ) -> StepAttemptRecord:
        return insert_record(tx, "step_attempt", value, StepAttemptRecord)

    def get_attempt(
        self, db: ReadContext, attempt_id: int
    ) -> StepAttemptRecord | None:
        return map_optional(
            StepAttemptRecord,
            db.fetch_one("SELECT * FROM step_attempt WHERE id = ?", (attempt_id,)),
        )

    def complete_attempt(
        self,
        tx: Transaction,
        attempt_id: int,
        completion: AttemptCompletion,
    ) -> StepAttemptRecord:
        result = tx.execute(
            "UPDATE step_attempt SET outcome = ?, outcome_detail = ?, "
            "actual_model = ?, output_sha = ?, finished_at = ?, "
            "transcript_path = ?, transcript_digest = ? "
            "WHERE id = ? AND outcome IS NULL",
            (
                completion.outcome,
                completion.outcome_detail,
                completion.actual_model,
                completion.output_sha,
                completion.finished_at,
                completion.transcript_path,
                completion.transcript_digest,
                attempt_id,
            ),
        )
        if result.rowcount == 0:
            row = tx.fetch_one(
                "SELECT outcome FROM step_attempt WHERE id = ?", (attempt_id,)
            )
            if row is None:
                raise RepositoryRecordNotFound("step_attempt", attempt_id)
            raise RepositoryAlreadyTerminal("step_attempt", attempt_id, row["outcome"])
        row = tx.fetch_one("SELECT * FROM step_attempt WHERE id = ?", (attempt_id,))
        assert row is not None
        return map_row(StepAttemptRecord, row)

    def reserve_reviewer_exposure(
        self, tx: Transaction, attempt_id: int
    ) -> ReviewerExposureRecord:
        attempt = tx.fetch_one(
            "SELECT a.*, c.subject_id, rp.provider, rp.model "
            "FROM step_attempt a "
            "LEFT JOIN review_campaign c ON c.id = a.campaign_id "
            "LEFT JOIN run_profile_resolution rp "
            "ON rp.run_id = a.run_id AND rp.profile_id = a.profile_id "
            "WHERE a.id = ?",
            (attempt_id,),
        )
        if attempt is None:
            raise RepositoryRecordNotFound("step_attempt", attempt_id)
        if attempt["role"] not in ("reviewer", "reconciler"):
            raise repository_precondition("only reviewer and reconciler attempts reserve exposure")
        required = ("campaign_id", "subject_id", "subject_revision", "provider", "model")
        if any(attempt[name] is None for name in required):
            raise repository_precondition("review exposure coordinates are incomplete")

        existing = tx.fetch_one(
            "SELECT * FROM reviewer_exposure WHERE subject_id = ? AND revision = ? "
            "AND provider = ? AND model = ? AND campaign_id = ?",
            (
                attempt["subject_id"],
                attempt["subject_revision"],
                attempt["provider"],
                attempt["model"],
                attempt["campaign_id"],
            ),
        )
        if existing is None:
            result = tx.execute(
                "INSERT INTO reviewer_exposure("
                "run_id, subject_id, revision, provider, model, campaign_id, "
                "first_attempt_id, profile_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    attempt["run_id"],
                    attempt["subject_id"],
                    attempt["subject_revision"],
                    attempt["provider"],
                    attempt["model"],
                    attempt["campaign_id"],
                    attempt_id,
                    attempt["profile_id"],
                    attempt["started_at"],
                ),
            )
            assert result.lastrowid is not None
            existing = tx.fetch_one(
                "SELECT * FROM reviewer_exposure WHERE id = ?", (result.lastrowid,)
            )
            assert existing is not None
            return map_row(ReviewerExposureRecord, existing)

        if not self._can_reuse_exposure(tx, attempt, existing):
            raise ReviewerExposureConflict(attempt_id, existing["first_attempt_id"])
        return map_row(ReviewerExposureRecord, existing)

    def _can_reuse_exposure(self, tx: Transaction, attempt: object, exposure: object) -> bool:
        if attempt["role"] == "reconciler":
            return True
        if attempt["lane_id"] is None or attempt["lane_assignment_id"] is None:
            return False
        first = tx.fetch_one(
            "SELECT lane_id FROM step_attempt WHERE id = ?",
            (exposure["first_attempt_id"],),
        )
        if first is None or first["lane_id"] != attempt["lane_id"]:
            return False
        current = tx.fetch_one(
            "SELECT 1 FROM lane_assignment a WHERE a.id = ? AND a.lane_id = ? "
            "AND NOT EXISTS (SELECT 1 FROM lane_assignment s WHERE s.replaces_id = a.id)",
            (attempt["lane_assignment_id"], attempt["lane_id"]),
        )
        if current is None:
            return False
        previous = tx.fetch_one(
            "SELECT outcome FROM step_attempt WHERE campaign_id = ? AND round_id = ? "
            "AND lane_id = ? AND lane_assignment_id = ? AND role = 'reviewer' "
            "AND id < ? ORDER BY id DESC LIMIT 1",
            (
                attempt["campaign_id"],
                attempt["round_id"],
                attempt["lane_id"],
                attempt["lane_assignment_id"],
                attempt["id"],
            ),
        )
        return previous is None or previous["outcome"] in _RETRYABLE_OUTCOMES
