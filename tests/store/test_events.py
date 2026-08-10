from __future__ import annotations

import asyncio
import json
import math
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from metaswarm.store import (
    Database,
    EventPayloadError,
    NewRunEvent,
    Transaction,
    append_run_event,
)


def _insert_run(transaction: Transaction, public_id: str, *, created_at: int = 1) -> int:
    result = transaction.execute(
        """
        INSERT INTO run(
          public_id, flow_id, flow_hash, project_config_hash, profiles_config_hash,
          core_version, schema_version, instance_profile, code_repo_path, code_sha,
          task_text, created_at
        ) VALUES (?, 'flow', 'flow-hash', 'project-hash', 'profiles-hash',
                  'test-core', 1, 'test', '/code', 'abc123', 'task', ?)
        """,
        (public_id, created_at),
    )
    assert result.lastrowid is not None
    return result.lastrowid


def test_state_and_event_commit_or_roll_back_together(tmp_path: Path) -> None:
    class CallbackFailure(RuntimeError):
        pass

    async def scenario() -> None:
        database = await Database.open(tmp_path / "state.sqlite3", core_version="test-core")
        try:
            def commit_pair(transaction: Transaction) -> int:
                run_id = _insert_run(transaction, "R-commit")
                return append_run_event(
                    transaction,
                    NewRunEvent(
                        run_id=run_id,
                        kind="run_created.v1",
                        payload={"public_id": "R-commit"},
                        created_at=10,
                    ),
                )

            event_id = await database.transaction(commit_pair)
            assert event_id > 0

            def roll_back_pair(transaction: Transaction) -> None:
                run_id = _insert_run(transaction, "R-rollback")
                append_run_event(
                    transaction,
                    NewRunEvent(
                        run_id=run_id,
                        kind="run_created.v1",
                        payload={"public_id": "R-rollback"},
                        created_at=20,
                    ),
                )
                raise CallbackFailure("rollback state and event")

            with pytest.raises(CallbackFailure):
                await database.transaction(roll_back_pair)

            counts = await database.read(
                lambda session: (
                    session.fetch_one("SELECT COUNT(*) FROM run")[0],
                    session.fetch_one("SELECT COUNT(*) FROM run_event")[0],
                )
            )
            assert counts == (1, 1)
        finally:
            await database.close()

    asyncio.run(scenario())


