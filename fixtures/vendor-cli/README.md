# Vendor CLI fixtures

Captured on 2026-08-03 on an Ubuntu 24.04 Linux VPS. Each scenario has the
exact target argv (`.argv`), raw stdout and stderr, observed exit code, and
notes. `S5-setup` is the first turn needed by the resumability scenario;
`S8-missing-cwd` is the second half of the error scenario. Supplemental
fixtures such as `S6-plan-mode` preserve observations that changed the chosen
adapter flags.

Claude S2–S7 and S9 were recaptured after review with the deterministic
isolation flags `--safe-mode --disable-slash-commands --strict-mcp-config` and
an explicit `--tools` set. The normal `HOME` remained available only because
the Anthropic subscription reads its authentication there. In structured
fixtures MCP and skills are empty, auto-memory is absent, and the toolset is
role-specific: empty in S4, `Read/Grep/Glob` in S6, and `Bash` in S9.

The `claude-m` and `claude-z` captures were executed through the temporary
local wrappers that held credentials, but their `.argv` files deliberately
record the target architecture: direct `claude` execution with the profile
environment supplied by the runner. Secret values were never copied; notes
contain only `<REDACTED>`.

## Fake CLI

Set `FAKE_PROFILE` and invoke `fake-cli.py` with the vendor arguments. For
example:

```bash
FAKE_PROFILE=claude fixtures/vendor-cli/fake-cli.py -p 'Reply with exactly: PONG'
FAKE_PROFILE=codex fixtures/vendor-cli/fake-cli.py exec --json 'Reply with exactly: PONG'
```

The fake matches argv after shell parsing and replays the fixture's stdout,
stderr, and exit code. For argv containing runtime values, set
`FAKE_SCENARIO=S5` (or another scenario name) to select directly.
`S8-missing-cwd` must always be selected this way: its `.argv` contains only
the real command, while the nonexistent cwd is launcher metadata in `.notes`.
Missing-cwd fixtures are deliberately excluded from automatic argv matching.
Errors in fake configuration itself return 97, outside all observed vendor
exit codes.

`FAKE_MODE` supports:

| Value | Behaviour |
|---|---|
| `broken_json` | Preserves startup/completion events and corrupts only the domain JSON inside the final result |
| `malformed_stream` | Preserves startup events, then emits syntactically invalid vendor JSON/JSONL |
| `silent` | No output; waits until signalled |
| `no_finish` | Replays output, then never exits |
| `ignore_term` | Replays output, ignores SIGTERM, then never exits |
| `slow` | Replays stdout in delayed chunks (`FAKE_DELAY_S`, `FAKE_CHUNK_BYTES`) |

Unset `FAKE_MODE` (or use `normal`) for exact replay.
