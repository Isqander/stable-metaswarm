from __future__ import annotations

from dataclasses import dataclass

from ..db import Transaction
from ._mapping import (
    ReadContext,
    RepositoryAlreadyTerminal,
    RepositoryRecordNotFound,
    insert_record,
    map_optional,
    map_row,
    map_rows,
    repository_precondition,
)


@dataclass(frozen=True, slots=True)
class ReviewSubjectRecord:
    id: int
    run_id: int
    kind: str
    target_ref: str
    revision: str
    parent_subject_id: int | None
    created_at: int


@dataclass(frozen=True, slots=True)
class NewReviewSubject:
    run_id: int
    kind: str
    target_ref: str
    revision: str
    parent_subject_id: int | None
    created_at: int


@dataclass(frozen=True, slots=True)
class ReviewCampaignRecord:
    id: int
    public_id: str
    run_id: int
    stage_id: int
    subject_id: int
    ordinal: int
    severity_threshold: str
    policy_version: str
    expected_lane_count: int
    state: str
    opened_at: int
    closed_at: int | None
    close_reason: str | None


@dataclass(frozen=True, slots=True)
class NewReviewCampaign:
    public_id: str
    run_id: int
    stage_id: int
    subject_id: int
    ordinal: int
    severity_threshold: str
    policy_version: str
    expected_lane_count: int
    opened_at: int


@dataclass(frozen=True, slots=True)
class ReviewLaneRecord:
    id: int
    campaign_id: int
    run_id: int
    lane_index: int


@dataclass(frozen=True, slots=True)
class NewReviewLane:
    campaign_id: int
    run_id: int
    lane_index: int


@dataclass(frozen=True, slots=True)
class LaneAssignmentRecord:
    id: int
    lane_id: int
    run_id: int
    generation: int
    profile_id: str
    replaces_id: int | None
    session_id: int | None
    human_answer_id: int | None
    event_id: int
    assigned_at: int


@dataclass(frozen=True, slots=True)
class NewLaneAssignment:
    lane_id: int
    run_id: int
    generation: int
    profile_id: str
    replaces_id: int | None
    session_id: int | None
    human_answer_id: int | None
    event_id: int
    assigned_at: int


@dataclass(frozen=True, slots=True)
class LaneWaiverRecord:
    id: int
    campaign_id: int
    round_no: int
    lane_id: int
    human_answer_id: int
    event_id: int
    created_at: int


@dataclass(frozen=True, slots=True)
class ReviewRoundRecord:
    id: int
    campaign_id: int
    round_no: int
    kind: str
    preceding_revision_id: int | None
    result: str | None
    opened_at: int
    closed_at: int | None


@dataclass(frozen=True, slots=True)
class NewReviewRound:
    campaign_id: int
    round_no: int
    kind: str
    preceding_revision_id: int | None
    opened_at: int


@dataclass(frozen=True, slots=True)
class AuthorRevisionRecord:
    id: int
    campaign_id: int
    stage_id: int
    revision_no: int
    attempt_id: int
    attempt_role: str
    attempt_outcome: str
    input_sha: str | None
    output_sha: str | None
    artifact_revision_id: int | None
    completed_at: int


@dataclass(frozen=True, slots=True)
class NewAuthorRevision:
    campaign_id: int
    stage_id: int
    revision_no: int
    attempt_id: int
    attempt_role: str
    attempt_outcome: str
    input_sha: str | None
    output_sha: str | None
    artifact_revision_id: int | None
    completed_at: int


@dataclass(frozen=True, slots=True)
class EffectiveLaneRecord:
    campaign_id: int
    lane_id: int
    lane_index: int
    assignment_id: int
    generation: int
    profile_id: str
    provider: str
    model: str


@dataclass(frozen=True, slots=True)
class CampaignCounters:
    campaign_id: int
    author_revision_count: int
    review_check_count: int


