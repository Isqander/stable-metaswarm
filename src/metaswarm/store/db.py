from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import queue
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, cast

type SQLiteValue = str | int | float | bytes | None
type SQLParameters = Sequence[SQLiteValue] | Mapping[str, SQLiteValue]

SUPPORTED_SCHEMA_VERSION = 1
_MIGRATION_NAME = re.compile(r"^(?P<version>[0-9]{4})_[a-z0-9_]+\.sql$")
_EMPTY_PARAMETERS: tuple[()] = ()


class StoreError(Exception):
    """Base class for store lifecycle and policy errors."""


class StoreClosedError(StoreError):
    """The database no longer accepts work."""


class DatabasePragmaError(StoreError):
    """SQLite did not accept a mandatory connection policy."""

    def __init__(self, pragma: str, expected: object, actual: object) -> None:
        self.pragma = pragma
        self.expected = expected
        self.actual = actual
        super().__init__(f"PRAGMA {pragma}: expected {expected!r}, got {actual!r}")


class WriteOutsideTransactionError(StoreError):
    """A read callback attempted to change persistent or connection state."""


class TransactionUsageError(StoreError):
    """A transaction callback violated the scoped synchronous API."""


class MigrationError(StoreError):
    """A numbered migration could not be validated or applied atomically."""

    def __init__(self, message: str, *, version: int | None = None, name: str | None = None) -> None:
        self.version = version
        self.name = name
        prefix = "migration"
        if version is not None or name is not None:
            prefix += f" {version if version is not None else '?'} ({name or '?'})"
        super().__init__(f"{prefix}: {message}")


class IncompatibleSchemaError(StoreError):
    """The database schema is newer than this core understands."""

    def __init__(self, found: int, supported: int) -> None:
        self.found = found
        self.supported = supported
        super().__init__(f"database schema version {found} is newer than supported {supported}")


class EventPayloadError(StoreError):
    """An event payload cannot be represented by the canonical JSON contract."""


@dataclass(frozen=True, slots=True)
class StatementResult:
    lastrowid: int | None
    rowcount: int


_DDL_ACTIONS = frozenset(
    action
    for name in (
        "SQLITE_ALTER_TABLE",
        "SQLITE_ANALYZE",
        "SQLITE_CREATE_INDEX",
        "SQLITE_CREATE_TABLE",
        "SQLITE_CREATE_TEMP_INDEX",
        "SQLITE_CREATE_TEMP_TABLE",
        "SQLITE_CREATE_TEMP_TRIGGER",
        "SQLITE_CREATE_TEMP_VIEW",
        "SQLITE_CREATE_TRIGGER",
        "SQLITE_CREATE_VIEW",
        "SQLITE_CREATE_VTABLE",
        "SQLITE_DROP_INDEX",
        "SQLITE_DROP_TABLE",
        "SQLITE_DROP_TEMP_INDEX",
        "SQLITE_DROP_TEMP_TABLE",
        "SQLITE_DROP_TEMP_TRIGGER",
        "SQLITE_DROP_TEMP_VIEW",
        "SQLITE_DROP_TRIGGER",
        "SQLITE_DROP_VIEW",
        "SQLITE_DROP_VTABLE",
        "SQLITE_REINDEX",
    )
    if (action := getattr(sqlite3, name, None)) is not None
)
_CONNECTION_ACTIONS = frozenset(
    action
    for name in ("SQLITE_ATTACH", "SQLITE_DETACH", "SQLITE_SAVEPOINT", "SQLITE_TRANSACTION")
    if (action := getattr(sqlite3, name, None)) is not None
)
_WRITE_ACTIONS = frozenset(
    action
    for name in ("SQLITE_DELETE", "SQLITE_INSERT", "SQLITE_UPDATE")
    if (action := getattr(sqlite3, name, None)) is not None
)
_READ_ONLY_PRAGMAS = frozenset(
    {
        "busy_timeout",
        "foreign_keys",
        "journal_mode",
        "recursive_triggers",
        "synchronous",
        "trusted_schema",
    }
)
_READ_ONLY_PRAGMAS_WITH_ARGUMENT = frozenset(
    {
        "foreign_key_check",
        "foreign_key_list",
        "index_info",
        "index_list",
        "index_xinfo",
        "integrity_check",
        "quick_check",
        "table_info",
        "table_xinfo",
    }
)


