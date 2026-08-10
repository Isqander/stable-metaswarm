#!/usr/bin/env python3
"""Проверяет Git-границы реализации задачи по trailers closing-коммита."""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
from pathlib import Path


TRAILERS = ("Planning-Baseline", "Implementation-Parent")


def git(*args: str, input_text: str | None = None, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        check=False,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ValueError(f"git {' '.join(args)}: {detail}")
    return result.stdout.strip()


def resolve_full_commit(value: str, label: str) -> str:
    if not re.fullmatch(r"[0-9a-f]+", value):
        raise ValueError(f"{label}: нужен lowercase hex full SHA, получено {value!r}")
    resolved = git("rev-parse", "--verify", f"{value}^{{commit}}")
    if value != resolved:
        raise ValueError(f"{label}: нужен full SHA {resolved}, получено {value}")
    return resolved


def is_ancestor(ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def read_planning_baseline(package: str, registry_path: Path) -> str:
    try:
        with registry_path.open(encoding="utf-8", newline="") as registry_file:
            reader = csv.DictReader(registry_file, delimiter="\t")
            if reader.fieldnames != ["package", "planning_baseline_sha"]:
                raise ValueError(
                    f"неверный header реестра {registry_path}: {reader.fieldnames!r}"
                )
            matches: list[str] = []
            for line_number, row in enumerate(reader, start=2):
                row_package = row.get("package")
                row_sha = row.get("planning_baseline_sha")
                if not row_package or not row_sha or None in row:
                    raise ValueError(
                        f"неполная строка {line_number} реестра {registry_path}"
                    )
                if row_package == package:
                    matches.append(row_sha)
    except (OSError, csv.Error) as error:
        raise ValueError(f"не удалось прочитать реестр {registry_path}: {error}") from error

    if len(matches) != 1:
        raise ValueError(
            f"реестр {registry_path} должен содержать ровно одну строку package={package!r}, "
            f"найдено {len(matches)}"
        )
    return resolve_full_commit(matches[0], f"planning baseline package {package}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("closing_commit", help="full SHA или ref closing-коммита задачи")
    parser.add_argument("--package", required=True, help="ID пакета из planning-baselines.tsv")
    parser.add_argument(
        "--baseline-registry",
        default="docs/task-plans/planning-baselines.tsv",
        help="путь от корня репозитория к реестру planning baseline",
    )
    parser.add_argument("--branch", default="main", help="линейная ветка (default: main)")
    args = parser.parse_args()

    try:
        closing = git("rev-parse", "--verify", f"{args.closing_commit}^{{commit}}")
        branch = git("rev-parse", "--verify", f"{args.branch}^{{commit}}")
        message = git("show", "-s", "--format=%B", closing)
        parsed = git("interpret-trailers", "--parse", input_text=message)
        values: dict[str, list[str]] = {name: [] for name in TRAILERS}
        for line in parsed.splitlines():
            key, separator, value = line.partition(":")
            if separator and key in values:
                values[key].append(value.strip())

        for name in TRAILERS:
            if len(values[name]) != 1:
                raise ValueError(
                    f"closing commit должен иметь ровно один trailer {name}, "
                    f"найдено {len(values[name])}"
                )

        planning = resolve_full_commit(values["Planning-Baseline"][0], "Planning-Baseline")
        parent = resolve_full_commit(values["Implementation-Parent"][0], "Implementation-Parent")
        repository_root = Path(git("rev-parse", "--show-toplevel"))
        expected_planning = read_planning_baseline(
            args.package, repository_root / args.baseline_registry
        )

        if planning != expected_planning:
            raise ValueError(
                f"Planning-Baseline {planning} не совпадает с package {args.package}: "
                f"ожидался {expected_planning}"
            )
        if not is_ancestor(planning, parent):
            raise ValueError("Planning-Baseline не является предком Implementation-Parent")
        if not is_ancestor(parent, closing) or parent == closing:
            raise ValueError("Implementation-Parent не является строгим предком closing commit")
        if not is_ancestor(closing, branch):
            raise ValueError(f"closing commit не достижим из {args.branch}")

        first_parent_chain = set(git("rev-list", "--first-parent", closing).splitlines())
        if parent not in first_parent_chain:
            raise ValueError(
                "Implementation-Parent не лежит на first-parent цепочке closing commit"
            )
        merges = git("rev-list", "--min-parents=2", f"{parent}..{closing}")
        if merges:
            raise ValueError(f"implementation range содержит merge-коммиты: {merges}")

        commit_count = git("rev-list", "--count", f"{parent}..{closing}")
        print(f"OK planning baseline {args.package}: {planning}")
        print(f"OK implementation parent: {parent}")
        print(f"OK closing commit: {closing}")
        print(f"OK linear first-parent range: {commit_count} commit(s)")
        return 0
    except ValueError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