class CampaignRepository:
    def get_campaign(
        self, db: ReadContext, campaign_id: int
    ) -> ReviewCampaignRecord | None:
        return map_optional(
            ReviewCampaignRecord,
            db.fetch_one("SELECT * FROM review_campaign WHERE id = ?", (campaign_id,)),
        )

    def get_round(
        self, db: ReadContext, round_id: int
    ) -> ReviewRoundRecord | None:
        return map_optional(
            ReviewRoundRecord,
            db.fetch_one("SELECT * FROM review_round WHERE id = ?", (round_id,)),
        )

    def create_subject(
        self, tx: Transaction, value: NewReviewSubject
    ) -> ReviewSubjectRecord:
        if value.parent_subject_id is not None:
            parent = tx.fetch_one(
                "WITH RECURSIVE ancestry(id, parent_subject_id, path, cycle) AS ("
                "SELECT id, parent_subject_id, printf('/%d/', id), 0 "
                "FROM review_subject WHERE id = ? AND run_id = ? UNION ALL "
                "SELECT p.id, p.parent_subject_id, ancestry.path || p.id || '/', "
                "instr(ancestry.path, printf('/%d/', p.id)) > 0 "
                "FROM review_subject p JOIN ancestry ON p.id = ancestry.parent_subject_id "
                "WHERE ancestry.cycle = 0) "
                "SELECT COUNT(*) AS count, MAX(cycle) AS cycle FROM ancestry",
                (value.parent_subject_id, value.run_id),
            )
            assert parent is not None
            if parent["count"] == 0:
                raise RepositoryRecordNotFound("review_subject", value.parent_subject_id)
            if parent["cycle"]:
                raise repository_precondition("review subject ancestry contains a cycle")
        return insert_record(tx, "review_subject", value, ReviewSubjectRecord)

    def create_campaign(
        self, tx: Transaction, value: NewReviewCampaign
    ) -> ReviewCampaignRecord:
        result = tx.execute(
            "INSERT INTO review_campaign("
            "public_id, run_id, stage_id, subject_id, ordinal, severity_threshold, "
            "policy_version, expected_lane_count, state, opened_at, closed_at, close_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'discovery', ?, NULL, NULL)",
            (
                value.public_id,
                value.run_id,
                value.stage_id,
                value.subject_id,
                value.ordinal,
                value.severity_threshold,
                value.policy_version,
                value.expected_lane_count,
                value.opened_at,
            ),
        )
        assert result.lastrowid is not None
        row = tx.fetch_one("SELECT * FROM review_campaign WHERE id = ?", (result.lastrowid,))
        assert row is not None
        return map_row(ReviewCampaignRecord, row)

    def create_lane(
        self, tx: Transaction, value: NewReviewLane
    ) -> ReviewLaneRecord:
        return insert_record(tx, "review_lane", value, ReviewLaneRecord)

    def create_lane_assignment(
        self, tx: Transaction, value: NewLaneAssignment
    ) -> LaneAssignmentRecord:
        return insert_record(tx, "lane_assignment", value, LaneAssignmentRecord)

    def create_round(
        self, tx: Transaction, value: NewReviewRound
    ) -> ReviewRoundRecord:
        result = tx.execute(
            "INSERT INTO review_round("
            "campaign_id, round_no, kind, preceding_revision_id, result, opened_at, closed_at) "
            "VALUES (?, ?, ?, ?, NULL, ?, NULL)",
            (
                value.campaign_id,
                value.round_no,
                value.kind,
                value.preceding_revision_id,
                value.opened_at,
            ),
        )
        assert result.lastrowid is not None
        row = tx.fetch_one("SELECT * FROM review_round WHERE id = ?", (result.lastrowid,))
        assert row is not None
        return map_row(ReviewRoundRecord, row)

    def create_author_revision(
        self, tx: Transaction, value: NewAuthorRevision
    ) -> AuthorRevisionRecord:
        return insert_record(tx, "author_revision", value, AuthorRevisionRecord)

    def read_effective_roster(
        self, db: ReadContext, campaign_id: int
    ) -> tuple[EffectiveLaneRecord, ...]:
        return map_rows(
            EffectiveLaneRecord,
            db.fetch_all(
                "SELECT * FROM effective_roster WHERE campaign_id = ? ORDER BY lane_index",
                (campaign_id,),
            ),
        )

    def read_campaign_counters(
        self, db: ReadContext, campaign_id: int
    ) -> CampaignCounters | None:
        return map_optional(
            CampaignCounters,
            db.fetch_one(
                "SELECT * FROM campaign_counters WHERE campaign_id = ?", (campaign_id,)
            ),
        )

    def transition_campaign_state(
        self,
        tx: Transaction,
        campaign_id: int,
        expected: str,
        new: str,
        closed_at: int | None,
    ) -> ReviewCampaignRecord:
        result = tx.execute(
            "UPDATE review_campaign SET state = ?, closed_at = ? "
            "WHERE id = ? AND state = ?",
            (new, closed_at, campaign_id, expected),
        )
        if result.rowcount == 0:
            row = tx.fetch_one("SELECT state FROM review_campaign WHERE id = ?", (campaign_id,))
            if row is None:
                raise RepositoryRecordNotFound("review_campaign", campaign_id)
            raise repository_precondition(
                f"campaign {campaign_id} expected state {expected}, got {row['state']}"
            )
        row = tx.fetch_one("SELECT * FROM review_campaign WHERE id = ?", (campaign_id,))
        assert row is not None
        return map_row(ReviewCampaignRecord, row)

    def replace_lane_assignment(
        self,
        tx: Transaction,
        round_id: int,
        lane_id: int,
        human_answer_id: int,
        profile_id: str,
        event_id: int,
        assigned_at: int,
    ) -> LaneAssignmentRecord:
        round_row = self._require_discovery_lane_failure_answer(
            tx, round_id, human_answer_id
        )
        current = tx.fetch_one(
            "SELECT er.* FROM effective_roster er "
            "WHERE er.campaign_id = ? AND er.lane_id = ? "
            "AND EXISTS (SELECT 1 FROM step_attempt a WHERE a.round_id = ? "
            "AND a.lane_assignment_id = er.assignment_id AND a.role = 'reviewer' "
            "AND a.outcome IS NOT NULL AND a.outcome <> 'succeeded') "
            "AND NOT EXISTS (SELECT 1 FROM step_attempt a WHERE a.round_id = ? "
            "AND a.lane_assignment_id = er.assignment_id AND a.role = 'reviewer' "
            "AND (a.outcome IS NULL OR a.outcome = 'succeeded')) ORDER BY er.lane_index",
            (round_row["campaign_id"], lane_id, round_id, round_id),
        )
        if current is None:
            raise repository_precondition(
                "lane replacement requires a failed current assignment in this round"
            )
        campaign = tx.fetch_one(
            "SELECT run_id FROM review_campaign WHERE id = ?", (round_row["campaign_id"],)
        )
        assert campaign is not None
        return self.create_lane_assignment(
            tx,
            NewLaneAssignment(
                lane_id=current["lane_id"],
                run_id=campaign["run_id"],
                generation=current["generation"] + 1,
                profile_id=profile_id,
                replaces_id=current["assignment_id"],
                session_id=None,
                human_answer_id=human_answer_id,
                event_id=event_id,
                assigned_at=assigned_at,
            ),
        )

    def waive_lane_for_round(
        self,
        tx: Transaction,
        round_id: int,
        lane_id: int,
        human_answer_id: int,
        event_id: int,
        created_at: int,
    ) -> LaneWaiverRecord:
        round_row = self._require_discovery_lane_failure_answer(
            tx, round_id, human_answer_id
        )
        value = LaneWaiverRecord(
            id=0,
            campaign_id=round_row["campaign_id"],
            round_no=round_row["round_no"],
            lane_id=lane_id,
            human_answer_id=human_answer_id,
            event_id=event_id,
            created_at=created_at,
        )
        result = tx.execute(
            "INSERT INTO lane_waiver(campaign_id, round_no, lane_id, human_answer_id, "
            "event_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                value.campaign_id,
                value.round_no,
                value.lane_id,
                value.human_answer_id,
                value.event_id,
                value.created_at,
            ),
        )
        assert result.lastrowid is not None
        row = tx.fetch_one("SELECT * FROM lane_waiver WHERE id = ?", (result.lastrowid,))
        assert row is not None
        return map_row(LaneWaiverRecord, row)

    def close_round(
        self, tx: Transaction, round_id: int, result: str, closed_at: int
    ) -> ReviewRoundRecord:
        update = tx.execute(
            "UPDATE review_round SET result = ?, closed_at = ? "
            "WHERE id = ? AND result IS NULL",
            (result, closed_at, round_id),
        )
        if update.rowcount == 0:
            row = tx.fetch_one("SELECT result FROM review_round WHERE id = ?", (round_id,))
            if row is None:
                raise RepositoryRecordNotFound("review_round", round_id)
            raise RepositoryAlreadyTerminal("review_round", round_id, row["result"])
        row = tx.fetch_one("SELECT * FROM review_round WHERE id = ?", (round_id,))
        assert row is not None
        return map_row(ReviewRoundRecord, row)

    def find_missing_discovery_lane_participation(
        self, db: ReadContext, round_id: int
    ) -> tuple[int, ...]:
        self._require_round(db, round_id)
        rows = db.fetch_all(
            "SELECT l.id FROM review_round rr JOIN review_lane l "
            "ON l.campaign_id = rr.campaign_id WHERE rr.id = ? "
            "AND rr.kind = 'discovery' AND NOT EXISTS (SELECT 1 FROM lane_waiver w "
            "WHERE w.campaign_id = rr.campaign_id AND w.round_no = rr.round_no "
            "AND w.lane_id = l.id) AND NOT EXISTS (SELECT 1 FROM effective_roster er "
            "JOIN step_attempt a ON a.round_id = rr.id "
            "AND a.lane_assignment_id = er.assignment_id AND a.role = 'reviewer' "
            "AND a.outcome = 'succeeded' WHERE er.lane_id = l.id) ORDER BY l.id",
            (round_id,),
        )
        return tuple(row["id"] for row in rows)

    def find_discovery_without_successful_opinion(
        self, db: ReadContext, round_id: int
    ) -> tuple[int, ...]:
        self._require_round(db, round_id)
        rows = db.fetch_all(
            "SELECT rr.id FROM review_round rr WHERE rr.id = ? AND rr.kind = 'discovery' "
            "AND NOT EXISTS (SELECT 1 FROM effective_roster er JOIN step_attempt a "
            "ON a.round_id = rr.id AND a.lane_assignment_id = er.assignment_id "
            "AND a.role = 'reviewer' AND a.outcome = 'succeeded' "
            "WHERE er.campaign_id = rr.campaign_id)",
            (round_id,),
        )
        return tuple(row["id"] for row in rows)

    def find_discovery_roster_cardinality_mismatch(
        self, db: ReadContext, round_id: int
    ) -> tuple[int, ...]:
        self._require_round(db, round_id)
        rows = db.fetch_all(
            "SELECT rr.campaign_id FROM review_round rr JOIN review_campaign c "
            "ON c.id = rr.campaign_id WHERE rr.id = ? AND rr.kind = 'discovery' "
            "AND (SELECT COUNT(*) FROM effective_roster er "
            "WHERE er.campaign_id = c.id) <> c.expected_lane_count",
            (round_id,),
        )
        return tuple(row["campaign_id"] for row in rows)

    def find_unlinked_observations(
        self, db: ReadContext, round_id: int
    ) -> tuple[int, ...]:
        self._require_round(db, round_id)
        rows = db.fetch_all(
            "SELECT o.id FROM review_observation o LEFT JOIN finding_observation_link l "
            "ON l.observation_id = o.id WHERE o.round_id = ? "
            "AND l.observation_id IS NULL ORDER BY o.id",
            (round_id,),
        )
        return tuple(row["id"] for row in rows)

    def find_missing_finding_participation(
        self, db: ReadContext, round_id: int
    ) -> tuple[int, ...]:
        self._require_round(db, round_id)
        rows = db.fetch_all(
            "SELECT fs.finding_id FROM review_round rr CROSS JOIN finding_status fs "
            "WHERE fs.status = 'open' AND rr.id = ? AND EXISTS (SELECT 1 "
            "FROM finding_observation_link l JOIN review_observation o "
            "ON o.id = l.observation_id WHERE l.finding_id = fs.finding_id "
            "AND o.campaign_id = rr.campaign_id) AND NOT EXISTS (SELECT 1 "
            "FROM finding_round fr WHERE fr.round_id = rr.id "
            "AND fr.campaign_id = rr.campaign_id AND fr.round_no = rr.round_no "
            "AND fr.finding_id = fs.finding_id) ORDER BY fs.finding_id",
            (round_id,),
        )
        return tuple(row["finding_id"] for row in rows)

    def find_incomplete_issued(
        self, db: ReadContext, round_id: int
    ) -> tuple[int, ...]:
        self._require_round(db, round_id)
        rows = db.fetch_all(
            "SELECT fr.finding_id FROM review_round rr JOIN finding_round fr "
            "ON fr.round_id = rr.id AND fr.campaign_id = rr.campaign_id "
            "AND fr.round_no = rr.round_no JOIN review_campaign c ON c.id = fr.campaign_id "
            "LEFT JOIN author_revision ar ON ar.id = rr.preceding_revision_id "
            "AND ar.campaign_id = fr.campaign_id "
            "LEFT JOIN step_attempt d ON d.id = fr.author_attempt_id "
            "LEFT JOIN step_attempt a ON a.id = fr.reviewer_attempt_id "
            "WHERE rr.id = ? AND fr.entry_kind = 'issued' AND (rr.kind <> 'fix_check' "
            "OR ar.id IS NULL OR fr.disposition IS NULL OR fr.author_attempt_id IS NULL "
            "OR fr.author_attempt_id <> ar.attempt_id OR d.id IS NULL OR d.role <> 'author' "
            "OR d.outcome <> 'succeeded' OR d.stage_id <> c.stage_id "
            "OR fr.reviewer_decision IS NULL OR a.id IS NULL OR a.role <> 'reviewer' "
            "OR a.outcome <> 'succeeded' OR a.stage_id <> c.stage_id "
            "OR a.round_id <> rr.id OR a.lane_id <> fr.owner_lane_id) "
            "ORDER BY fr.finding_id",
            (round_id,),
        )
        return tuple(row["finding_id"] for row in rows)

    def _require_round(self, db: ReadContext, round_id: int) -> object:
        row = db.fetch_one("SELECT * FROM review_round WHERE id = ?", (round_id,))
        if row is None:
            raise RepositoryRecordNotFound("review_round", round_id)
        return row

    def _require_discovery_lane_failure_answer(
        self, tx: Transaction, round_id: int, human_answer_id: int
    ) -> object:
        round_row = self._require_round(tx, round_id)
        if round_row["kind"] != "discovery" or round_row["result"] is not None:
            raise repository_precondition("lane replacement and waiver require an open discovery round")
        answer = tx.fetch_one(
            "SELECT q.campaign_id, q.round_id, q.reason FROM human_answer a "
            "JOIN human_question q ON q.id = a.question_id WHERE a.id = ?",
            (human_answer_id,),
        )
        if (
            answer is None
            or answer["reason"] != "lane_failure"
            or answer["campaign_id"] != round_row["campaign_id"]
            or answer["round_id"] not in (None, round_id)
        ):
            raise repository_precondition("human answer does not authorize this lane action")
        return round_row
