from __future__ import annotations

import asyncio
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest

import metaswarm.store.db as db_module
from metaswarm.store import (
    Database,
    IncompatibleSchemaError,
    MigrationError,
    NewRunEvent,
    Transaction,
    append_run_event,
)

SIMPLE_SEEDS: dict[str, tuple[str, set[str]]] = {
    "attempt_outcome": (
        "outcome",
        {"succeeded", "interrupted", "hung", "transient", "contract_error", "failed"},
    ),
    "attempt_role": ("role", {"author", "reviewer", "planner", "reconciler"}),
    "heartbeat_source": ("source", {"stdout", "stderr", "fs"}),
    "branch_kind": ("kind", {"pipeline", "task"}),
    "branch_state": (
        "state",
        {"ready", "running", "retry_wait", "blocked", "done", "failed", "cancelled"},
    ),
    "run_terminal_state": ("state", {"succeeded", "failed", "cancelled"}),
    "campaign_state": (
        "state",
        {
            "discovery",
            "reconciliation",
            "fix_cycle",
            "closed_clean",
            "closed_escalated",
            "closed_cancelled",
        },
    ),
    "review_round_kind": ("kind", {"discovery", "fix_check"}),
    "round_result": ("result", {"clean", "needs_revision", "escalated"}),
    "finding_round_entry_kind": ("entry_kind", {"issued", "post_check"}),
    "subject_kind": ("kind", {"code", "artifact", "task", "stage"}),
    "link_type": ("link_type", {"first_seen", "recurrence", "reaffirmation", "reopening"}),
    "disposition": ("value", {"fixed", "rejected", "wont_fix"}),
    "reviewer_decision": (
        "value",
        {"verified_fixed", "still_present", "accepted_reason", "insists"},
    ),
    "resolution_authority": ("value", {"reviewer", "human", "policy"}),
    "blocker_kind": (
        "kind",
        {"human_question", "awaiting_continue", "dependency", "drift", "invalid_graph"},
    ),
    "task_state": (
        "state",
        {"pending", "ready", "running", "done", "invalidated", "cancelled"},
    ),
    "title_authority": ("authority", {"runtime", "human"}),
    "question_reason": (
        "reason",
        {
            "cap_exhausted_same",
            "cap_exhausted_new",
            "dispute",
            "contract_error",
            "hang",
            "baseline_red",
            "approval_gate",
            "open_question",
            "reopen_human_closed",
            "reconcile_failed",
            "lane_failure",
            "verification_policy",
        },
    ),
    "transport_kind": ("transport", {"telegram", "cli"}),
    "artifact_kind": (
        "kind",
        {"design", "breakdown", "task_plan", "cutoff", "verification", "notes"},
    ),
    "artifact_producer": ("producer", {"agent", "human"}),
    "verification_purpose": ("purpose", {"baseline", "after_fix", "final"}),
    "verification_plan_source": ("source", {"recipe", "agent"}),
    "verification_status": ("status", {"green", "red", "error"}),
}

CAMPAIGN_TRANSITIONS = {
    ("discovery", "reconciliation"),
    ("discovery", "closed_cancelled"),
    ("reconciliation", "fix_cycle"),
    ("reconciliation", "closed_clean"),
    ("reconciliation", "closed_cancelled"),
    ("fix_cycle", "fix_cycle"),
    ("fix_cycle", "closed_clean"),
    ("fix_cycle", "closed_escalated"),
    ("fix_cycle", "closed_cancelled"),
}


def _lastrowid(result) -> int:
    assert result.lastrowid is not None
    return result.lastrowid


def _insert_run(transaction: Transaction, suffix: str) -> int:
    return _lastrowid(
        transaction.execute(
            """
            INSERT INTO run(
              public_id, flow_id, flow_hash, project_config_hash, profiles_config_hash,
              core_version, schema_version, instance_profile, code_repo_path, code_sha,
              task_text, created_at
            ) VALUES (?, 'flow', 'flow-hash', 'project-hash', 'profiles-hash',
                      'test-core', 1, 'test', '/code', 'abc123', 'task', 1)
            """,
            (f"R-{suffix}",),
        )
    )


