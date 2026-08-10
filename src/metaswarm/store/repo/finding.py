from __future__ import annotations

from dataclasses import dataclass

from ..db import StoreError, Transaction
from ._mapping import (
    ReadContext,
    RepositoryRecordNotFound,
    insert_record,
    map_optional,
    map_row,
    map_rows,
    next_autoincrement_id,
    repository_precondition,
)


@dataclass(frozen=True, slots=True)
class ReviewObservationRecord:
    id: int
    public_id: str
    campaign_id: int
    round_id: int
    lane_id: int
    attempt_id: int
    subject_id: int
    revision: str
    seq: int
    title: str
    body: str
    file_path: str | None
    line_start: int | None
    line_end: int | None
    evidence: str | None
    severity_suggested: str | None
    unchanged_from_id: int | None
    severity_effective: str
    dedup_key: str
    created_at: int


@dataclass(frozen=True, slots=True)
class NewReviewObservation:
    campaign_id: int
    round_id: int
    lane_id: int
    attempt_id: int
    subject_id: int
    revision: str
    title: str
    body: str
    file_path: str | None
    line_start: int | None
    line_end: int | None
    evidence: str | None
    severity_suggested: str | None
    unchanged_from_id: int | None
    severity_effective: str
    dedup_key: str
    created_at: int


@dataclass(frozen=True, slots=True)
class FindingRecord:
    id: int
    public_id: str
    run_id: int
    subject_id: int
    first_campaign_id: int
    first_round_id: int
    first_observation_id: int
    first_revision: str
    first_owner_lane_id: int
    title: str
    title_authority: str
    title_changed_reason: str | None
    event_id: int
    created_at: int


@dataclass(frozen=True, slots=True)
class NewFinding:
    run_id: int
    subject_id: int
    first_campaign_id: int
    first_round_id: int
    first_observation_id: int
    first_revision: str
    first_owner_lane_id: int
    title: str
    event_id: int
    created_at: int


@dataclass(frozen=True, slots=True)
class ObservationLinkRecord:
    observation_id: int
    campaign_id: int
    round_id: int
    finding_id: int
    link_type: str
    decided_by_attempt_id: int | None
    decided_by_role: str | None
    decided_by_outcome: str | None
    decided_by_human_answer_id: int | None
    reason: str | None
    event_id: int
    created_at: int


@dataclass(frozen=True, slots=True)
class NewObservationLink:
    observation_id: int
    campaign_id: int
    round_id: int
    finding_id: int
    link_type: str
    decided_by_attempt_id: int | None
    decided_by_role: str | None
    decided_by_outcome: str | None
    decided_by_human_answer_id: int | None
    reason: str | None
    event_id: int
    created_at: int


@dataclass(frozen=True, slots=True)
class FindingRoundRecord:
    id: int
    campaign_id: int
    run_id: int
    finding_id: int
    round_no: int
    round_id: int
    owner_lane_id: int
    entry_kind: str
    disposition: str | None
    disposition_reason: str | None
    author_attempt_id: int | None
    reviewer_decision: str | None
    reviewer_attempt_id: int | None
    decided_at: int | None


@dataclass(frozen=True, slots=True)
class NewFindingRound:
    campaign_id: int
    run_id: int
    finding_id: int
    round_no: int
    round_id: int
    owner_lane_id: int
    entry_kind: str
    disposition: str | None
    disposition_reason: str | None
    author_attempt_id: int | None


@dataclass(frozen=True, slots=True)
class ReviewerDecisionWrite:
    reviewer_decision: str
    reviewer_attempt_id: int
    decided_at: int


@dataclass(frozen=True, slots=True)
class FindingResolutionRecord:
    id: int
    run_id: int
    finding_id: int
    resolution: str
    resolution_authority: str
    campaign_id: int
    round_no: int | None
    human_answer_id: int | None
    closes_severity_period: int
    event_id: int
    created_at: int


@dataclass(frozen=True, slots=True)
class NewFindingResolution:
    run_id: int
    finding_id: int
    resolution: str
    resolution_authority: str
    campaign_id: int
    round_no: int | None
    human_answer_id: int | None
    closes_severity_period: int
    event_id: int
    created_at: int


