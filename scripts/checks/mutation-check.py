#!/usr/bin/env python3
"""Mutation-проверка: снять одно ограничение и убедиться, что покраснел
именно тот сценарий, который его доказывает.

Зелёный прогон сам по себе ничего не доказывает: негативный шаг может падать
по соседнему ключу, а не по проверяемому. Поэтому для каждого ограничения
заводится мутация и **назначенный шаг**; проверка проходит, только если после
снятия ограничения этот шаг оказался среди упавших.

Два режима:

    python3 scripts/checks/mutation-check.py scripts/checks/mutations.tsv
    python3 scripts/checks/mutation-check.py <файл.sql> "<мутация>" [ещё…]

Манифест — TSV из трёх колонок: sql-файл, мутация, ожидаемый шаг. Строки,
начинающиеся с `#`, игнорируются.

Мутация записывается одним из трёх способов:

    <таблица>::<фрагмент>   вырезать строку с фрагментом внутри CREATE TABLE
    TRIGGER:<имя>           удалить блок CREATE TRIGGER целиком
    <фрагмент>              вырезать по всему файлу (осторожно: фрагмент
                            может встречаться в нескольких таблицах)

Адресация по таблице обязательна для повторяющихся ключей вроде
`FOREIGN KEY (campaign_id, run_id)`: без неё вырезаются все одноимённые
ограничения сразу и ломается DDL, а не ослабляется проверка.

CHECK не вырезается, а ослабляется до `CHECK (1)`: удаление последнего в
списке оставило бы висящую запятую, то есть сломало бы схему.
"""

import io
import re
import subprocess
import sys
import tempfile
from pathlib import Path

RUNNER = Path(__file__).with_name("run-sql-check.py")


def strip_trigger(sql: str, name: str) -> str:
    start = sql.find("CREATE TRIGGER " + name)
    if start < 0:
        return sql
    end = sql.find("END;", start)
    if end < 0:
        return sql
    return sql[:start] + sql[end + len("END;"):]


def table_block(sql: str, table: str):
    start = sql.find("CREATE TABLE " + table + " (")
    if start < 0:
        return None
    end = sql.find(");", start)
    return (start, end + 2) if end > 0 else None


def strip_constraint_lines(sql: str, needle: str, table: str = None) -> str:
    if table:
        span = table_block(sql, table)
        if span is None:
            return sql
        head, body, tail = sql[:span[0]], sql[span[0]:span[1]], sql[span[1]:]
        return head + strip_constraint_lines(body, needle) + tail

    out, skip_next = [], False
    it = iter(sql.splitlines(True))
    for line in it:
        if skip_next and "REFERENCES" in line:
            skip_next = False
            continue
        skip_next = False
        if needle in line:
            if "CHECK" in line:
                # CHECK может занимать несколько строк: считаем скобки, пока
                # выражение не закроется, и всё это заменяем на CHECK (1).
                depth = line.count("(") - line.count(")")
                tail_comma = line.rstrip().endswith(",")
                while depth > 0:
                    nxt = next(it, "")
                    if not nxt:
                        break
                    depth += nxt.count("(") - nxt.count(")")
                    tail_comma = nxt.rstrip().endswith(",")
                indent = line[:len(line) - len(line.lstrip())]
                out.append(indent + "CHECK (1)" + ("," if tail_comma else "")
                           + chr(10))
                continue
            if "REFERENCES" not in line:
                skip_next = True          # ограничение занимает две строки
            continue
        out.append(line)

    # Если вырезали последнее ограничение списка, у предыдущей строки осталась
    # висящая запятая — это сломанный DDL, а не ослабленная проверка.
    for i in range(len(out) - 1, -1, -1):
        stripped = out[i].strip()
        if not stripped:
            continue
        if stripped.startswith(");"):
            j = i - 1
            while j >= 0 and not out[j].strip():
                j -= 1
            if j >= 0 and out[j].rstrip().endswith(","):
                out[j] = out[j].rstrip().rstrip(",") + chr(10)
            break
    return "".join(out)


def apply_mutation(source: str, mutation: str) -> str:
    if " && " in mutation:
        # Перекрывающиеся ограничения снимаются вместе: поодиночке каждое
        # выглядит недоказанным, потому что нарушение ловит соседнее.
        result = source
        for part in mutation.split(" && "):
            result = apply_mutation(result, part.strip())
        return result
    if mutation.startswith("TRIGGER:"):
        return strip_trigger(source, mutation[len("TRIGGER:"):])
    if "::" in mutation:
        table, fragment = mutation.split("::", 1)
        return strip_constraint_lines(source, fragment, table)
    return strip_constraint_lines(source, mutation)


def run_sql(sql_text: str):
    """Возвращает (число провалов, список ID упавших шагов)."""
    with tempfile.NamedTemporaryFile("w", suffix=".sql", encoding="utf-8",
                                     delete=False, newline=chr(10)) as fh:
        fh.write(sql_text)
        path = fh.name
    proc = subprocess.run([sys.executable, str(RUNNER), path],
                          capture_output=True, text=True, encoding="utf-8")
    out = proc.stdout or ""
    m = re.search("провалено ([0-9]+)", out)
    count = int(m.group(1)) if m else -1
    ids = re.search("FAILED-STEPS: (.+)", out)
    return count, (ids.group(1).split(",") if ids else [])


def check_one(source: str, mutation: str, expected: str) -> bool:
    mutated = apply_mutation(source, mutation)
    if mutated == source:
        print("SKIP   %s — фрагмент не найден" % mutation)
        return False
    count, steps = run_sql(mutated)
    if count < 0:
        print("BROKEN %s — мутация сломала DDL, а не ослабила проверку" % mutation)
        return False
    if expected in steps:
        print("OK     %s → шаг %s покраснел" % (mutation, expected))
        return True
    print("FAIL   %s → шаг %s остался зелёным (упали: %s)"
          % (mutation, expected, ",".join(steps) or "никто"))
    return False


def run_manifest(path: str) -> int:
    rows = []
    for raw in io.open(path, encoding="utf-8"):
        line = raw.rstrip(chr(10))
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split(chr(9))
        if len(parts) != 3:
            print("BROKEN строка манифеста не из трёх колонок: %s" % line)
            return 2
        rows.append(parts)

    cache, bad = {}, 0
    for sql_file, mutation, expected in rows:
        source = cache.setdefault(sql_file,
                                  io.open(sql_file, encoding="utf-8").read())
        if not check_one(source, mutation, expected):
            bad += 1
    print(chr(10) + "мутаций %d, без доказательной силы %d" % (len(rows), bad))
    return 1 if bad else 0


def main(argv) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if len(argv) == 2 and argv[1].endswith(".tsv"):
        return run_manifest(argv[1])
    if len(argv) < 3:
        print(__doc__)
        return 2

    source = io.open(argv[1], encoding="utf-8").read()
    base, _ = run_sql(source)
    if base != 0:
        print("исходный сценарий не зелёный: провалено %d" % base)
        return 1
    bad = 0
    for mutation in argv[2:]:
        mutated = apply_mutation(source, mutation)
        if mutated == source:
            print("SKIP   %s — фрагмент не найден" % mutation)
            bad += 1
            continue
        count, steps = run_sql(mutated)
        if count < 0:
            print("BROKEN %s — мутация сломала DDL" % mutation)
            bad += 1
        elif count > 0:
            print("OK     %s → покраснели: %s" % (mutation, ",".join(steps)))
        else:
            print("FAIL   %s → всё зелёное" % mutation)
            bad += 1
    print(chr(10) + "мутаций %d, без доказательной силы %d" % (len(argv) - 2, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
