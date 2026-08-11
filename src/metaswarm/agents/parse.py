from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import NoReturn

RESULT_BEGIN = "<<<METASWARM-RESULT-BEGIN>>>"
RESULT_END = "<<<METASWARM-RESULT-END>>>"
MAX_RESULT_BYTES = 1_048_576


@dataclass(frozen=True, slots=True)
class PayloadParseError(ValueError):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


class _DuplicateKeyError(ValueError):
    pass


class _NonFiniteNumberError(ValueError):
    pass


def _reject_constant(_value: str) -> NoReturn:
    raise _NonFiniteNumberError


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise _NonFiniteNumberError
    return parsed


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError
        result[key] = value
    return result


def _validate_unicode(value: object) -> None:
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError as error:
                raise PayloadParseError(
                    "invalid_unicode",
                    "JSON strings and keys must be valid UTF-8",
                ) from error
        elif isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, dict):
            for key, nested in item.items():
                pending.append(key)
                pending.append(nested)


def extract_marked_payload(text: str) -> dict[str, object]:
    if not isinstance(text, str):
        raise PayloadParseError("invalid_text", "agent result must be text")
    if text.count(RESULT_BEGIN) != 1 or text.count(RESULT_END) != 1:
        raise PayloadParseError(
            "invalid_markers",
            "result markers must each occur exactly once",
        )
    begin = text.index(RESULT_BEGIN) + len(RESULT_BEGIN)
    end = text.index(RESULT_END)
    if end < begin:
        raise PayloadParseError("invalid_markers", "result markers are out of order")

    payload_text = text[begin:end]
    try:
        payload_bytes = payload_text.encode("utf-8")
    except UnicodeEncodeError as error:
        raise PayloadParseError(
            "invalid_unicode",
            "marked payload must be valid UTF-8",
        ) from error
    if len(payload_bytes) > MAX_RESULT_BYTES:
        raise PayloadParseError("result_too_large", "marked payload exceeds 1 MiB")
    try:
        parsed = json.loads(
            payload_text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except _DuplicateKeyError as error:
        raise PayloadParseError("duplicate_key", "JSON object keys must be unique") from error
    except _NonFiniteNumberError as error:
        raise PayloadParseError("non_finite_number", "JSON numbers must be finite") from error
    except (json.JSONDecodeError, RecursionError, ValueError) as error:
        raise PayloadParseError("invalid_json", "marked payload must be one JSON value") from error

    _validate_unicode(parsed)
    if not isinstance(parsed, dict):
        raise PayloadParseError("top_level_object", "top-level JSON value must be an object")
    return parsed


def canonical_json_bytes(payload: object) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise PayloadParseError(
            "canonical_json",
            "validated payload cannot be serialized canonically",
        ) from error