def _append_event(transaction: Transaction, run_id: int, kind: str, created_at: int) -> int:
    return append_run_event(
        transaction,
        NewRunEvent(
            run_id=run_id,
            kind=kind,
            payload={"kind": kind},
            created_at=created_at,
        ),
    )


def _create_review_fixture(transaction: Transaction, suffix: str) -> dict[str, Any]:
    run_id = _insert_run(transaction, suffix)
    branch_id = _lastrowid(
        transaction.execute(
            "INSERT INTO branch(run_id, public_id, kind, state, created_at) "
            "VALUES (?, ?, 'pipeline', 'ready', 1)",
            (run_id, f"B-{suffix}"),
        )
    )
    stage_id = _lastrowid(
        transaction.execute(
            "INSERT INTO stage_execution"
            "(run_id, branch_id, stage_key, ordinal, state, max_author_revisions, "
            "severity_threshold) VALUES (?, ?, 'review', 1, 'running', 1, 'high')",
            (run_id, branch_id),
        )
    )
    subject_id = _lastrowid(
        transaction.execute(
            "INSERT INTO review_subject(run_id, kind, target_ref, revision, created_at) "
            "VALUES (?, 'code', ?, ?, 1)",
            (run_id, f"target-{suffix}", f"revision-{suffix}"),
        )
    )
    campaign_id = _lastrowid(
        transaction.execute(
            "INSERT INTO review_campaign"
            "(public_id, run_id, stage_id, subject_id, ordinal, severity_threshold, "
            "policy_version, expected_lane_count, state, opened_at) "
            "VALUES (?, ?, ?, ?, 1, 'high', 'policy-v1', 1, 'discovery', 1)",
            (f"C-{suffix}", run_id, stage_id, subject_id),
        )
    )
    round_id = _lastrowid(
        transaction.execute(
            "INSERT INTO review_round(campaign_id, round_no, kind, opened_at) "
            "VALUES (?, 1, 'discovery', 1)",
            (campaign_id,),
        )
    )
    lane_id = _lastrowid(
        transaction.execute(
            "INSERT INTO review_lane(campaign_id, run_id, lane_index) VALUES (?, ?, 0)",
            (campaign_id, run_id),
        )
    )
    profile_id = f"profile-{suffix}"
    provider = f"provider-{suffix}"
    model = f"model-{suffix}"
    transaction.execute(
        "INSERT INTO run_profile_resolution"
        "(run_id, profile_id, provider, model, resolved_at) VALUES (?, ?, ?, ?, 1)",
        (run_id, profile_id, provider, model),
    )
    assignment_event_id = _append_event(transaction, run_id, "lane_assigned.v1", 2)
    assignment_id = _lastrowid(
        transaction.execute(
            "INSERT INTO lane_assignment"
            "(lane_id, run_id, generation, profile_id, event_id, assigned_at) "
            "VALUES (?, ?, 1, ?, ?, 2)",
            (lane_id, run_id, profile_id, assignment_event_id),
        )
    )
    attempt_id = _lastrowid(
        transaction.execute(
            "INSERT INTO step_attempt"
            "(public_id, run_id, stage_id, role, campaign_id, round_id, lane_id, "
            "lane_assignment_id, subject_revision, profile_id, requested_model, "
            "prompt_template_id, prompt_hash, input_refs_json, manifest_json, started_at) "
            "VALUES (?, ?, ?, 'reviewer', ?, ?, ?, ?, ?, ?, ?, 'review-v1', "
            "'prompt-hash', '[]', '{}', 3)",
            (
                f"A-{suffix}",
                run_id,
                stage_id,
                campaign_id,
                round_id,
                lane_id,
                assignment_id,
                f"revision-{suffix}",
                profile_id,
                model,
            ),
        )
    )
    return {
        "run": run_id,
        "branch": branch_id,
        "stage": stage_id,
        "subject": subject_id,
        "campaign": campaign_id,
        "round": round_id,
        "lane": lane_id,
        "profile": profile_id,
        "provider": provider,
        "model": model,
        "assignment": assignment_id,
        "assignment_event": assignment_event_id,
        "attempt": attempt_id,
        "revision": f"revision-{suffix}",
    }


