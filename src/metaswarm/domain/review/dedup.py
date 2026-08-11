from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass


def _canonical_string(value: str) -> str:
    if type(value) is not str:
        raise ValueError("fingerprint string fields must be strings")
    normalized = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    try:
        normalized.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("fingerprint strings must be encodable as UTF-8") from error
    return normalized


@dataclass(frozen=True, slots=True)
class ObservationFingerprint:
    severity: str
    title: str
    body: str
    file_path: str | None
    line_start: int | None
    line_end: int | None

    def __post_init__(self) -> None:
        _canonical_string(self.severity)
        _canonical_string(self.title)
        _canonical_string(self.body)
        if self.file_path is not None:
            _canonical_string(self.file_path)
        if (self.line_start is None) != (self.line_end is None):
            raise ValueError("fingerprint line bounds must be both present or both absent")
        if self.line_start is not None:
            if type(self.line_start) is not int or type(self.line_end) is not int:
                raise ValueError("fingerprint line bounds must be integers")
            if self.line_start < 1 or self.line_end < self.line_start:
                raise ValueError("fingerprint line bounds are invalid")


def observation_dedup_key(fingerprint: ObservationFingerprint) -> str:
    if not isinstance(fingerprint, ObservationFingerprint):
        raise ValueError("fingerprint has an invalid type")
    payload = (
        _canonical_string(fingerprint.severity),
        _canonical_string(fingerprint.title),
        _canonical_string(fingerprint.body),
        None
        if fingerprint.file_path is None
        else _canonical_string(fingerprint.file_path),
        fingerprint.line_start,
        fingerprint.line_end,
    )
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


__all__ = ("ObservationFingerprint", "observation_dedup_key")
