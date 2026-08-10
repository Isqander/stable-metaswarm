from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from metaswarm.store import (
    Database,
    DatabasePragmaError,
    StatementResult,
    StoreClosedError,
    Transaction,
    TransactionUsageError,
    WriteOutsideTransactionError,
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


def test_open_configures_pragmas_on_writer_thread_without_blocking_loop(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = await Database.open(tmp_path / "state.sqlite3", core_version="test-core")
        try:
            loop_thread = threading.get_ident()
            ticks = 0

            def inspect_connection(session):
                time.sleep(0.08)
                values = tuple(
                    session.fetch_one(f"PRAGMA {name}")[0]
                    for name in (
                        "journal_mode",
                        "foreign_keys",
                        "synchronous",
                        "busy_timeout",
                        "trusted_schema",
                        "recursive_triggers",
                    )
                )
                return threading.get_ident(), values

            job = asyncio.create_task(database.read(inspect_connection))
            while not job.done():
                ticks += 1
                await asyncio.sleep(0.005)
            worker_thread, values = await job

            assert worker_thread != loop_thread
            assert values == ("wal", 1, 2, 5000, 0, 1)
            assert ticks >= 3
        finally:
            await database.close()

    asyncio.run(scenario())


def test_memory_database_rejects_non_wal_readback_and_closes_worker() -> None:
    async def scenario() -> None:
        before = {thread.ident for thread in threading.enumerate()}
        with pytest.raises(DatabasePragmaError) as captured:
            await Database.open(":memory:", core_version="test-core")

        assert captured.value.pragma == "journal_mode"
        assert captured.value.expected == "wal"
        assert captured.value.actual == "memory"
        assert {
            thread.ident
            for thread in threading.enumerate()
            if thread.name == "metaswarm-sqlite-writer"
        } <= before

    asyncio.run(scenario())


def test_transaction_commits_or_rolls_back_as_one_unit(tmp_path: Path) -> None:
    class CallbackFailure(RuntimeError):
        pass

    async def scenario() -> None:
        database = await Database.open(tmp_path / "state.sqlite3", core_version="test-core")
        try:
            result = await database.transaction(
                lambda transaction: transaction.execute(
                    "INSERT INTO run(public_id, flow_id, flow_hash, project_config_hash, "
                    "profiles_config_hash, core_version, schema_version, instance_profile, "
                    "code_repo_path, code_sha, task_text, created_at) "
                    "VALUES ('R-commit', 'flow', 'f', 'p', 'profiles', 'core', 1, "
                    "'test', '/code', 'sha', 'task', 1)"
                )
            )
            assert isinstance(result, StatementResult)
            with pytest.raises(FrozenInstanceError):
                result.rowcount = 99  # type: ignore[misc]

            def fail_after_insert(transaction: Transaction) -> None:
                _insert_run(transaction, "R-rollback")
                raise CallbackFailure("original callback error")

            with pytest.raises(CallbackFailure, match="original callback error"):
                await database.transaction(fail_after_insert)

            rows = await database.read(
                lambda session: session.fetch_all("SELECT public_id FROM run ORDER BY id")
            )
            assert [row[0] for row in rows] == ["R-commit"]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_read_boundary_and_scoped_sessions_reject_write_or_reuse(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = await Database.open(tmp_path / "state.sqlite3", core_version="test-core")
        leaked = []
        try:
            def write_from_read(session) -> None:
                leaked.append(session)
                session.fetch_all("DELETE FROM run")

            with pytest.raises(WriteOutsideTransactionError):
                await database.read(write_from_read)

            await database.read(lambda session: leaked.append(session))
            await database.transaction(lambda transaction: leaked.append(transaction))

            for session in leaked:
                with pytest.raises(TransactionUsageError, match="no longer active"):
                    session.fetch_one("SELECT 1")

            assert await database.read(lambda session: session.fetch_one("SELECT 1")[0]) == 1
        finally:
            await database.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE forbidden(id INTEGER)",
        "PRAGMA foreign_keys = OFF",
        "PRAGMA optimize",
        "ATTACH DATABASE ':memory:' AS forbidden",
        "BEGIN",
        "SAVEPOINT forbidden",
    ],
)
def test_transaction_rejects_schema_connection_and_transaction_control(
    tmp_path: Path,
    sql: str,
) -> None:
    async def scenario() -> None:
        database = await Database.open(tmp_path / "state.sqlite3", core_version="test-core")
        try:
            with pytest.raises(TransactionUsageError):
                await database.transaction(lambda transaction: transaction.execute(sql))
            assert await database.transaction(
                lambda transaction: transaction.fetch_one("SELECT 1")[0]
            ) == 1
        finally:
            await database.close()

    asyncio.run(scenario())


def test_database_callbacks_must_be_synchronous(tmp_path: Path) -> None:
    async def invalid_callback(_transaction: Transaction) -> int:
        return 1

    async def scenario() -> None:
        database = await Database.open(tmp_path / "state.sqlite3", core_version="test-core")
        try:
            with pytest.raises(TransactionUsageError, match="synchronous"):
                await database.transaction(invalid_callback)
            assert await database.transaction(
                lambda transaction: transaction.fetch_one("SELECT 1")[0]
            ) == 1
        finally:
            await database.close()

    asyncio.run(scenario())


def test_concurrent_transactions_are_fifo_and_do_not_interleave(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = await Database.open(tmp_path / "state.sqlite3", core_version="test-core")
        first_started = threading.Event()
        second_started = threading.Event()
        release_first = threading.Event()
        order: list[str] = []
        try:
            def first(transaction: Transaction) -> None:
                order.append("first-start")
                first_started.set()
                assert release_first.wait(timeout=2)
                _insert_run(transaction, "R-first")
                order.append("first-end")

            def second(transaction: Transaction) -> None:
                order.append("second-start")
                second_started.set()
                _insert_run(transaction, "R-second")

            first_task = asyncio.create_task(database.transaction(first))
            assert await asyncio.to_thread(first_started.wait, 1)
            second_task = asyncio.create_task(database.transaction(second))
            await asyncio.sleep(0.05)
            assert not second_started.is_set()

            release_first.set()
            await asyncio.gather(first_task, second_task)
            assert order == ["first-start", "first-end", "second-start"]
        finally:
            release_first.set()
            await database.close()

    asyncio.run(scenario())


def test_close_drains_accepted_jobs_and_rejects_new_work(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = await Database.open(tmp_path / "state.sqlite3", core_version="test-core")
        started = threading.Event()
        release = threading.Event()
        accepted_ran = threading.Event()

        def blocking(transaction: Transaction) -> None:
            started.set()
            assert release.wait(timeout=2)
            _insert_run(transaction, "R-blocking")

        first = asyncio.create_task(database.transaction(blocking))
        assert await asyncio.to_thread(started.wait, 1)
        accepted = asyncio.create_task(
            database.read(lambda _session: accepted_ran.set())
        )
        await asyncio.sleep(0)
        closing = asyncio.create_task(database.close())
        await asyncio.sleep(0)

        with pytest.raises(StoreClosedError):
            await database.read(lambda session: session.fetch_one("SELECT 1"))

        release.set()
        await first
        await accepted
        await closing
        assert accepted_ran.is_set()
        assert not database._thread.is_alive()
        await database.close()

    asyncio.run(scenario())


def test_cancellation_before_start_skips_callback(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = await Database.open(tmp_path / "state.sqlite3", core_version="test-core")
        blocker_started = threading.Event()
        release = threading.Event()
        cancelled_callback_ran = False
        try:
            def blocking(_transaction: Transaction) -> None:
                blocker_started.set()
                assert release.wait(timeout=2)

            def should_not_run(_transaction: Transaction) -> None:
                nonlocal cancelled_callback_ran
                cancelled_callback_ran = True

            blocker = asyncio.create_task(database.transaction(blocking))
            assert await asyncio.to_thread(blocker_started.wait, 1)
            cancelled = asyncio.create_task(database.transaction(should_not_run))
            await asyncio.sleep(0)
            cancelled.cancel()
            await asyncio.sleep(0.02)
            assert not cancelled.done()

            release.set()
            await blocker
            with pytest.raises(asyncio.CancelledError):
                await cancelled
            assert not cancelled_callback_ran
        finally:
            release.set()
            await database.close()

    asyncio.run(scenario())


def test_cancellation_after_start_waits_for_commit_and_callback_error_wins(
    tmp_path: Path,
) -> None:
    class CallbackFailure(RuntimeError):
        pass

    async def scenario() -> None:
        database = await Database.open(tmp_path / "state.sqlite3", core_version="test-core")
        commit_started = threading.Event()
        release_commit = threading.Event()
        error_started = threading.Event()
        release_error = threading.Event()
        try:
            def commit_after_release(transaction: Transaction) -> None:
                _insert_run(transaction, "R-cancelled-caller")
                commit_started.set()
                assert release_commit.wait(timeout=2)

            committed = asyncio.create_task(database.transaction(commit_after_release))
            assert await asyncio.to_thread(commit_started.wait, 1)
            committed.cancel()
            await asyncio.sleep(0.02)
            assert not committed.done()
            release_commit.set()
            with pytest.raises(asyncio.CancelledError):
                await committed
            assert await database.read(
                lambda session: session.fetch_one(
                    "SELECT COUNT(*) FROM run WHERE public_id='R-cancelled-caller'"
                )[0]
            ) == 1

            def fail_after_release(_transaction: Transaction) -> None:
                error_started.set()
                assert release_error.wait(timeout=2)
                raise CallbackFailure("callback wins over cancellation")

            failed = asyncio.create_task(database.transaction(fail_after_release))
            assert await asyncio.to_thread(error_started.wait, 1)
            failed.cancel()
            release_error.set()
            with pytest.raises(CallbackFailure, match="wins over cancellation"):
                await failed
        finally:
            release_commit.set()
            release_error.set()
            await database.close()

    asyncio.run(scenario())