def test_event_payload_is_canonical_deep_snapshot_and_ids_are_monotonic(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = await Database.open(tmp_path / "state.sqlite3", core_version="test-core")
        try:
            run_id = await database.transaction(lambda transaction: _insert_run(transaction, "R-1"))
            nested_list = [3, {"b": 2, "a": 1}]
            payload = {"z": nested_list, "a": (True, None, "😀")}
            first = NewRunEvent(
                run_id=run_id,
                kind="snapshot.v1",
                payload=payload,
                created_at=10,
            )
            nested_list.append("mutated")
            payload["late"] = "not captured"

            first_id = await database.transaction(
                lambda transaction: append_run_event(transaction, first)
            )
            second_id = await database.transaction(
                lambda transaction: append_run_event(
                    transaction,
                    NewRunEvent(
                        run_id=run_id,
                        kind="snapshot.v1",
                        payload={"n": 2},
                        created_at=11,
                    ),
                )
            )

            assert second_id > first_id
            row = await database.read(
                lambda session: session.fetch_one(
                    "SELECT payload FROM run_event WHERE id = ?", (first_id,)
                )
            )
            assert row is not None
            assert row[0] == '{"a":[true,null,"😀"],"z":[3,{"a":1,"b":2}]}'
            assert json.loads(row[0]) == {
                "a": [True, None, "😀"],
                "z": [3, {"a": 1, "b": 2}],
            }
        finally:
            await database.close()

    asyncio.run(scenario())


def _root_integer_key() -> object:
    return {1: "value"}


def _nested_integer_key() -> object:
    return {"nested": {1: "value"}}


def _high_surrogate_value() -> object:
    return {"value": "\ud800"}


def _low_surrogate_key() -> object:
    return {"\udfff": "value"}


def _set_value() -> object:
    return {"value": {1, 2}}


def _bytes_value() -> object:
    return {"value": b"bytes"}


def _foreign_object() -> object:
    return {"value": object()}


def _list_cycle() -> object:
    cycle: list[object] = []
    cycle.append(cycle)
    return {"cycle": cycle}


def _mapping_cycle() -> object:
    cycle: dict[str, object] = {}
    cycle["self"] = cycle
    return cycle


def _nan_value() -> object:
    return {"value": math.nan}


def _infinity_value() -> object:
    return {"value": math.inf}


@pytest.mark.parametrize(
    "payload_factory",
    [
        lambda: [1, 2],
        _root_integer_key,
        _nested_integer_key,
        _high_surrogate_value,
        _low_surrogate_key,
        _set_value,
        _bytes_value,
        _foreign_object,
        _list_cycle,
        _mapping_cycle,
        _nan_value,
        _infinity_value,
    ],
)
def test_invalid_recursive_json_payload_is_rejected_before_insert(
    payload_factory: Callable[[], object],
) -> None:
    with pytest.raises(EventPayloadError):
        NewRunEvent(
            run_id=1,
            kind="invalid.v1",
            payload=payload_factory(),  # type: ignore[arg-type]
            created_at=1,
        )


@pytest.mark.parametrize("kind", ["invalid-\ud800.v1", "invalid-\udfff.v1"])
def test_event_kind_rejects_lone_surrogate_as_payload_error(kind: str) -> None:
    with pytest.raises(EventPayloadError, match="kind contains a Unicode surrogate"):
        NewRunEvent(run_id=1, kind=kind, payload={}, created_at=1)


def test_invalid_payload_inside_transaction_rolls_back_prior_state(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = await Database.open(tmp_path / "state.sqlite3", core_version="test-core")
        try:
            def invalid_after_state(transaction: Transaction) -> None:
                run_id = _insert_run(transaction, "R-invalid-event")
                NewRunEvent(
                    run_id=run_id,
                    kind="invalid.v1",
                    payload={"nested": {1: "not-json"}},  # type: ignore[dict-item]
                    created_at=1,
                )

            with pytest.raises(EventPayloadError):
                await database.transaction(invalid_after_state)
            assert await database.read(
                lambda session: session.fetch_one(
                    "SELECT COUNT(*) FROM run WHERE public_id='R-invalid-event'"
                )[0]
            ) == 0
        finally:
            await database.close()

    asyncio.run(scenario())


def test_run_event_is_append_only_including_insert_or_replace(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = await Database.open(tmp_path / "state.sqlite3", core_version="test-core")
        try:
            run_id = await database.transaction(lambda transaction: _insert_run(transaction, "R-1"))
            event_id = await database.transaction(
                lambda transaction: append_run_event(
                    transaction,
                    NewRunEvent(
                        run_id=run_id,
                        kind="immutable.v1",
                        payload={"original": True},
                        created_at=1,
                    ),
                )
            )

            statements = (
                ("UPDATE run_event SET payload='{}' WHERE id=?", (event_id,)),
                ("DELETE FROM run_event WHERE id=?", (event_id,)),
                (
                    "INSERT OR REPLACE INTO run_event"
                    "(id, run_id, kind, payload, created_at) VALUES (?, ?, 'replacement.v1', '{}', 2)",
                    (event_id, run_id),
                ),
            )
            for sql, parameters in statements:
                with pytest.raises(sqlite3.IntegrityError, match="run_event is append-only"):
                    await database.transaction(
                        lambda transaction, sql=sql, parameters=parameters: transaction.execute(
                            sql, parameters
                        )
                    )

            row = await database.read(
                lambda session: session.fetch_one(
                    "SELECT kind, payload, created_at FROM run_event WHERE id=?", (event_id,)
                )
            )
            assert row is not None
            assert tuple(row) == ("immutable.v1", '{"original":true}', 1)
        finally:
            await database.close()

    asyncio.run(scenario())