def _create_finding(
    transaction: Transaction,
    fixture: dict[str, Any],
    suffix: str,
    *,
    seq: int,
) -> dict[str, int]:
    observation_id = _lastrowid(
        transaction.execute(
            "INSERT INTO review_observation"
            "(public_id, campaign_id, round_id, lane_id, attempt_id, subject_id, revision, "
            "seq, title, body, evidence, severity_suggested, severity_effective, dedup_key, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'body', 'evidence', 'high', "
            "'high', ?, 4)",
            (
                f"O-{suffix}",
                fixture["campaign"],
                fixture["round"],
                fixture["lane"],
                fixture["attempt"],
                fixture["subject"],
                fixture["revision"],
                seq,
                f"title-{suffix}",
                f"dedup-{suffix}",
            ),
        )
    )
    event_id = _append_event(transaction, fixture["run"], "finding_created.v1", 5 + seq)
    finding_id = _lastrowid(
        transaction.execute(
            "INSERT INTO finding"
            "(public_id, run_id, subject_id, first_campaign_id, first_round_id, "
            "first_observation_id, first_revision, first_owner_lane_id, title, event_id, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 5)",
            (
                f"F-{suffix}",
                fixture["run"],
                fixture["subject"],
                fixture["campaign"],
                fixture["round"],
                observation_id,
                fixture["revision"],
                fixture["lane"],
                f"title-{suffix}",
                event_id,
            ),
        )
    )
    return {"observation": observation_id, "event": event_id, "finding": finding_id}


