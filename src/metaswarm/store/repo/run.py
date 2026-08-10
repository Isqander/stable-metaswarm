from __future__ import annotations

from dataclasses import dataclass

from ..db import Transaction
from ._mapping import (
    ReadContext,
    RepositoryRecordNotFound,
    insert_record,
    map_optional,
    map_row,
    map_rows,
    repository_precondition,
)


@dataclass(frozen=True, slots=True)
class RunRecord:
    id: int
    public_id: str
    flow_id: str
    flow_hash: str
    project_config_hash: str
    profiles_config_hash: str
    core_version: str
    schema_version: int
    instance_profile: str
    code_repo_path: str
    code_sha: str
    task_text: str
    created_at: int
    pause_requested_at: int | None
    cancel_requested_at: int | None
    finished_at: int | None
    terminal_state: str | None


@dataclass(frozen=True, slots=True)
class NewRun:
    public_id: str
    flow_id: str
    flow_hash: str
    project_config_hash: str
    profiles_config_hash: str
    core_version: str
    schema_version: int
    instance_profile: str
    code_repo_path: str
    code_sha: str
    task_text: str
    created_at: int
    pause_requested_at: int | None
    cancel_requested_at: int | None
    finished_at: int | None
    terminal_state: str | None


@dataclass(frozen=True, slots=True)
class BranchRecord:
    id: int
    run_id: int
    public_id: str
    kind: str
    task_id: int | None
    state: str
    created_at: int


@dataclass(frozen=True, slots=True)
class NewBranch:
    run_id: int
    public_id: str
    kind: str
    task_id: int | None
    state: str
    created_at: int


@dataclass(frozen=True, slots=True)
class StageExecutionRecord:
    id: int
    run_id: int
    branch_id: int
    stage_key: str
    ordinal: int
    state: str
    max_author_revisions: int
    severity_threshold: str
    started_at: int | None
    finished_at: int | None


@dataclass(frozen=True, slots=True)
class NewStageExecution:
    run_id: int
    branch_id: int
    stage_key: str
    ordinal: int
    state: str
    max_author_revisions: int
    severity_threshold: str
    started_at: int | None
    finished_at: int | None


@dataclass(frozen=True, slots=True)
class RunState:
    run_id: int
    state: str


@dataclass(frozen=True, slots=True)
class OpenBlockerRecord:
    blocker_id: int
    kind: str
    branch_id: int | None
    task_id: int | None
    stage_id: int | None
    question_id: int | None
    detail: str | None
    created_at: int


class RunRepository:
    def create_run(self, tx: Transaction, value: NewRun) -> RunRecord:
        return insert_record(tx, "run", value, RunRecord)

    def create_branch(self, tx: Transaction, value: NewBranch) -> BranchRecord:
        return insert_record(tx, "branch", value, BranchRecord)

    def create_stage(
        self, tx: Transaction, value: NewStageExecution
    ) -> StageExecutionRecord:
        return insert_record(tx, "stage_execution", value, StageExecutionRecord)

    def get_run(self, db: ReadContext, run_id: int) -> RunRecord | None:
        return map_optional(RunRecord, db.fetch_one("SELECT * FROM run WHERE id = ?", (run_id,)))

    def get_branch(self, db: ReadContext, branch_id: int) -> BranchRecord | None:
        return map_optional(
            BranchRecord, db.fetch_one("SELECT * FROM branch WHERE id = ?", (branch_id,))
        )

    def get_stage(self, db: ReadContext, stage_id: int) -> StageExecutionRecord | None:
        return map_optional(
            StageExecutionRecord,
            db.fetch_one("SELECT * FROM stage_execution WHERE id = ?", (stage_id,)),
        )

    def read_run_state(self, db: ReadContext, run_id: int) -> RunState | None:
        return map_optional(
            RunState, db.fetch_one("SELECT * FROM run_state WHERE run_id = ?", (run_id,))
        )

    def read_open_blockers(
        self, db: ReadContext, run_id: int
    ) -> tuple[OpenBlockerRecord, ...]:
        rows = db.fetch_all(
            "SELECT id AS blocker_id, kind, branch_id, task_id, stage_id, "
            "question_id, detail, created_at FROM blocker "
            "WHERE run_id = ? AND cleared_at IS NULL ORDER BY id",
            (run_id,),
        )
        return map_rows(OpenBlockerRecord, rows)

    def find_human_blockers_on_unblocked_branches(
        self, db: ReadContext, run_id: int
    ) -> tuple[int, ...]:
        rows = db.fetch_all(
            "SELECT bl.id FROM blocker bl JOIN branch b ON b.id = bl.branch_id "
            "WHERE bl.run_id = ? AND bl.cleared_at IS NULL "
            "AND bl.kind IN ('human_question', 'awaiting_continue') "
            "AND b.state <> 'blocked' ORDER BY bl.id",
            (run_id,),
        )
        return tuple(row["id"] for row in rows)

    def find_blocked_branches_without_blocker(
        self, db: ReadContext, run_id: int
    ) -> tuple[int, ...]:
        rows = db.fetch_all(
            "SELECT b.id FROM branch b WHERE b.run_id = ? AND b.state = 'blocked' "
            "AND NOT EXISTS (SELECT 1 FROM blocker bl WHERE bl.branch_id = b.id "
            "AND bl.run_id = b.run_id AND bl.cleared_at IS NULL) ORDER BY b.id",
            (run_id,),
        )
        return tuple(row["id"] for row in rows)

    def find_questions_without_matching_blocker(
        self, db: ReadContext, run_id: int
    ) -> tuple[int, ...]:
        rows = db.fetch_all(
            "SELECT q.id FROM human_question q LEFT JOIN blocker bl "
            "ON bl.question_id = q.id AND bl.branch_id = q.branch_id "
            "AND bl.run_id = q.run_id AND bl.kind = 'human_question' "
            "AND bl.cleared_at IS NULL WHERE q.run_id = ? "
            "AND q.branch_id IS NOT NULL AND q.answered_at IS NULL "
            "AND bl.id IS NULL ORDER BY q.id",
            (run_id,),
        )
        return tuple(row["id"] for row in rows)

    def set_branch_state(
        self, tx: Transaction, branch_id: int, expected: str, new: str
    ) -> BranchRecord:
        result = tx.execute(
            "UPDATE branch SET state = ? WHERE id = ? AND state = ?",
            (new, branch_id, expected),
        )
        if result.rowcount == 0:
            row = tx.fetch_one("SELECT state FROM branch WHERE id = ?", (branch_id,))
            if row is None:
                raise RepositoryRecordNotFound("branch", branch_id)
            raise repository_precondition(
                f"branch {branch_id} expected state {expected}, got {row['state']}"
            )
        row = tx.fetch_one("SELECT * FROM branch WHERE id = ?", (branch_id,))
        assert row is not None
        return map_row(BranchRecord, row)
