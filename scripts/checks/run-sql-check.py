#!/usr/bin/env python3
"""Прогон проверочного SQL-сценария по конструкциям из docs/design.

Сценарий делится маркером `-- === данные ===`: всё до него — DDL, он
исполняется одним `executescript`; всё после — по одному statement за раз,
чтобы ожидаемое нарушение констрейнта не обрывало прогон.

Ожидание каждого шага записывается директивами перед statement:

    -- @step 03 вторая активная попытка того же слота
    -- @expect error ux_attempt_active
    INSERT INTO step_attempt ...

Директивы `@expect`:

    ok              statement обязан выполниться без исключения
    error [текст]   обязано быть исключение; текст, если указан, — подстрока
    rows=N          SELECT обязан вернуть ровно N строк
    empty           то же, что rows=0
    rows-json JSON  SELECT обязан вернуть ровно эти значения, по порядку;
                    JSON — список списков, например [[0,"p-a",1],[1,"p-e",2]]

`rows=N` проверяет только количество и потому годится там, где значения ничего
не добавляют; где важно, что именно вернулось, — `rows-json`.

Шаг без `@expect` считается подготовкой данных: он обязан пройти без
исключения, но в отчёт не попадает. Несовпадение ожидания — FAIL и код
возврата 1, поэтому проверку можно ставить в конвейер, а не читать глазами.

Использование:
    python3 scripts/checks/run-sql-check.py scripts/checks/<файл>.sql [ещё.sql …]

Файлов можно передать несколько: каждый прогоняется в своей базе, код возврата
ненулевой, если провалился хотя бы один.
"""

import json
import sqlite3
import sys


class Step:
    def __init__(self):
        self.name = None
        self.expect = None      # ("ok"|"error"|"rows", payload)

    def reset(self):
        self.__init__()


def parse_expect(rest: str):
    kind, _, payload = rest.partition(" ")
    kind = kind.strip()
    payload = payload.strip()
    if kind == "ok":
        return ("ok", None)
    if kind == "error":
        return ("error", payload or None)
    if kind == "empty":
        return ("rows", 0)
    if kind.startswith("rows="):
        return ("rows", int(kind[5:]))
    if kind == "rows-json":
        return ("rows-json", [tuple(row) for row in json.loads(payload)])
    raise ValueError("непонятная директива @expect: %r" % rest)


def check(step: Step, error, rows):
    """Возвращает None при совпадении либо текст расхождения."""
    kind, payload = step.expect
    if kind == "ok":
        return None if error is None else "ждали успех, получили %s" % error
    if kind == "error":
        if error is None:
            return "ждали ошибку, statement прошёл"
        if payload and payload not in str(error):
            return "ждали ошибку с %r, получили %s" % (payload, error)
        return None
    if kind == "rows":
        if error is not None:
            return "ждали %d строк, получили %s" % (payload, error)
        if len(rows) != payload:
            return "ждали %d строк, получили %d: %s" % (payload, len(rows), rows)
        return None
    if kind == "rows-json":
        if error is not None:
            return "ждали %s, получили %s" % (payload, error)
        if rows != payload:
            return "ждали %s, получили %s" % (payload, rows)
        return None
    return "неизвестный вид ожидания %r" % kind


def main(path: str) -> int:
    # Подписи шагов по-русски, а консоль Windows по умолчанию не UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    with open(path, encoding="utf-8") as fh:
        sql = fh.read()

    head, marker, tail = sql.partition("-- === данные ===")
    if not marker:
        print("нет маркера `-- === данные ===`", file=sys.stderr)
        return 2

    con = sqlite3.connect(":memory:")
    con.executescript(head)
    con.execute("PRAGMA foreign_keys = ON")
    # DELETE-триггеры при INSERT OR REPLACE в SQLite вызываются только при
    # recursive_triggers=ON. Production открывает каждое соединение с тем же
    # обязательным PRAGMA; без него сценарии immutable/no-delete доказывали бы
    # только прямой DELETE и пропускали замену строки целиком.
    con.execute("PRAGMA recursive_triggers = ON")

    step, stmt = Step(), ""
    passed = failed = 0
    failed_ids = []

    for line in tail.splitlines(True):
        stripped = line.strip()
        if stripped.startswith("-- @step "):
            step.name = stripped[len("-- @step "):]
            continue
        if stripped.startswith("-- @expect "):
            step.expect = parse_expect(stripped[len("-- @expect "):])
            continue
        if stripped.startswith("--"):
            continue

        stmt += line
        if not sqlite3.complete_statement(stmt):
            continue
        statement, stmt = stmt.strip(), ""
        if not statement:
            continue

        # Шаг с ожиданием идёт в своём savepoint. Без этого мутационная
        # проверка врёт: разрешённая мутацией вставка остаётся в базе и роняет
        # соседние сценарии, а назначенный шаг может покраснеть «за компанию».
        # Подготовка (без @expect) выполняется обычным порядком — её эффект
        # нужен последующим шагам.
        guarded = step.expect is not None
        if guarded:
            con.execute("SAVEPOINT step")

        error = rows = None
        try:
            rows = con.execute(statement).fetchall()
        except Exception as exc:                      # ожидаемое — часть сценария
            error = "%s: %s" % (type(exc).__name__, exc)

        if step.expect is None:
            if error is not None:
                print("FAIL  подготовка данных: %s" % error)
                print("      %s" % statement.splitlines()[0])
                failed += 1
            step.reset()
            continue

        problem = check(step, error, rows)
        # Ожидание совпало — эффект шага остаётся; не совпало — откатываем,
        # чтобы одна ошибка не превращалась в лавину чужих.
        if problem is None:
            con.execute("RELEASE step")
        else:
            con.execute("ROLLBACK TO step")
            con.execute("RELEASE step")
        label = step.name or statement.splitlines()[0]
        if problem is None:
            passed += 1
            print("OK    %s" % label)
        else:
            failed += 1
            failed_ids.append(label.split(" ", 1)[0])
            print("FAIL  %s" % label)
            print("      %s" % problem)
        step.reset()

    # Хвост файла: оборванный statement или директива без statement — это
    # молча исчезнувший тест, а не «конец сценария».
    if stmt.strip():
        print("FAIL  оборванный statement в конце файла:")
        print("      %s" % stmt.strip().splitlines()[0])
        failed += 1
        failed_ids.append("<хвост>")
    if step.expect is not None:
        print("FAIL  @expect без statement: %s" % (step.name or "<без подписи>"))
        failed += 1
        failed_ids.append("<без-statement>")

    con.close()
    print(chr(10) + "пройдено %d, провалено %d" % (passed, failed))
    if failed_ids:
        # Машиночитаемо для mutation-check: какие именно шаги упали.
        print("FAILED-STEPS: %s" % ",".join(failed_ids))
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    codes = []
    for path in sys.argv[1:]:
        if len(sys.argv) > 2:
            print("=== %s" % path)
        codes.append(main(path))
    sys.exit(max(codes))