def test_initial_migration_has_exact_inventory_seeds_and_readable_views(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = await Database.open(tmp_path / "state.sqlite3", core_version="core-1")
        try:
            def inspect(session):
                inventory = {
                    row[0]: row[1]
                    for row in session.fetch_all(
                        "SELECT type, COUNT(*) FROM sqlite_master "
                        "WHERE name NOT LIKE 'sqlite_%' GROUP BY type"
                    )
                }
                views = tuple(
                    row[0]
                    for row in session.fetch_all(
                        "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name"
                    )
                )
                for view in views:
                    session.fetch_all(f'SELECT * FROM "{view}" LIMIT 0')

                seeds = {
                    table: {row[0] for row in session.fetch_all(f"SELECT {column} FROM {table}")}
                    for table, (column, _expected) in SIMPLE_SEEDS.items()
                }
                return {
                    "inventory": inventory,
                    "views": views,
                    "fk": session.fetch_all("PRAGMA foreign_key_check"),
                    "migration": tuple(
                        session.fetch_one(
                            "SELECT version, core_version FROM schema_migration"
                        )
                    ),
                    "seeds": seeds,
                    "severity": {
                        tuple(row)
                        for row in session.fetch_all(
                            "SELECT severity, rank FROM severity_scale"
                        )
                    },
                    "resolution_kind": {
                        tuple(row)
                        for row in session.fetch_all(
                            "SELECT resolution, resolution_authority, closes_period "
                            "FROM resolution_kind"
                        )
                    },
                    "transitions": {
                        tuple(row)
                        for row in session.fetch_all(
                            "SELECT from_state, to_state FROM campaign_transition"
                        )
                    },
                    "resolution_columns": {
                        row[1]
                        for row in session.fetch_all(
                            "PRAGMA table_info('finding_resolution')"
                        )
                    },
                }

            result = await database.read(inspect)
            assert result["inventory"] == {"table": 65, "index": 33, "view": 7, "trigger": 47}
            assert len(result["views"]) == 7
            assert result["fk"] == ()
            assert result["migration"] == (1, "core-1")
            for table, (_column, expected) in SIMPLE_SEEDS.items():
                assert result["seeds"][table] == expected
            assert result["severity"] == {
                ("low", 10),
                ("medium", 20),
                ("high", 30),
                ("critical", 40),
            }
            assert result["resolution_kind"] == {
                ("verified_fixed", "reviewer", 1),
                ("accepted_reason", "reviewer", 0),
                ("policy_closed", "policy", 0),
                ("human_decision", "human", 1),
            }
            assert result["transitions"] == CAMPAIGN_TRANSITIONS
            assert "seq" not in result["resolution_columns"]
            assert len(SIMPLE_SEEDS) + 2 == 27
        finally:
            await database.close()

    asyncio.run(scenario())


def test_reopen_is_noop_and_rejects_newer_or_unversioned_database(tmp_path: Path) -> None:
    async def reopen_noop(path: Path) -> None:
        first = await Database.open(path, core_version="core-first")
        try:
            before = await first.read(
                lambda session: tuple(
                    session.fetch_one(
                        "SELECT version, applied_at, core_version FROM schema_migration"
                    )
                )
            )
        finally:
            await first.close()

        second = await Database.open(path, core_version="core-second")
        try:
            after = await second.read(
                lambda session: tuple(
                    session.fetch_one(
                        "SELECT version, applied_at, core_version FROM schema_migration"
                    )
                )
            )
        finally:
            await second.close()
        assert after == before

    asyncio.run(reopen_noop(tmp_path / "reopen.sqlite3"))

    newer = tmp_path / "newer.sqlite3"
    connection = sqlite3.connect(newer)
    connection.executescript(
        "CREATE TABLE schema_migration"
        "(version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL, core_version TEXT NOT NULL);"
        "CREATE TABLE preserved(value TEXT NOT NULL);"
        "INSERT INTO schema_migration VALUES (2, 1, 'future');"
        "INSERT INTO preserved VALUES ('keep');"
    )
    connection.close()

    with pytest.raises(IncompatibleSchemaError) as incompatible:
        asyncio.run(Database.open(newer, core_version="old-core"))
    assert (incompatible.value.found, incompatible.value.supported) == (2, 1)
    connection = sqlite3.connect(newer)
    assert connection.execute("SELECT value FROM preserved").fetchone() == ("keep",)
    connection.close()

    unversioned = tmp_path / "unversioned.sqlite3"
    connection = sqlite3.connect(unversioned)
    connection.executescript(
        "CREATE TABLE preserved(value TEXT NOT NULL); INSERT INTO preserved VALUES ('keep');"
    )
    connection.close()

    with pytest.raises(MigrationError, match="refusing implicit adoption"):
        asyncio.run(Database.open(unversioned, core_version="core"))
    connection = sqlite3.connect(unversioned)
    assert connection.execute("SELECT value FROM preserved").fetchone() == ("keep",)
    connection.close()


@pytest.mark.parametrize(
    "sql, expected_message",
    [
        (
            "CREATE TABLE schema_migration(version INTEGER PRIMARY KEY, applied_at INTEGER "
            "NOT NULL, core_version TEXT NOT NULL); CREATE TABLE partial(id INTEGER); BROKEN SQL;",
            "syntax error",
        ),
        (
            "CREATE TABLE schema_migration(version INTEGER PRIMARY KEY, applied_at INTEGER "
            "NOT NULL, core_version TEXT NOT NULL); "
            "CREATE TABLE parent(id INTEGER PRIMARY KEY); "
            "CREATE TABLE child(parent_id INTEGER REFERENCES parent(id) "
            "DEFERRABLE INITIALLY DEFERRED); INSERT INTO child VALUES (99);",
            "foreign_key_check failed",
        ),
    ],
)
def test_broken_migration_is_atomic_and_worker_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sql: str,
    expected_message: str,
) -> None:
    migration = db_module._Migration(1, "0001_broken.sql", sql)
    monkeypatch.setattr(db_module, "_discover_migrations", lambda: (migration,))
    path = tmp_path / "broken.sqlite3"
    before_threads = {thread.ident for thread in threading.enumerate()}

    with pytest.raises(MigrationError, match=expected_message) as captured:
        asyncio.run(Database.open(path, core_version="core"))
    assert captured.value.version == 1
    assert captured.value.name == "0001_broken.sql"
    assert {
        thread.ident
        for thread in threading.enumerate()
        if thread.name == "metaswarm-sqlite-writer"
    } <= before_threads

    connection = sqlite3.connect(path)
    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
    ).fetchall() == []
    connection.close()