class _ScopedSession:
    def __init__(
        self,
        connection: sqlite3.Connection,
        denied_actions: frozenset[int],
        policy_error: type[StoreError],
    ) -> None:
        self._connection = connection
        self._denied_actions = denied_actions
        self._policy_error = policy_error
        self._owner_thread = threading.get_ident()
        self._active = True
        self._denied = False

    def _check_active(self) -> None:
        if not self._active or threading.get_ident() != self._owner_thread:
            raise TransactionUsageError("scoped database session is no longer active")

    def _deactivate(self) -> None:
        self._active = False

    def _authorizer(
        self,
        action: int,
        arg1: str | None,
        arg2: str | None,
        _database: str | None,
        _source: str | None,
    ) -> int:
        pragma_action = getattr(sqlite3, "SQLITE_PRAGMA", -1)
        pragma = (arg1 or "").lower()
        forbidden_pragma = action == pragma_action and not (
            (pragma in _READ_ONLY_PRAGMAS and arg2 is None)
            or pragma in _READ_ONLY_PRAGMAS_WITH_ARGUMENT
        )
        if action in self._denied_actions or forbidden_pragma:
            self._denied = True
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def _translate_policy_error(self, error: sqlite3.DatabaseError) -> None:
        if self._denied:
            self._denied = False
            raise self._policy_error("SQL operation is forbidden in this scoped session") from error

    def _execute_cursor(
        self,
        sql: str,
        parameters: SQLParameters,
    ) -> sqlite3.Cursor:
        self._check_active()
        self._denied = False
        try:
            return self._connection.execute(sql, parameters)
        except sqlite3.DatabaseError as error:
            self._translate_policy_error(error)
            raise

    def fetch_one(
        self,
        sql: str,
        parameters: SQLParameters = _EMPTY_PARAMETERS,
    ) -> sqlite3.Row | None:
        cursor = self._execute_cursor(sql, parameters)
        try:
            return cursor.fetchone()
        finally:
            cursor.close()

    def fetch_all(
        self,
        sql: str,
        parameters: SQLParameters = _EMPTY_PARAMETERS,
    ) -> tuple[sqlite3.Row, ...]:
        cursor = self._execute_cursor(sql, parameters)
        try:
            return tuple(cursor.fetchall())
        finally:
            cursor.close()


class ReadSession(_ScopedSession):
    """A read-only session valid only during one synchronous read callback."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        super().__init__(
            connection,
            _DDL_ACTIONS | _CONNECTION_ACTIONS | _WRITE_ACTIONS,
            WriteOutsideTransactionError,
        )


class Transaction(_ScopedSession):
    """A write session valid only during one synchronous transaction callback."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        super().__init__(
            connection,
            _DDL_ACTIONS | _CONNECTION_ACTIONS,
            TransactionUsageError,
        )

    def execute(
        self,
        sql: str,
        parameters: SQLParameters = _EMPTY_PARAMETERS,
    ) -> StatementResult:
        cursor = self._execute_cursor(sql, parameters)
        try:
            return StatementResult(lastrowid=cursor.lastrowid, rowcount=cursor.rowcount)
        finally:
            cursor.close()

    def executemany(
        self,
        sql: str,
        parameter_rows: Iterable[SQLParameters],
    ) -> StatementResult:
        self._check_active()
        self._denied = False
        try:
            cursor = self._connection.executemany(sql, parameter_rows)
        except sqlite3.DatabaseError as error:
            self._translate_policy_error(error)
            raise
        try:
            return StatementResult(lastrowid=cursor.lastrowid, rowcount=cursor.rowcount)
        finally:
            cursor.close()


@dataclass(frozen=True, slots=True)
class _Migration:
    version: int
    name: str
    sql: str


def _discover_migrations() -> tuple[_Migration, ...]:
    package = resources.files("metaswarm.store.migrations")
    migrations: list[_Migration] = []
    seen_versions: set[int] = set()
    for resource in sorted(package.iterdir(), key=lambda item: item.name):
        if not resource.name.endswith(".sql"):
            continue
        match = _MIGRATION_NAME.fullmatch(resource.name)
        if match is None:
            raise MigrationError(f"invalid migration resource name {resource.name!r}")
        version = int(match.group("version"))
        if version in seen_versions:
            raise MigrationError(f"duplicate migration version {version}")
        seen_versions.add(version)
        migrations.append(
            _Migration(version=version, name=resource.name, sql=resource.read_text(encoding="utf-8"))
        )

    actual = [migration.version for migration in migrations]
    expected = list(range(1, len(migrations) + 1))
    if actual != expected:
        raise MigrationError(f"migration versions must be continuous from 1: {actual!r}")
    if not migrations or migrations[-1].version != SUPPORTED_SCHEMA_VERSION:
        raise MigrationError(
            "migration resources do not match supported schema version "
            f"{SUPPORTED_SCHEMA_VERSION}: {actual!r}"
        )
    return tuple(migrations)


