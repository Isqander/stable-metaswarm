#!/usr/bin/env python3
"""Replay captured vendor CLI fixtures, including failure/liveness modes.

Select a fixture profile with FAKE_PROFILE. The original vendor executable is
reconstructed from that profile and matched against the shell-parsed *.argv
files. FAKE_SCENARIO can select a fixture directly when argv contains runtime
values such as a session id or working directory.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
EXECUTABLES = {
    "claude": "claude",
    "claude-m": "claude",
    "claude-z": "claude",
    "codex": "codex",
    "cursor-agent": "cursor-agent",
}
MODES = {"normal", "broken_json", "silent", "no_finish", "ignore_term", "slow"}


def fail(message: str, code: int = 2) -> "NoReturn":
    print(f"fake-cli: {message}", file=sys.stderr)
    raise SystemExit(code)


def scenario_from_argv(profile: str) -> str:
    requested = os.environ.get("FAKE_SCENARIO")
    if requested:
        if not (ROOT / profile / f"{requested}.argv").is_file():
            fail(f"fixture {profile}/{requested} does not exist")
        return requested

    actual = [EXECUTABLES[profile], *sys.argv[1:]]
    matches: list[str] = []
    for path in sorted((ROOT / profile).glob("*.argv")):
        try:
            expected = shlex.split(path.read_text(encoding="utf-8"))
        except ValueError as error:
            fail(f"cannot parse {path.relative_to(ROOT)}: {error}")
        if expected == actual:
            matches.append(path.stem)
    if not matches:
        rendered = shlex.join(actual)
        fail(f"no {profile} fixture matches argv: {rendered}")
    if len(matches) > 1:
        fail(f"ambiguous {profile} fixture match: {', '.join(matches)}")
    return matches[0]


def read_fixture(profile: str, scenario: str) -> tuple[bytes, bytes, int]:
    base = ROOT / profile / scenario
    stdout = base.with_suffix(".stdout").read_bytes()
    stderr = base.with_suffix(".stderr").read_bytes()
    try:
        exit_code = int(base.with_suffix(".exit").read_text(encoding="ascii").strip())
    except ValueError as error:
        fail(f"invalid exit code in {profile}/{scenario}.exit: {error}")
    return stdout, stderr, exit_code


def broken_json_envelope(profile: str) -> bytes:
    invalid_result = (
        "<<<METASWARM-RESULT-BEGIN>>>\n"
        '{"schema":"review.observations.v1","observations":[}\n'
        "<<<METASWARM-RESULT-END>>>"
    )
    if profile.startswith("claude"):
        envelope = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": invalid_result,
        }
    elif profile == "codex":
        envelope = {
            "type": "item.completed",
            "item": {"id": "fake-broken-json", "type": "agent_message", "text": invalid_result},
        }
    else:
        envelope = {"type": "result", "result": invalid_result}
    return (json.dumps(envelope, separators=(",", ":")) + "\n").encode()


def write_normal(stdout: bytes, stderr: bytes) -> None:
    if stderr:
        sys.stderr.buffer.write(stderr)
        sys.stderr.buffer.flush()
    if stdout:
        sys.stdout.buffer.write(stdout)
        sys.stdout.buffer.flush()


def hang() -> "NoReturn":
    while True:
        time.sleep(3600)


def write_slow(stdout: bytes, stderr: bytes) -> None:
    try:
        delay = float(os.environ.get("FAKE_DELAY_S", "0.25"))
        chunk_size = int(os.environ.get("FAKE_CHUNK_BYTES", "80"))
    except ValueError:
        fail("FAKE_DELAY_S and FAKE_CHUNK_BYTES must be numeric")
    if delay < 0 or chunk_size < 1:
        fail("FAKE_DELAY_S must be non-negative and FAKE_CHUNK_BYTES must be positive")
    if stderr:
        sys.stderr.buffer.write(stderr)
        sys.stderr.buffer.flush()
    chunks = [stdout[index : index + chunk_size] for index in range(0, len(stdout), chunk_size)]
    if not chunks:
        chunks = [b""]
    for chunk in chunks:
        time.sleep(delay)
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()


def main() -> int:
    profile = os.environ.get("FAKE_PROFILE", "")
    if profile not in EXECUTABLES:
        fail("FAKE_PROFILE must be one of: " + ", ".join(EXECUTABLES))
    mode = os.environ.get("FAKE_MODE", "normal")
    if mode not in MODES:
        fail("FAKE_MODE must be one of: " + ", ".join(sorted(MODES)))

    scenario = scenario_from_argv(profile)
    stdout, stderr, exit_code = read_fixture(profile, scenario)

    if mode == "silent":
        hang()
    if mode == "broken_json":
        write_normal(broken_json_envelope(profile), stderr)
        return 0
    if mode == "ignore_term":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        write_normal(stdout, stderr)
        hang()
    if mode == "no_finish":
        write_normal(stdout, stderr)
        hang()
    if mode == "slow":
        write_slow(stdout, stderr)
        return exit_code

    write_normal(stdout, stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