@dataclass(frozen=True, slots=True)
class SeverityOverrideRecord:
    id: int
    finding_id: int
    old_severity: str
    new_severity: str
    reason: str
    human_answer_id: int | None
    event_id: int
    created_at: int


@dataclass(frozen=True, slots=True)
class NewSeverityOverride:
    finding_id: int
    old_severity: str
    new_severity: str
    reason: str
    human_answer_id: int | None
    event_id: int
    created_at: int


@dataclass(frozen=True, slots=True)
class ReconciliationObservationRecord:
    id: int
    public_id: str
    campaign_id: int
    round_id: int
    lane_id: int
    lane_index: int
    attempt_id: int
    subject_id: int
    revision: str
    seq: int
    title: str
    body: str
    file_path: str | None
    line_start: int | None
    line_end: int | None
    evidence: str | None
    severity_suggested: str | None
    unchanged_from_id: int | None
    severity_effective: str
    dedup_key: str


@dataclass(frozen=True, slots=True)
class OpenFindingLedgerRecord:
    finding_id: int
    public_id: str
    subject_id: int
    title: str
    last_resolution: str | None
    last_authority: str | None
    period_start_event_id: int | None
    escalation_severity: str | None
    historical_max: str | None


@dataclass(frozen=True, slots=True)
class ClosedFindingLedgerRecord:
    finding_id: int
    public_id: str
    subject_id: int
    title: str
    last_resolution: str
    last_authority: str
    escalation_severity: str | None
    historical_max: str | None
    human_answer_id: int | None
    question_reason: str | None
    closing_snapshot_json: str | None


@dataclass(frozen=True, slots=True)
class ScopedFindingLedger:
    open: tuple[OpenFindingLedgerRecord, ...]
    closed: tuple[ClosedFindingLedgerRecord, ...]


@dataclass(frozen=True, slots=True)
class CurrentFindingRoundRecord:
    finding_id: int
    finding_public_id: str
    round_id: int
    round_no: int
    owner_lane_id: int
    entry_kind: str
    disposition: str | None
    disposition_reason: str | None
    author_attempt_id: int | None
    reviewer_decision: str | None
    reviewer_attempt_id: int | None
    decided_at: int | None


@dataclass(frozen=True, slots=True)
class DisputeCandidateRecord:
    finding_id: int
    finding_public_id: str
    owner_lane_id: int
    reviewer_decision: str
    period_start_event_id: int | None
    escalation_severity: str | None
    historical_max: str | None


@dataclass(frozen=True, slots=True)
class FindingStatus:
    finding_id: int
    status: str
    last_resolution: str | None
    last_authority: str | None


@dataclass(frozen=True, slots=True)
class FindingPeriod:
    finding_id: int
    period_start_event_id: int | None


@dataclass(frozen=True, slots=True)
class FindingSeverity:
    finding_id: int
    period_start_event_id: int | None
    escalation_severity: str | None
    historical_max: str | None


@dataclass(frozen=True, slots=True)
class FindingRoundHistoryEntry:
    finding_id: int
    finding_public_id: str
    round_id: int
    round_no: int
    round_kind: str
    entry_kind: str
    disposition: str | None
    reviewer_decision: str | None
    decided_at: int | None