def _split_sql(sql: str, *, migration: _Migration) -> tuple[str, ...]:
    statements: list[str] = []
    start = 0
    for index, character in enumerate(sql):
        if character == ";" and sqlite3.complete_statement(sql[start : index + 1]):
            statement = sql[start : index + 1].strip()
            if statement:
                statements.append(statement)
            start = index + 1
    if sql[start:].strip():
        raise MigrationError(
            "incomplete SQL statement",
            version=migration.version,
            name=migration.name,
        )
    return tuple(statements)


def _user_objects(connection: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    )


def _apply_migration(
    connection: sqlite3.Connection,
    migration: _Migration,
    *,
    core_version: str,
) -> None:
    try:
        statements = _split_sql(migration.sql, migration=migration)
        connection.execute("BEGIN IMMEDIATE")
        for statement in statements:
            connection.execute(statement)
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise MigrationError(
                f"foreign_key_check failed: {[tuple(row) for row in foreign_key_errors]!r}",
                version=migration.version,
                name=migration.name,
            )
        connection.execute(
            "INSERT INTO schema_migration(version, applied_at, core_version) VALUES (?, ?, ?)",
            (migration.version, time.time_ns() // 1_000_000, core_version),
        )
        connection.execute("COMMIT")
    except BaseException as error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        if isinstance(error, MigrationError):
            raise
        raise MigrationError(
            str(error),
            version=migration.version,
            name=migration.name,
        ) from error


def _migrate(connection: sqlite3.Connection, *, core_version: str) -> None:
    migrations = _discover_migrations()
    objects = _user_objects(connection)
    has_registry = "schema_migration" in objects
    if not has_registry and objects:
        raise MigrationError(
            "non-empty database has no schema_migration table; refusing implicit adoption"
        )

    applied: tuple[int, ...] = ()
    if has_registry:
        try:
            applied = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT version FROM schema_migration ORDER BY version"
                )
            )
        except sqlite3.DatabaseError as error:
            raise MigrationError("cannot read schema_migration") from error

    if applied and applied[-1] > SUPPORTED_SCHEMA_VERSION:
        raise IncompatibleSchemaError(applied[-1], SUPPORTED_SCHEMA_VERSION)
    if applied != tuple(range(1, len(applied) + 1)):
        raise MigrationError(f"applied migration versions are not continuous: {applied!r}")

    current = applied[-1] if applied else 0
    for migration in migrations:
        if migration.version > current:
            _apply_migration(connection, migration, core_version=core_version)


_EXPECTED_PRAGMAS: tuple[tuple[str, object], ...] = (
    ("journal_mode", "wal"),
    ("foreign_keys", 1),
    ("synchronous", 2),
    ("busy_timeout", 5000),
    ("trusted_schema", 0),
    ("recursive_triggers", 1),
)


def _configure_connection(connection: sqlite3.Connection) -> None:
    for pragma, expected in _EXPECTED_PRAGMAS:
        connection.execute(f"PRAGMA {pragma} = {expected}")
        actual = connection.execute(f"PRAGMA {pragma}").fetchone()[0]
        if isinstance(expected, str):
            actual = str(actual).lower()
        if actual != expected:
            raise DatabasePragmaError(pragma, expected, actual)


def _close_awaitable(value: object) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        close()


def _run_read[T](connection: sqlite3.Connection, callback: Callable[[ReadSession], T]) -> T:
    session = ReadSession(connection)
    connection.execute("PRAGMA query_only = ON")
    connection.set_authorizer(session._authorizer)
    try:
        result = callback(session)
        if inspect.isawaitable(result):
            _close_awaitable(result)
            raise TransactionUsageError("database callbacks must be synchronous")
        return result
    finally:
        session._deactivate()
        connection.set_authorizer(None)
        connection.execute("PRAGMA query_only = OFF")


def _run_transaction[T](connection: sqlite3.Connection, callback: Callable[[Transaction], T]) -> T:
    connection.execute("BEGIN IMMEDIATE")
    session = Transaction(connection)
    connection.set_authorizer(session._authorizer)
    try:
        result = callback(session)
        if inspect.isawaitable(result):
            _close_awaitable(result)
            raise TransactionUsageError("database callbacks must be synchronous")
        session._deactivate()
        connection.set_authorizer(None)
        connection.execute("COMMIT")
        return result
    except BaseException:
        session._deactivate()
        connection.set_authorizer(None)
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