def test_campaign_scope_exposure_and_cap_constraints(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = await Database.open(tmp_path / "state.sqlite3", core_version="core")
        try:
            fixture_a, fixture_b = await database.transaction(
                lambda transaction: (
                    _create_review_fixture(transaction, "A"),
                    _create_review_fixture(transaction, "B"),
                )
            )

            with pytest.raises(sqlite3.IntegrityError, match="illegal campaign state transition"):
                await database.transaction(
                    lambda transaction: transaction.execute(
                        "UPDATE review_campaign SET state='fix_cycle' WHERE id=?",
                        (fixture_a["campaign"],),
                    )
                )

            with pytest.raises(sqlite3.IntegrityError, match="lane_index outside declared quorum"):
                await database.transaction(
                    lambda transaction: transaction.execute(
                        "INSERT INTO review_lane(campaign_id, run_id, lane_index) VALUES (?, ?, 1)",
                        (fixture_a["campaign"], fixture_a["run"]),
                    )
                )

            def insert_cross_round_attempt(transaction: Transaction) -> None:
                transaction.execute(
                    "UPDATE step_attempt SET outcome='succeeded', finished_at=8 WHERE id=?",
                    (fixture_a["attempt"],),
                )
                transaction.execute(
                    "INSERT INTO step_attempt"
                    "(public_id, run_id, stage_id, role, campaign_id, round_id, lane_id, "
                    "lane_assignment_id, subject_revision, profile_id, requested_model, "
                    "prompt_template_id, prompt_hash, input_refs_json, manifest_json, started_at) "
                    "VALUES ('A-cross-round', ?, ?, 'reviewer', ?, ?, ?, ?, ?, ?, ?, "
                    "'review-v1', 'hash', '[]', '{}', 9)",
                    (
                        fixture_a["run"],
                        fixture_a["stage"],
                        fixture_a["campaign"],
                        fixture_b["round"],
                        fixture_a["lane"],
                        fixture_a["assignment"],
                        fixture_a["revision"],
                        fixture_a["profile"],
                        fixture_a["model"],
                    ),
                )

            with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
                await database.transaction(insert_cross_round_attempt)

            invalid_exposures = (
                (
                    fixture_a["revision"] + "-wrong",
                    fixture_a["provider"],
                    fixture_a["model"],
                    fixture_a["profile"],
                ),
                (
                    fixture_a["revision"],
                    fixture_a["provider"],
                    fixture_a["model"] + "-wrong",
                    fixture_a["profile"],
                ),
                (
                    fixture_a["revision"],
                    fixture_a["provider"],
                    fixture_a["model"],
                    fixture_b["profile"],
                ),
            )
            for revision, provider, model, profile in invalid_exposures:
                with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
                    await database.transaction(
                        lambda transaction,
                        revision=revision,
                        provider=provider,
                        model=model,
                        profile=profile: transaction.execute(
                            "INSERT INTO reviewer_exposure"
                            "(run_id, subject_id, revision, provider, model, campaign_id, "
                            "first_attempt_id, profile_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 9)",
                            (
                                fixture_a["run"],
                                fixture_a["subject"],
                                revision,
                                provider,
                                model,
                                fixture_a["campaign"],
                                fixture_a["attempt"],
                                profile,
                            ),
                        )
                    )

            await database.transaction(
                lambda transaction: transaction.execute(
                    "INSERT INTO reviewer_exposure"
                    "(run_id, subject_id, revision, provider, model, campaign_id, "
                    "first_attempt_id, profile_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 9)",
                    (
                        fixture_a["run"],
                        fixture_a["subject"],
                        fixture_a["revision"],
                        fixture_a["provider"],
                        fixture_a["model"],
                        fixture_a["campaign"],
                        fixture_a["attempt"],
                        fixture_a["profile"],
                    ),
                )
            )

            branch = await database.transaction(
                lambda transaction: _lastrowid(
                    transaction.execute(
                        "INSERT INTO branch(run_id, public_id, kind, state, created_at) "
                        "VALUES (?, 'B-cap', 'pipeline', 'ready', 1)",
                        (fixture_a["run"],),
                    )
                )
            )
            await database.transaction(
                lambda transaction: transaction.execute(
                    "INSERT INTO stage_execution"
                    "(run_id, branch_id, stage_key, ordinal, state, max_author_revisions, "
                    "severity_threshold) VALUES (?, ?, 'cap-ok', 1, 'running', 1, 'high')",
                    (fixture_a["run"], branch),
                )
            )
            with pytest.raises(sqlite3.IntegrityError, match="max_author_revisions"):
                await database.transaction(
                    lambda transaction: transaction.execute(
                        "INSERT INTO stage_execution"
                        "(run_id, branch_id, stage_key, ordinal, state, max_author_revisions, "
                        "severity_threshold) VALUES (?, ?, 'cap-zero', 1, 'running', 0, 'high')",
                        (fixture_a["run"], branch),
                    )
                )
        finally:
            await database.close()

    asyncio.run(scenario())


def test_override_uniqueness_append_only_and_canonical_resolution_order(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = await Database.open(tmp_path / "state.sqlite3", core_version="core")
        try:
            fixture, first, second = await database.transaction(
                lambda transaction: (
                    (fixture := _create_review_fixture(transaction, "override")),
                    _create_finding(transaction, fixture, "one", seq=1),
                    _create_finding(transaction, fixture, "two", seq=2),
                )
            )

            def create_ordered_events(transaction: Transaction) -> tuple[int, int, int]:
                event_40 = _lastrowid(
                    transaction.execute(
                        "INSERT INTO run_event(id, run_id, kind, payload, created_at) "
                        "VALUES (40, ?, 'resolution.v1', '{}', 40)",
                        (fixture["run"],),
                    )
                )
                event_35 = _lastrowid(
                    transaction.execute(
                        "INSERT INTO run_event(id, run_id, kind, payload, created_at) "
                        "VALUES (35, ?, 'resolution.v1', '{}', 35)",
                        (fixture["run"],),
                    )
                )
                event_50 = _lastrowid(
                    transaction.execute(
                        "INSERT INTO run_event(id, run_id, kind, payload, created_at) "
                        "VALUES (50, ?, 'override.v1', '{}', 50)",
                        (fixture["run"],),
                    )
                )
                return event_40, event_35, event_50

            event_40, event_35, event_50 = await database.transaction(create_ordered_events)

            def insert_resolutions(transaction: Transaction) -> None:
                for event_id, resolution in (
                    (event_40, "verified_fixed"),
                    (event_35, "policy_closed"),
                ):
                    authority = "reviewer" if resolution == "verified_fixed" else "policy"
                    closes = 1 if resolution == "verified_fixed" else 0
                    transaction.execute(
                        "INSERT INTO finding_resolution"
                        "(run_id, finding_id, resolution, resolution_authority, campaign_id, "
                        "closes_severity_period, event_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            fixture["run"],
                            first["finding"],
                            resolution,
                            authority,
                            fixture["campaign"],
                            closes,
                            event_id,
                            event_id,
                        ),
                    )

            await database.transaction(insert_resolutions)
            status = await database.read(
                lambda session: tuple(
                    session.fetch_one(
                        "SELECT status, last_resolution FROM finding_status WHERE finding_id=?",
                        (first["finding"],),
                    )
                )
            )
            assert status == ("closed", "verified_fixed")

            with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
                await database.transaction(
                    lambda transaction: transaction.execute(
                        "INSERT INTO finding_resolution"
                        "(run_id, finding_id, resolution, resolution_authority, campaign_id, "
                        "closes_severity_period, event_id, created_at) "
                        "VALUES (?, ?, 'verified_fixed', 'reviewer', ?, 1, ?, 41)",
                        (fixture["run"], first["finding"], fixture["campaign"], event_40),
                    )
                )

            def insert_overrides(transaction: Transaction) -> tuple[int, int, int]:
                first_override = _lastrowid(
                    transaction.execute(
                        "INSERT INTO severity_override"
                        "(finding_id, old_severity, new_severity, reason, event_id, created_at) "
                        "VALUES (?, 'high', 'medium', 'human', ?, 50)",
                        (first["finding"], event_50),
                    )
                )
                same_event_other_finding = _lastrowid(
                    transaction.execute(
                        "INSERT INTO severity_override"
                        "(finding_id, old_severity, new_severity, reason, event_id, created_at) "
                        "VALUES (?, 'high', 'low', 'human', ?, 50)",
                        (second["finding"], event_50),
                    )
                )
                later_event = _append_event(transaction, fixture["run"], "override.v1", 60)
                same_finding_other_event = _lastrowid(
                    transaction.execute(
                        "INSERT INTO severity_override"
                        "(finding_id, old_severity, new_severity, reason, event_id, created_at) "
                        "VALUES (?, 'medium', 'high', 'human', ?, 60)",
                        (first["finding"], later_event),
                    )
                )
                return first_override, same_event_other_finding, same_finding_other_event

            override_id, _other, _later = await database.transaction(insert_overrides)
            with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
                await database.transaction(
                    lambda transaction: transaction.execute(
                        "INSERT INTO severity_override"
                        "(finding_id, old_severity, new_severity, reason, event_id, created_at) "
                        "VALUES (?, 'high', 'critical', 'duplicate', ?, 51)",
                        (first["finding"], event_50),
                    )
                )

            mutations = (
                ("UPDATE severity_override SET reason='changed' WHERE id=?", (override_id,)),
                ("DELETE FROM severity_override WHERE id=?", (override_id,)),
                (
                    "INSERT OR REPLACE INTO severity_override"
                    "(id, finding_id, old_severity, new_severity, reason, event_id, created_at) "
                    "VALUES (?, ?, 'high', 'critical', 'replace', ?, 51)",
                    (override_id, first["finding"], event_50),
                ),
            )
            for sql, parameters in mutations:
                with pytest.raises(sqlite3.IntegrityError, match="severity_override is append-only"):
                    await database.transaction(
                        lambda transaction, sql=sql, parameters=parameters: transaction.execute(
                            sql, parameters
                        )
                    )

            original = await database.read(
                lambda session: tuple(
                    session.fetch_one(
                        "SELECT old_severity, new_severity, reason, event_id "
                        "FROM severity_override WHERE id=?",
                        (override_id,),
                    )
                )
            )
            assert original == ("high", "medium", "human", event_50)
        finally:
            await database.close()

    asyncio.run(scenario())


def _create_question_task_scope(
    transaction: Transaction,
    fixture: dict[str, Any],
    suffix: str,
) -> dict[str, int]:
    question_id = _lastrowid(
        transaction.execute(
            "INSERT INTO human_question"
            "(public_id, run_id, branch_id, stage_id, reason, question_text, asked_at) "
            "VALUES (?, ?, ?, ?, 'open_question', 'question', 1)",
            (f"Q-{suffix}", fixture["run"], fixture["branch"], fixture["stage"]),
        )
    )
    artifact_id = _lastrowid(
        transaction.execute(
            "INSERT INTO artifact_revision"
            "(run_id, kind, logical_path, revision_no, content_digest, code_sha, produced_by, "
            "manifest_json, created_at) VALUES (?, 'breakdown', ?, 1, 'digest', 'sha', "
            "'human', '{}', 1)",
            (fixture["run"], f"artifact-{suffix}"),
        )
    )
    import_event = _append_event(transaction, fixture["run"], "task_graph_imported.v1", 20)
    import_id = _lastrowid(
        transaction.execute(
            "INSERT INTO task_graph_import(run_id, source_artifact_revision, imported_at, event_id) "
            "VALUES (?, ?, 20, ?)",
            (fixture["run"], artifact_id, import_event),
        )
    )
    task_id = _lastrowid(
        transaction.execute(
            "INSERT INTO task"
            "(run_id, semantic_task_id, import_id, title, body, state, created_at) "
            "VALUES (?, ?, ?, 'task', 'body', 'pending', 20)",
            (fixture["run"], f"T-{suffix}", import_id),
        )
    )
    return {"question": question_id, "task": task_id, "event": import_event}


def test_run_state_and_all_blocker_scope_foreign_keys(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = await Database.open(tmp_path / "state.sqlite3", core_version="core")
        try:
            fixture_a, fixture_b, scope_a, scope_b = await database.transaction(
                lambda transaction: (
                    (a := _create_review_fixture(transaction, "scope-A")),
                    (b := _create_review_fixture(transaction, "scope-B")),
                    _create_question_task_scope(transaction, a, "scope-A"),
                    _create_question_task_scope(transaction, b, "scope-B"),
                )
            )

            cross_scope_rows = (
                {
                    "kind": "dependency",
                    "branch_id": fixture_b["branch"],
                    "created_event_id": scope_a["event"],
                },
                {
                    "kind": "dependency",
                    "task_id": scope_b["task"],
                    "created_event_id": scope_a["event"],
                },
                {
                    "kind": "dependency",
                    "stage_id": fixture_b["stage"],
                    "created_event_id": scope_a["event"],
                },
                {
                    "kind": "human_question",
                    "question_id": scope_b["question"],
                    "created_event_id": scope_a["event"],
                },
                {"kind": "dependency", "created_event_id": scope_b["event"]},
                {
                    "kind": "dependency",
                    "created_event_id": scope_a["event"],
                    "cleared_at": 30,
                    "cleared_event_id": scope_b["event"],
                },
            )
            for index, fields in enumerate(cross_scope_rows):
                optional_fields = {key: value for key, value in fields.items() if key != "kind"}
                columns = ["run_id", "kind", "created_at", *optional_fields]
                values = [
                    fixture_a["run"],
                    fields["kind"],
                    index + 1,
                    *optional_fields.values(),
                ]
                placeholders = ", ".join("?" for _ in columns)
                with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
                    await database.transaction(
                        lambda transaction,
                        columns=columns,
                        values=values,
                        placeholders=placeholders: transaction.execute(
                            f"INSERT INTO blocker({', '.join(columns)}) VALUES ({placeholders})",
                            values,
                        )
                    )
            assert await database.read(
                lambda session: session.fetch_one("SELECT COUNT(*) FROM blocker")[0]
            ) == 0

            def create_state_runs(transaction: Transaction) -> dict[str, int]:
                states: dict[str, int] = {}
                for name in (
                    "idle",
                    "human",
                    "continue",
                    "dependency",
                    "drift",
                    "invalid",
                    "mixed",
                    "active",
                    "paused",
                    "cancelling",
                    "terminal",
                ):
                    states[name] = _insert_run(transaction, f"state-{name}")
                for name, kind in (
                    ("continue", "awaiting_continue"),
                    ("dependency", "dependency"),
                    ("drift", "drift"),
                    ("invalid", "invalid_graph"),
                    ("mixed", "dependency"),
                ):
                    event = _append_event(transaction, states[name], "blocker_opened.v1", 40)
                    transaction.execute(
                        "INSERT INTO blocker(run_id, kind, created_at, created_event_id) "
                        "VALUES (?, ?, 40, ?)",
                        (states[name], kind, event),
                    )

                for name in ("human", "mixed"):
                    question = _lastrowid(
                        transaction.execute(
                            "INSERT INTO human_question"
                            "(public_id, run_id, reason, question_text, asked_at) "
                            "VALUES (?, ?, 'open_question', 'question', 40)",
                            (f"Q-state-{name}", states[name]),
                        )
                    )
                    event = _append_event(transaction, states[name], "human_gate_opened.v1", 40)
                    transaction.execute(
                        "INSERT INTO blocker"
                        "(run_id, kind, question_id, created_at, created_event_id) "
                        "VALUES (?, 'human_question', ?, 40, ?)",
                        (states[name], question, event),
                    )

                transaction.execute(
                    "INSERT INTO branch(run_id, public_id, kind, state, created_at) "
                    "VALUES (?, 'B-active', 'pipeline', 'running', 1)",
                    (states["active"],),
                )
                transaction.execute(
                    "UPDATE run SET pause_requested_at=1 WHERE id=?", (states["paused"],)
                )
                transaction.execute(
                    "UPDATE run SET cancel_requested_at=1 WHERE id=?", (states["cancelling"],)
                )
                transaction.execute(
                    "UPDATE run SET terminal_state='succeeded', finished_at=1 WHERE id=?",
                    (states["terminal"],),
                )
                return states

            states = await database.transaction(create_state_runs)
            actual = await database.read(
                lambda session: {
                    row[0]: row[1]
                    for row in session.fetch_all(
                        "SELECT r.public_id, s.state FROM run r JOIN run_state s ON s.run_id=r.id "
                        "WHERE r.public_id LIKE 'R-state-%'"
                    )
                }
            )
            expected = {
                "idle": "idle",
                "human": "waiting_human",
                "continue": "waiting_human",
                "dependency": "stalled",
                "drift": "stalled",
                "invalid": "stalled",
                "mixed": "waiting_human",
                "active": "running",
                "paused": "paused",
                "cancelling": "cancelling",
                "terminal": "succeeded",
            }
            assert actual == {f"R-state-{name}": state for name, state in expected.items()}
            assert len(states) == len(expected)
        finally:
            await database.close()

    asyncio.run(scenario())
