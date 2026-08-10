from __future__ import annotations

import sqlite3
from dataclasses import fields
from typing import Any

from ..db import ReadSession, StoreError, Transaction

type ReadContext = ReadSession | Transaction


class RepositoryRecordNotFound(StoreError):
    """A required repository row does not exist."""

    def __init__(self, entity: str, id: int) -> None:
        self.entity = entity
        self.id = id
        super().__init__(f"{entity} {id} does not exist")


class RepositoryAlreadyTerminal(StoreError):
    """A compare-and-set tried to finish an already terminal row."""

    def __init__(self, entity: str, id: int, terminal_value: str) -> None:
        self.entity = entity
        self.id = id
        self.terminal_value = terminal_value
        super().__init__(f"{entity} {id} is already terminal: {terminal_value}")


class ReviewerExposureConflict(StoreError):
    """A reviewer pair is already reserved by another quorum lane."""

    def __init__(self, attempt_id: int, first_attempt_id: int) -> None:
        self.attempt_id = attempt_id
        self.first_attempt_id = first_attempt_id
        super().__init__(
            f"attempt {attempt_id} conflicts with reviewer exposure from "
            f"attempt {first_attempt_id}"
        )


def map_row[T](record_type: type[T], row: sqlite3.Row) -> T:
    return record_type(**{field.name: row[field.name] for field in fields(record_type)})


def map_optional[T](record_type: type[T], row: sqlite3.Row | None) -> T | None:
    return None if row is None else map_row(record_type, row)


def map_rows[T](record_type: type[T], rows: tuple[sqlite3.Row, ...]) -> tuple[T, ...]:
    return tuple(map_row(record_type, row) for row in rows)


def insert_record[T](
    tx: Transaction,
    table: str,
    value: object,
    record_type: type[T],
) -> T:
    value_fields = fields(value)
    columns = tuple(field.name for field in value_fields)
    placeholders = ", ".join("?" for _ in columns)
    result = tx.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        tuple(getattr(value, column) for column in columns),
    )
    if result.lastrowid is None:
        raise StoreError(f"SQLite did not return an ID for {table}")
    row = tx.fetch_one(f"SELECT * FROM {table} WHERE id = ?", (result.lastrowid,))
    if row is None:
        raise RepositoryRecordNotFound(table, result.lastrowid)
    return map_row(record_type, row)


def record_from_value[T](record_type: type[T], value: object) -> T:
    return record_type(**{field.name: getattr(value, field.name) for field in fields(value)})


def require_by_id(
    db: ReadContext,
    table: str,
    id: int,
) -> sqlite3.Row:
    row = db.fetch_one(f"SELECT * FROM {table} WHERE id = ?", (id,))
    if row is None:
        raise RepositoryRecordNotFound(table, id)
    return row


def next_autoincrement_id(tx: Transaction, table: str) -> int:
    row = tx.fetch_one(
        "SELECT MAX(candidate) AS next_id FROM ("
        "SELECT COALESCE((SELECT seq FROM sqlite_sequence WHERE name = ?), 0) + 1 "
        "AS candidate UNION ALL "
        f"SELECT COALESCE(MAX(id), 0) + 1 FROM {table})",
        (table,),
    )
    assert row is not None
    return int(row["next_id"])


def repository_precondition(message: str) -> sqlite3.IntegrityError:
    return sqlite3.IntegrityError(message)


def dataclass_values(value: object) -> tuple[Any, ...]:
    return tuple(getattr(value, field.name) for field in fields(value))