class _SkippedJob:
    pass


_SKIPPED = _SkippedJob()


@dataclass(slots=True)
class _Job[T]:
    callback: Callable[[sqlite3.Connection], T]
    future: concurrent.futures.Future[T | _SkippedJob] = field(
        default_factory=concurrent.futures.Future
    )
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _started: bool = False
    _cancel_requested: bool = False

    def request_cancel(self) -> None:
        with self._lock:
            self._cancel_requested = True

    def try_start(self) -> bool:
        with self._lock:
            if self._cancel_requested:
                return False
            self._started = True
            return True


class _Stop:
    pass


_STOP = _Stop()


class Database:
    """One SQLite connection owned by one dedicated FIFO worker thread."""

    def __init__(self, path: str | Path, *, core_version: str) -> None:
        self._path = str(path)
        self._core_version = core_version
        self._queue: queue.Queue[_Job[Any] | _Stop] = queue.Queue()
        self._opened: concurrent.futures.Future[None] = concurrent.futures.Future()
        self._closed: concurrent.futures.Future[None] = concurrent.futures.Future()
        self._state_lock = threading.Lock()
        self._state = "opening"
        self._thread = threading.Thread(
            target=self._worker_main,
            name="metaswarm-sqlite-writer",
            daemon=False,
        )

    @classmethod
    async def open(cls, path: str | Path, *, core_version: str) -> Database:
        database = cls(path, core_version=core_version)
        database._thread.start()
        cancelled: asyncio.CancelledError | None = None
        opened = asyncio.wrap_future(database._opened)
        while True:
            try:
                await asyncio.shield(opened)
                break
            except asyncio.CancelledError as error:
                cancelled = error
                continue
            except BaseException:
                await asyncio.to_thread(database._thread.join)
                raise

        with database._state_lock:
            database._state = "open"
        if cancelled is not None:
            await database.close()
            raise cancelled
        return database

    async def read[T](self, callback: Callable[[ReadSession], T]) -> T:
        job = self._submit(lambda connection: _run_read(connection, callback))
        return await self._await_job(job)

    async def transaction[T](self, callback: Callable[[Transaction], T]) -> T:
        job = self._submit(lambda connection: _run_transaction(connection, callback))
        return await self._await_job(job)

    async def close(self) -> None:
        with self._state_lock:
            if self._state == "closed":
                return
            if self._state == "open":
                self._state = "closing"
                self._queue.put(_STOP)
            closed = self._closed

        cancelled: asyncio.CancelledError | None = None
        wrapped = asyncio.wrap_future(closed)
        while True:
            try:
                await asyncio.shield(wrapped)
                break
            except asyncio.CancelledError as error:
                cancelled = error
                continue
        await asyncio.to_thread(self._thread.join)
        with self._state_lock:
            self._state = "closed"
        if cancelled is not None:
            raise cancelled

    def _submit[T](self, callback: Callable[[sqlite3.Connection], T]) -> _Job[T]:
        job = _Job(callback)
        with self._state_lock:
            if self._state != "open":
                raise StoreClosedError("database is closing or closed")
            self._queue.put(job)
        return job

    async def _await_job[T](self, job: _Job[T]) -> T:
        wrapped = asyncio.wrap_future(job.future)
        cancelled: asyncio.CancelledError | None = None
        while True:
            try:
                result = await asyncio.shield(wrapped)
                break
            except asyncio.CancelledError as error:
                if wrapped.done():
                    try:
                        wrapped.result()
                    except BaseException:
                        raise
                    raise error
                cancelled = error
                job.request_cancel()
                continue
        if cancelled is not None or result is _SKIPPED:
            raise cancelled or asyncio.CancelledError()
        return cast(T, result)

    def _worker_main(self) -> None:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self._path, isolation_level=None)
            connection.row_factory = sqlite3.Row
            _configure_connection(connection)
            _migrate(connection, core_version=self._core_version)
        except BaseException as error:
            if connection is not None:
                connection.close()
            self._opened.set_exception(error)
            self._closed.set_result(None)
            return

        self._opened.set_result(None)
        while True:
            item = self._queue.get()
            if item is _STOP:
                break
            job = cast(_Job[Any], item)
            if not job.try_start():
                job.future.set_result(_SKIPPED)
                continue
            try:
                job.future.set_result(job.callback(connection))
            except BaseException as error:
                job.future.set_exception(error)

        try:
            connection.close()
        except BaseException as error:
            self._closed.set_exception(error)
        else:
            self._closed.set_result(None)