class FindingRepository:
    def create_observation(
        self, tx: Transaction, value: NewReviewObservation
    ) -> ReviewObservationRecord:
        row = tx.fetch_one(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS seq FROM review_observation "
            "WHERE campaign_id = ?",
            (value.campaign_id,),
        )
        assert row is not None
        seq = row["seq"]
        result = tx.execute(
            "INSERT INTO review_observation("
            "public_id, campaign_id, round_id, lane_id, attempt_id, subject_id, "
            "revision, seq, title, body, file_path, line_start, line_end, evidence, "
            "severity_suggested, unchanged_from_id, severity_effective, dedup_key, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"O-{value.campaign_id}-{seq}",
                value.campaign_id,
                value.round_id,
                value.lane_id,
                value.attempt_id,
                value.subject_id,
                value.revision,
                seq,
                value.title,
                value.body,
                value.file_path,
                value.line_start,
                value.line_end,
                value.evidence,
                value.severity_suggested,
                value.unchanged_from_id,
                value.severity_effective,
                value.dedup_key,
                value.created_at,
            ),
        )
        assert result.lastrowid is not None
        record = tx.fetch_one(
            "SELECT * FROM review_observation WHERE id = ?", (result.lastrowid,)
        )
        assert record is not None
        return map_row(ReviewObservationRecord, record)

    def create_finding(self, tx: Transaction, value: NewFinding) -> FindingRecord:
        next_id = next_autoincrement_id(tx, "finding")
        result = tx.execute(
            "INSERT INTO finding("
            "public_id, run_id, subject_id, first_campaign_id, first_round_id, "
            "first_observation_id, first_revision, first_owner_lane_id, title, "
            "title_authority, title_changed_reason, event_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'runtime', NULL, ?, ?)",
            (
                f"F-{next_id}",
                value.run_id,
                value.subject_id,
                value.first_campaign_id,
                value.first_round_id,
                value.first_observation_id,
                value.first_revision,
                value.first_owner_lane_id,
                value.title,
                value.event_id,
                value.created_at,
            ),
        )
        if result.lastrowid != next_id:
            raise StoreError("finding allocator did not return the reserved ID")
        row = tx.fetch_one("SELECT * FROM finding WHERE id = ?", (next_id,))
        assert row is not None
        return map_row(FindingRecord, row)

    def create_observation_link(
        self, tx: Transaction, value: NewObservationLink
    ) -> ObservationLinkRecord:
        scope = tx.fetch_one(
            "SELECT c.run_id AS observation_run_id, f.run_id AS finding_run_id "
            "FROM review_observation o JOIN review_campaign c ON c.id = o.campaign_id "
            "JOIN finding f ON f.id = ? WHERE o.id = ?",
            (value.finding_id, value.observation_id),
        )
        if scope is None:
            raise repository_precondition("observation link endpoints do not exist")
        if scope["observation_run_id"] != scope["finding_run_id"]:
            raise repository_precondition("observation and finding belong to different runs")
        tx.execute(
            "INSERT INTO finding_observation_link("
            "observation_id, campaign_id, round_id, finding_id, link_type, "
            "decided_by_attempt_id, decided_by_role, decided_by_outcome, "
            "decided_by_human_answer_id, reason, event_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                value.observation_id,
                value.campaign_id,
                value.round_id,
                value.finding_id,
                value.link_type,
                value.decided_by_attempt_id,
                value.decided_by_role,
                value.decided_by_outcome,
                value.decided_by_human_answer_id,
                value.reason,
                value.event_id,
                value.created_at,
            ),
        )
        row = tx.fetch_one(
            "SELECT * FROM finding_observation_link WHERE observation_id = ?",
            (value.observation_id,),
        )
        assert row is not None
        return map_row(ObservationLinkRecord, row)

    def create_finding_round(
        self, tx: Transaction, value: NewFindingRound
    ) -> FindingRoundRecord:
        result = tx.execute(
            "INSERT INTO finding_round("
            "campaign_id, run_id, finding_id, round_no, round_id, owner_lane_id, "
            "entry_kind, disposition, disposition_reason, author_attempt_id, "
            "reviewer_decision, reviewer_attempt_id, decided_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)",
            (
                value.campaign_id,
                value.run_id,
                value.finding_id,
                value.round_no,
                value.round_id,
                value.owner_lane_id,
                value.entry_kind,
                value.disposition,
                value.disposition_reason,
                value.author_attempt_id,
            ),
        )
        assert result.lastrowid is not None
        row = tx.fetch_one("SELECT * FROM finding_round WHERE id = ?", (result.lastrowid,))
        assert row is not None
        return map_row(FindingRoundRecord, row)

    def record_reviewer_decision(
        self,
        tx: Transaction,
        finding_round_id: int,
        decision: ReviewerDecisionWrite,
    ) -> FindingRoundRecord:
        row = tx.fetch_one("SELECT id FROM finding_round WHERE id = ?", (finding_round_id,))
        if row is None:
            raise RepositoryRecordNotFound("finding_round", finding_round_id)
        tx.execute(
            "UPDATE finding_round SET reviewer_decision = ?, reviewer_attempt_id = ?, "
            "decided_at = ? WHERE id = ?",
            (
                decision.reviewer_decision,
                decision.reviewer_attempt_id,
                decision.decided_at,
                finding_round_id,
            ),
        )
        updated = tx.fetch_one("SELECT * FROM finding_round WHERE id = ?", (finding_round_id,))
        assert updated is not None
        return map_row(FindingRoundRecord, updated)

    def create_resolution(
        self, tx: Transaction, value: NewFindingResolution
    ) -> FindingResolutionRecord:
        return insert_record(tx, "finding_resolution", value, FindingResolutionRecord)

    def create_severity_override(
        self, tx: Transaction, value: NewSeverityOverride
    ) -> SeverityOverrideRecord:
        return insert_record(tx, "severity_override", value, SeverityOverrideRecord)

    def read_reconciliation_observations(
        self, db: ReadContext, round_id: int
    ) -> tuple[ReconciliationObservationRecord, ...]:
        rows = db.fetch_all(
            "SELECT o.id, o.public_id, o.campaign_id, o.round_id, o.lane_id, "
            "l.lane_index, o.attempt_id, o.subject_id, o.revision, o.seq, o.title, "
            "o.body, o.file_path, o.line_start, o.line_end, o.evidence, "
            "o.severity_suggested, o.unchanged_from_id, o.severity_effective, o.dedup_key "
            "FROM review_observation o JOIN review_lane l ON l.id = o.lane_id "
            "LEFT JOIN finding_observation_link link ON link.observation_id = o.id "
            "WHERE o.round_id = ? AND link.observation_id IS NULL ORDER BY o.id",
            (round_id,),
        )
        return map_rows(ReconciliationObservationRecord, rows)

    def read_scoped_finding_ledger(
        self, db: ReadContext, campaign_id: int
    ) -> ScopedFindingLedger:
        campaign = db.fetch_one(
            "SELECT run_id, subject_id FROM review_campaign WHERE id = ?", (campaign_id,)
        )
        if campaign is None:
            raise RepositoryRecordNotFound("review_campaign", campaign_id)
        common = (
            "WITH RECURSIVE subjects(id) AS (SELECT ? UNION ALL "
            "SELECT s.id FROM review_subject s JOIN subjects p ON s.parent_subject_id = p.id "
            "WHERE s.run_id = ?) "
        )
        open_rows = db.fetch_all(
            common
            + "SELECT f.id AS finding_id, f.public_id, f.subject_id, f.title, "
            "fs.last_resolution, fs.last_authority, fp.period_start_event_id, "
            "sev.escalation_severity, sev.historical_max FROM finding f "
            "JOIN subjects s ON s.id = f.subject_id JOIN finding_status fs ON fs.finding_id = f.id "
            "JOIN finding_period fp ON fp.finding_id = f.id "
            "JOIN finding_severity sev ON sev.finding_id = f.id "
            "WHERE f.run_id = ? AND fs.status = 'open' ORDER BY f.id",
            (campaign["subject_id"], campaign["run_id"], campaign["run_id"]),
        )
        closed_rows = db.fetch_all(
            common
            + "SELECT f.id AS finding_id, f.public_id, f.subject_id, f.title, "
            "fs.last_resolution, fs.last_authority, sev.escalation_severity, "
            "sev.historical_max, CASE WHEN fs.last_authority = 'human' THEN r.human_answer_id END "
            "AS human_answer_id, CASE WHEN fs.last_authority = 'human' THEN q.reason END "
            "AS question_reason, CASE WHEN fs.last_authority = 'human' THEN q.snapshot_json END "
            "AS closing_snapshot_json FROM finding f JOIN subjects s ON s.id = f.subject_id "
            "JOIN finding_status fs ON fs.finding_id = f.id "
            "JOIN finding_severity sev ON sev.finding_id = f.id "
            "LEFT JOIN finding_resolution r ON r.finding_id = f.id "
            "AND r.event_id = (SELECT MAX(r2.event_id) FROM finding_resolution r2 "
            "WHERE r2.finding_id = f.id) LEFT JOIN human_answer a ON a.id = r.human_answer_id "
            "LEFT JOIN human_question q ON q.id = a.question_id "
            "WHERE f.run_id = ? AND fs.status = 'closed' ORDER BY f.id",
            (campaign["subject_id"], campaign["run_id"], campaign["run_id"]),
        )
        return ScopedFindingLedger(
            open=map_rows(OpenFindingLedgerRecord, open_rows),
            closed=map_rows(ClosedFindingLedgerRecord, closed_rows),
        )

    def read_current_finding_rounds(
        self, db: ReadContext, round_id: int
    ) -> tuple[CurrentFindingRoundRecord, ...]:
        rows = db.fetch_all(
            "SELECT fr.finding_id, f.public_id AS finding_public_id, fr.round_id, "
            "fr.round_no, fr.owner_lane_id, fr.entry_kind, fr.disposition, "
            "fr.disposition_reason, fr.author_attempt_id, fr.reviewer_decision, "
            "fr.reviewer_attempt_id, fr.decided_at FROM finding_round fr "
            "JOIN finding f ON f.id = fr.finding_id WHERE fr.round_id = ? "
            "ORDER BY fr.finding_id",
            (round_id,),
        )
        return map_rows(CurrentFindingRoundRecord, rows)

    def read_dispute_candidates(
        self, db: ReadContext, round_id: int
    ) -> tuple[DisputeCandidateRecord, ...]:
        rows = db.fetch_all(
            "SELECT fr.finding_id, f.public_id AS finding_public_id, fr.owner_lane_id, "
            "fr.reviewer_decision, sev.period_start_event_id, sev.escalation_severity, "
            "sev.historical_max FROM finding_round fr JOIN finding f ON f.id = fr.finding_id "
            "JOIN finding_status fs ON fs.finding_id = fr.finding_id "
            "JOIN finding_severity sev ON sev.finding_id = fr.finding_id "
            "WHERE fr.round_id = ? AND fr.reviewer_decision = 'insists' "
            "AND fs.status = 'open' ORDER BY fr.finding_id",
            (round_id,),
        )
        return map_rows(DisputeCandidateRecord, rows)

    def read_finding_status(
        self, db: ReadContext, finding_id: int
    ) -> FindingStatus | None:
        return map_optional(
            FindingStatus,
            db.fetch_one("SELECT * FROM finding_status WHERE finding_id = ?", (finding_id,)),
        )

    def read_finding_period(
        self, db: ReadContext, finding_id: int
    ) -> FindingPeriod | None:
        return map_optional(
            FindingPeriod,
            db.fetch_one("SELECT * FROM finding_period WHERE finding_id = ?", (finding_id,)),
        )

    def read_finding_severity(
        self, db: ReadContext, finding_id: int
    ) -> FindingSeverity | None:
        return map_optional(
            FindingSeverity,
            db.fetch_one("SELECT * FROM finding_severity WHERE finding_id = ?", (finding_id,)),
        )

    def read_finding_round_history(
        self,
        db: ReadContext,
        campaign_id: int,
        finding_ids: tuple[int, ...],
    ) -> tuple[FindingRoundHistoryEntry, ...]:
        if not finding_ids:
            return ()
        placeholders = ", ".join("?" for _ in finding_ids)
        rows = db.fetch_all(
            "SELECT fr.finding_id, f.public_id AS finding_public_id, fr.round_id, "
            "fr.round_no, rr.kind AS round_kind, fr.entry_kind, fr.disposition, "
            "fr.reviewer_decision, fr.decided_at FROM finding_round fr "
            "JOIN finding f ON f.id = fr.finding_id JOIN review_round rr ON rr.id = fr.round_id "
            f"WHERE fr.campaign_id = ? AND fr.finding_id IN ({placeholders}) "
            "ORDER BY fr.finding_id, fr.round_no",
            (campaign_id, *finding_ids),
        )
        return map_rows(FindingRoundHistoryEntry, rows)

    def find_links_with_foreign_finding_run(
        self, db: ReadContext, run_id: int
    ) -> tuple[int, ...]:
        rows = db.fetch_all(
            "SELECT l.observation_id FROM finding_observation_link l "
            "JOIN review_observation o ON o.id = l.observation_id "
            "JOIN review_campaign c ON c.id = o.campaign_id "
            "JOIN finding f ON f.id = l.finding_id "
            "WHERE c.run_id = ? AND f.run_id <> c.run_id ORDER BY l.observation_id",
            (run_id,),
        )
        return tuple(row["observation_id"] for row in rows)
