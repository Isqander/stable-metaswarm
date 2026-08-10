from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass

from .db import EventPayloadError, StoreError, Transaction

type JsonScalar = bool | int | float | str | None
type JsonValue = JsonScalar | Mapping[str, JsonValue] | list[JsonValue] | tuple[JsonValue, ...]


def _validate_string(value: str, *, path: str) -> str:
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise EventPayloadError(f"{path} contains a Unicode surrogate code point") from error
    return value


def _normalize_json(value: object, *, path: str, active: set[int]) -> object:
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EventPayloadError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, str):
        return _validate_string(value, path=path)

    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise EventPayloadError(f"{path} contains a cycle")
        active.add(identity)
        try:
            normalized: dict[str, object] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise EventPayloadError(f"{path} contains a non-string mapping key")
                key = _validate_string(key, path=f"{path} key")
                normalized[key] = _normalize_json(item, path=f"{path}.{key}", active=active)
            return normalized
        except EventPayloadError:
            raise
        except Exception as error:
            raise EventPayloadError(f"{path} cannot be read as a JSON mapping") from error
        finally:
            active.remove(identity)

    if isinstance(value, list | tuple):
        identity = id(value)
        if identity in active:
            raise EventPayloadError(f"{path} contains a cycle")
        active.add(identity)
        try:
            return [
                _normalize_json(item, path=f"{path}[{index}]", active=active)
                for index, item in enumerate(value)
            ]
        finally:
            active.remove(identity)

    raise EventPayloadError(f"{path} contains unsupported value {type(value).__name__}")


@dataclass(frozen=True, slots=True, init=False)
class NewRunEvent:
    run_id: int
    kind: str
    payload_json: str
    created_at: int
    branch_id: int | None
    stage_id: int | None

    def __init__(
        self,
        *,
        run_id: int,
        kind: str,
        payload: Mapping[str, JsonValue],
        created_at: int,
        branch_id: int | None = None,
        stage_id: int | None = None,
    ) -> None:
        if not isinstance(payload, Mapping):
            raise EventPayloadError("event payload must be a mapping")
        normalized = _normalize_json(payload, path="payload", active=set())
        try:
            payload_json = json.dumps(
                normalized,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            payload_json.encode("utf-8", errors="strict")
        except (TypeError, ValueError, UnicodeEncodeError) as error:
            raise EventPayloadError("event payload cannot be encoded as canonical JSON") from error

        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "payload_json", payload_json)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "branch_id", branch_id)
        object.__setattr__(self, "stage_id", stage_id)


def append_run_event(transaction: Transaction, event: NewRunEvent) -> int:
    result = transaction.execute(
        "INSERT INTO run_event(run_id, branch_id, stage_id, kind, payload, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            event.run_id,
            event.branch_id,
            event.stage_id,
            event.kind,
            event.payload_json,
            event.created_at,
        ),
    )
    if result.lastrowid is None:
        raise StoreError("SQLite did not return an ID for appended run_event")
    return result.lastrowid
