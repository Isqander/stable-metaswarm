#!/usr/bin/env sh
set -eu

if ! command -v uv >/dev/null 2>&1; then
    echo "uv not found; install uv to run project checks" >&2
    exit 127
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_root=$(CDPATH= cd -- "$script_dir/.." && pwd)
cd "$project_root"

uv run --locked pytest -q
uv run --locked ruff check .
