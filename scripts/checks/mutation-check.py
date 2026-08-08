#!/usr/bin/env python3
"""Mutation-проверка: снять один констрейнт и убедиться, что тест покраснел.

Негативный сценарий доказывает ровно то, что проверяет, только если после
удаления соответствующего FK/CHECK/триггера он падает — и падает именно он.
Скрипт по очереди вырезает из SQL-файла строки, содержащие заданную подстроку,
и прогоняет сценарий: ожидается, что число провалов станет больше нуля.

Использование:
    python3 scripts/checks/mutation-check.py <файл.sql> "<подстрока>" [ещё…]

Каждая подстрока — фрагмент одного констрейнта (например,
"FOREIGN KEY (attempt_id, round_id)"). Вырезание идёт по логическим строкам
DDL: строка с подстрокой и, если она продолжается, следующая строка с
REFERENCES. Для триггера подстрока пишется как "TRIGGER:<имя>" — он удаляется
блоком целиком, иначе ломается синтаксис.

Несколько фрагментов можно объединить через " && ": они вырезаются вместе.
Это нужно для **перекрывающихся** ключей — например, «вопрос той же кампании»
и «наблюдение той же кампании» ловят одну и ту же строку, поэтому поодиночке
каждый из них выглядит недоказанным, а вместе доказываются оба.
"""

import io
import re
import subprocess
import sys
import tempfile
from pathlib import Path

RUNNER = Path(__file__).with_name("run-sql-check.py")


def strip_trigger(sql: str, name: str) -> str:
    """Удалить весь блок CREATE TRIGGER <name> ... END; — вырезать одну его
    строку нельзя, это ломает синтаксис, а не ослабляет проверку."""
    start = sql.find("CREATE TRIGGER " + name)
    if start < 0:
        return sql
    end = sql.find("END;", start)
    if end < 0:
        return sql
    return sql[:start] + sql[end + len("END;"):]


def strip_constraint_lines(sql: str, needle: str) -> str:
    lines = sql.splitlines(True)
    out, skip_next = [], False
    for line in lines:
        if skip_next and "REFERENCES" in line:
            skip_next = False
            continue
        skip_next = False
        if needle in line:
            if "CHECK" in line:
                # CHECK нельзя просто убрать: если он последний в списке,
                # предыдущая строка останется с запятой. Ослабляем до CHECK (1).
                tail = "," if line.rstrip().endswith(",") else ""
                indent = line[:len(line) - len(line.lstrip())]
                out.append(indent + "CHECK (1)" + tail + chr(10))
                continue
            # constraint может занимать две строки: сама и REFERENCES ниже
            if "REFERENCES" not in line:
                skip_next = True
            continue
        out.append(line)
    return "".join(out)


def failures(sql_text: str) -> int:
    with tempfile.NamedTemporaryFile("w", suffix=".sql", encoding="utf-8",
                                     delete=False, newline="\n") as fh:
        fh.write(sql_text)
        path = fh.name
    proc = subprocess.run([sys.executable, str(RUNNER), path],
                          capture_output=True, text=True, encoding="utf-8")
    m = re.search(r"провалено (\d+)", proc.stdout or "")
    return int(m.group(1)) if m else -1


def main(argv) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    source = io.open(argv[1], encoding="utf-8").read()
    base = failures(source)
    if base != 0:
        print("исходный сценарий не зелёный: провалено %d" % base)
        return 1

    bad = 0
    for needle in argv[2:]:
        mutated = source
        for part in needle.split(" && "):     # перекрывающиеся ключи — вместе
            part = part.strip()
            if part.startswith("TRIGGER:"):
                mutated = strip_trigger(mutated, part[len("TRIGGER:"):])
            else:
                mutated = strip_constraint_lines(mutated, part)
        if mutated == source:
            print("SKIP  подстрока не найдена: %s" % needle)
            bad += 1
            continue
        got = failures(mutated)
        if got < 0:
            print("BROKEN мутация сломала DDL, а не ослабила проверку: %s" % needle)
            bad += 1
            continue
        if got > 0:
            print("OK    без «%s» краснеет %d сценариев" % (needle, got))
        else:
            print("FAIL  без «%s» всё зелёное — тест доказывает не это" % needle)
            bad += 1
    print("\nпроверено %d, без доказательной силы %d" % (len(argv) - 2, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
