#!/usr/bin/env python3
"""Прогон проверочного SQL-сценария по schema-конструкциям из docs/design.

Сценарий делится маркером `-- === данные ===`: всё до него — DDL, он
исполняется одним `executescript`; всё после — по одному statement за раз,
чтобы ожидаемые нарушения констрейнтов не обрывали прогон.

Строка вида `SELECT 'NN подпись:';` печатается как заголовок шага; результат
следующего statement печатается под ним, а пойманное исключение — с префиксом
ERROR. Ожидания записаны в самих подписях, поэтому вывод читается глазами:
шаг, помеченный «ждём ошибку», обязан её дать, помеченный «ждём тишину» —
не дать.

Использование:  python3 scripts/checks/run-sql-check.py scripts/checks/<файл>.sql
"""

import sqlite3
import sys


def main(path: str) -> int:
    with open(path, encoding="utf-8") as fh:
        sql = fh.read()

    head, marker, tail = sql.partition("-- === данные ===")
    if not marker:
        print("нет маркера `-- === данные ===`", file=sys.stderr)
        return 2

    con = sqlite3.connect(":memory:")
    con.executescript(head)
    con.execute("PRAGMA foreign_keys = ON")

    stmt = ""
    for line in tail.splitlines(True):
        if line.strip().startswith("--"):
            continue
        stmt += line
        if not sqlite3.complete_statement(stmt):
            continue
        statement, stmt = stmt.strip(), ""
        if not statement:
            continue
        try:
            rows = con.execute(statement).fetchall()
        except Exception as exc:            # ожидаемые нарушения — часть сценария
            print("    ERROR:", type(exc).__name__, exc)
            continue
        if statement.startswith("SELECT '"):
            print(statement[8:statement.index("'", 8)])
            continue
        for row in rows:
            print("   ", row)

    con.close()
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
