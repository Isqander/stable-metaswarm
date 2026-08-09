#!/usr/bin/env python3
"""Mutation-проверка: снять одно ограничение и убедиться, что покраснел
именно тот сценарий, который его доказывает.

Зелёный прогон сам по себе ничего не доказывает: негативный шаг может падать
по соседнему ключу, а не по проверяемому. Поэтому для каждого ограничения
заводится мутация и **назначенный шаг**; проверка проходит, только если после
снятия ограничения этот шаг оказался среди упавших.

Три режима:

    python3 scripts/checks/mutation-check.py scripts/checks/mutations.tsv
    python3 scripts/checks/mutation-check.py --coverage scripts/checks/mutations.tsv
    python3 scripts/checks/mutation-check.py <файл.sql> "<мутация>" [ещё…]

`--coverage` отвечает на вопрос, который манифест сам о себе не задаёт: какие
ограничения sql-файлов **не имеют ни одной строки**. Прогон по манифесту может
быть «68 из 68» при том, что двенадцать констрейнтов в него просто не попали, —
и такой разрыв ищется руками ровно до первого пропуска. Режим печатает
недостающие строки в формате манифеста, чтобы их оставалось только заполнить
шагом, и возвращает ненулевой код, пока разрыв есть.

Манифест — TSV: sql-файл, мутация, ожидаемый шаг и необязательный статус.
Статус `todo: <причина>` означает известный долг — такая мутация считается
**недоказанной** и видна в итоге отдельным числом, а не исчезает из отчёта.
Перед мутациями каждый файл прогоняется как есть: красный baseline обесценивает
любую мутацию. Строки, начинающиеся с `#`, игнорируются.

Мутация записывается одной из следующих форм:

    <таблица>::<фрагмент>   вырезать строку с фрагментом внутри CREATE TABLE
    TRIGGER:<имя>           удалить блок CREATE TRIGGER целиком
    TRIGGER-WHEN:<имя>::<условие>
                            удалить один верхнеуровневый дизъюнкт WHEN;
                            для помеченных @mutation-cover-when триггеров
                            одной строки на весь trigger недостаточно
    TRIGGER-UPDATE-OF:<имя>::<колонка>
                            убрать колонку из списка UPDATE OF; для помеченных
                            @mutation-cover-update-of проверяется каждая
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


def trigger_when(sql: str, name: str):
    """Вернуть span WHEN-body и простые верхнеуровневые OR-термы.

    Гранулярная мутация намеренно поддерживает только форму, в которой каждый
    верхнеуровневый дизъюнкт занимает одну строку (`WHEN ...`, затем `OR ...`).
    Это делает манифест читаемым и не притворяется SQL-парсером. Помеченный
    trigger с другой формой coverage объявит сломанным.
    """
    start = sql.find("CREATE TRIGGER " + name)
    if start < 0:
        return None
    begin = re.search(r"(?m)^BEGIN\s*$", sql[start:])
    if not begin:
        return None
    header_end = start + begin.start()
    header = sql[start:header_end]
    match = re.search(r"(?s)\bWHEN\s+(.+?)\s*\Z", header)
    if not match:
        return None
    body_start = start + match.start(1)
    body_end = start + match.end(1)
    lines = [line.strip() for line in match.group(1).splitlines()
             if line.strip()]
    if not lines:
        return None
    terms = [lines[0]]
    for line in lines[1:]:
        if not line.startswith("OR "):
            return None
        terms.append(line[3:].strip())
    return body_start, body_end, terms


def strip_trigger_when_term(sql: str, name: str, needle: str) -> str:
    parsed = trigger_when(sql, name)
    if parsed is None:
        return sql
    start, end, terms = parsed
    normalized = squash(needle)
    indexes = [i for i, term in enumerate(terms) if squash(term) == normalized]
    if len(indexes) != 1 or len(terms) == 1:
        return sql
    del terms[indexes[0]]
    replacement = terms[0] + "\n" + "\n".join("  OR " + t for t in terms[1:])
    return sql[:start] + replacement + sql[end:]


def trigger_update_columns(sql: str, name: str):
    start = sql.find("CREATE TRIGGER " + name)
    if start < 0:
        return None
    begin = re.search(r"(?m)^BEGIN\s*$", sql[start:])
    if not begin:
        return None
    header_end = start + begin.start()
    header = sql[start:header_end]
    match = re.search(r"(?is)\bBEFORE\s+UPDATE\s+OF\s+(.+?)\s+ON\s+\w+",
                      header)
    if not match:
        return None
    columns = [item.strip() for item in match.group(1).split(",")]
    if not columns or any(not re.fullmatch(r"\w+", col) for col in columns):
        return None
    return start + match.start(1), start + match.end(1), columns


def strip_trigger_update_column(sql: str, name: str, needle: str) -> str:
    parsed = trigger_update_columns(sql, name)
    if parsed is None:
        return sql
    start, end, columns = parsed
    indexes = [i for i, col in enumerate(columns) if col == needle.strip()]
    if len(indexes) != 1 or len(columns) == 1:
        return sql
    del columns[indexes[0]]
    replacement = ", ".join(columns)
    return sql[:start] + replacement + sql[end:]


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
    if mutation.startswith("TRIGGER-UPDATE-OF:"):
        spec = mutation[len("TRIGGER-UPDATE-OF:"):]
        if "::" not in spec:
            return source
        name, column = spec.split("::", 1)
        return strip_trigger_update_column(source, name, column)
    if mutation.startswith("TRIGGER-WHEN:"):
        spec = mutation[len("TRIGGER-WHEN:"):]
        if "::" not in spec:
            return source
        name, fragment = spec.split("::", 1)
        return strip_trigger_when_term(source, name, fragment)
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


def read_manifest(path: str):
    rows = []
    for raw in io.open(path, encoding="utf-8"):
        line = raw.rstrip(chr(10))
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split(chr(9))
        if len(parts) == 3:
            parts.append("active")
        if len(parts) != 4:
            raise ValueError("строка манифеста не из 3-4 колонок: %s" % line)
        rows.append(parts)
    return rows


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def split_top_level(body: str):
    """Разбить тело CREATE TABLE по запятым нулевого уровня вложенности."""
    items, depth, start = [], 0, 0
    for i, ch in enumerate(body):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            items.append(body[start:i])
            start = i + 1
    items.append(body[start:])
    return [squash(strip_comments(item)) for item in items]


def strip_comments(text: str) -> str:
    return re.sub(r"--[^\n]*", "", text)


def referenced_keys(sql: str):
    """Все ключи, на которые в файле кто-то ссылается: (таблица, колонки)."""
    out = set()
    for m in re.finditer(r"REFERENCES (\w+)\s*\(([^)]*)\)", sql):
        out.add((m.group(1), tuple(c.strip() for c in m.group(2).split(","))))
    return out


def constraints_of(sql: str, referenced):
    """Все ограничения уровня таблицы и триггеры файла.

    Инлайновые `REFERENCES`/`NOT NULL` в объявлении колонки сюда не входят
    намеренно: одноколоночная ссылка на родителя — тривиальный случай, ради
    которого сценарии scope не пишутся, а FK на закрытые справочники доказывает
    отдельный манифест T1.3. Граница названа здесь, чтобы «покрыто N из N» не
    означало большего, чем есть.
    """
    # Родительские ключи составных FK: `UNIQUE (id, run_id)` существует не как
    # самостоятельное правило, а чтобы на него можно было сослаться. Снять его
    # значит не ослабить проверку, а сломать схему: SQLite отвечает «foreign key
    # mismatch» на каждую вставку через зависимый ключ, краснеет пол-файла, и
    # назначенный шаг доказывает не своё. Такие UNIQUE из отчёта исключаются —
    # их держит сам факт, что зависимый FK работает.
    #
    # `referenced` приходит снаружи и собран по ВСЕМ сценарным файлам: стаб —
    # это срез схемы под свой сюжет, и ключ, никому не нужный здесь, вполне
    # может быть несущим в соседнем файле. Считать ссылки по одному файлу
    # значит объявлять мёртвым живое.
    found, dead = [], []
    for m in re.finditer(r"CREATE TABLE (\w+) \(", sql):
        table, start = m.group(1), m.end()
        depth, i = 1, start
        while i < len(sql) and depth:
            if sql[i] == "(":
                depth += 1
            elif sql[i] == ")":
                depth -= 1
            i += 1
        body = split_top_level(sql[start:i - 1])
        pk = next((re.match(r"(\w+)", c).group(1) for c in body
                   if "PRIMARY KEY" in c and re.match(r"\w+\s+\w+", c)), None)
        for item in body:
            if not re.match(r"(FOREIGN KEY|CHECK|UNIQUE)\b", item):
                continue
            unique = re.match(r"UNIQUE\s*\(([^)]*)\)$", item)
            if unique:
                cols = tuple(c.strip() for c in unique.group(1).split(","))
                # UNIQUE, куда входит первичный ключ, не может отвергнуть ни
                # одной строки: PK уже уникален. Такой ключ существует ровно
                # затем, чтобы на него ссылались, — и если никто не ссылается,
                # он не «недоказан», он мёртв. Мутация тут не нужна, нужна
                # правка схемы, поэтому такие ключи идут отдельным списком.
                if pk and pk in cols:
                    if (table, cols) not in referenced:
                        dead.append((table, item))
                    continue
            found.append((table, item))
    for m in re.finditer(r"CREATE TRIGGER (\w+)", sql):
        found.append((None, "TRIGGER:" + m.group(1)))
    return found, dead


def addresses(mutation: str):
    for part in mutation.split(" && "):
        part = part.strip()
        if part.startswith("TRIGGER-UPDATE-OF:"):
            spec = part[len("TRIGGER-UPDATE-OF:"):]
            name = spec.split("::", 1)[0]
            yield (None, "TRIGGER:" + name)
        elif part.startswith("TRIGGER-WHEN:"):
            spec = part[len("TRIGGER-WHEN:"):]
            name = spec.split("::", 1)[0]
            yield (None, "TRIGGER:" + name)
        elif part.startswith("TRIGGER:"):
            yield (None, part)
        elif "::" in part:
            table, fragment = part.split("::", 1)
            yield (table, squash(fragment))
        else:
            yield ("*", squash(part))


def covers(address, constraint) -> bool:
    a_table, a_text = address
    c_table, c_text = constraint
    if a_text.startswith("TRIGGER:"):
        return a_text == c_text
    if c_text.startswith("TRIGGER:"):
        return False
    if a_table not in ("*", c_table):
        return False
    return a_text in c_text


def run_coverage(path: str) -> int:
    """Какие ограничения sql-файлов не имеют ни одной строки манифеста.

    Разрыв покрытия руками не ищется: правило «каждое ограничение — своя
    мутация» проверяемо механически, и без этого режима оно держится на
    внимательности того, кто правил DDL последним.
    """
    rows = read_manifest(path)

    # Счёт по различимым ограничениям, а не по их копиям в стабах: один и тот
    # же ключ живёт в двух-трёх сценарных файлах, но доказать его достаточно
    # один раз — в том, где он несущий. Поэтому и адреса берутся из всего
    # манифеста, а не только из строк своего файла.
    files = sorted({r[0] for r in rows})
    sources = {f: io.open(f, encoding="utf-8").read() for f in files}
    referenced = set().union(*(referenced_keys(s) for s in sources.values()))

    where, dead = {}, {}
    for sql_file in files:
        sql = sources[sql_file]
        live, orphans = constraints_of(sql, referenced)
        for constraint in live:
            where.setdefault(constraint, []).append(sql_file)
        for constraint in orphans:
            dead.setdefault(constraint, []).append(sql_file)

    addrs = [a for _f, mutation, _e, _s in rows for a in addresses(mutation)]
    missing = [c for c in where if not any(covers(a, c) for a in addrs)]

    # Для явно помеченных многосоставных WHEN структурной строки TRIGGER:name
    # недостаточно: она доказывает только, что хоть какая-то часть trigger
    # нужна. Каждый верхнеуровневый OR обязан иметь собственную granular row.
    expected_branches = []
    broken_annotations = []
    for sql_file, source in sources.items():
        for name in re.findall(r"(?m)^-- @mutation-cover-when (\w+)\s*$",
                               source):
            parsed = trigger_when(source, name)
            if parsed is None:
                broken_annotations.append((sql_file, name))
                continue
            expected_branches.extend((sql_file, name, term)
                                     for term in parsed[2])

    covered_branches = set()
    covered_update_columns = set()
    for sql_file, mutation, _expected, _status in rows:
        for part in mutation.split(" && "):
            part = part.strip()
            if part.startswith("TRIGGER-UPDATE-OF:") and "::" in part:
                name, column = part[len("TRIGGER-UPDATE-OF:"):].split("::", 1)
                covered_update_columns.add((sql_file, name, column.strip()))
                continue
            if not part.startswith("TRIGGER-WHEN:") or "::" not in part:
                continue
            name, term = part[len("TRIGGER-WHEN:"):].split("::", 1)
            covered_branches.add((sql_file, name, squash(term)))
    missing_branches = [
        (sql_file, name, term)
        for sql_file, name, term in expected_branches
        if (sql_file, name, squash(term)) not in covered_branches
    ]

    expected_update_columns = []
    broken_update_annotations = []
    for sql_file, source in sources.items():
        for name in re.findall(
                r"(?m)^-- @mutation-cover-update-of (\w+)\s*$", source):
            parsed = trigger_update_columns(source, name)
            if parsed is None:
                broken_update_annotations.append((sql_file, name))
                continue
            expected_update_columns.extend((sql_file, name, column)
                                           for column in parsed[2])
    missing_update_columns = [
        (sql_file, name, column)
        for sql_file, name, column in expected_update_columns
        if (sql_file, name, column) not in covered_update_columns
    ]

    for constraint in sorted(missing, key=lambda c: (where[c][0], c[0] or "", c[1])):
        table, text = constraint
        address = text if table is None else "%s::%s" % (table, text)
        print("%s%s%s%s<шаг>" % (where[constraint][0], chr(9), address, chr(9)))

    for sql_file, name in broken_annotations:
        print("BROKEN annotation %s: trigger %s не имеет простой WHEN/OR-формы"
              % (sql_file, name))
    for sql_file, name, term in missing_branches:
        address = "TRIGGER-WHEN:%s::%s" % (name, squash(term))
        print("%s%s%s%s<шаг>" % (sql_file, chr(9), address, chr(9)))
    for sql_file, name in broken_update_annotations:
        print("BROKEN annotation %s: trigger %s не имеет UPDATE OF-списка"
              % (sql_file, name))
    for sql_file, name, column in missing_update_columns:
        address = "TRIGGER-UPDATE-OF:%s::%s" % (name, column)
        print("%s%s%s%s<шаг>" % (sql_file, chr(9), address, chr(9)))

    if dead:
        print(chr(10) + "МЁРТВЫЕ КЛЮЧИ — не мутация, а правка схемы:")
        for (table, text), files in sorted(dead.items()):
            print("  %s.%s — %s" % (table, text, files[0]))

    print(chr(10) + "ограничений %d / покрыто %d / без строки %d / мёртвых ключей %d"
          % (len(where), len(where) - len(missing), len(missing), len(dead)))
    print("ветвей WHEN %d / покрыто %d / без строки %d / сломанных меток %d"
          % (len(expected_branches), len(expected_branches) - len(missing_branches),
             len(missing_branches), len(broken_annotations)))
    print("колонок UPDATE OF %d / покрыто %d / без строки %d / сломанных меток %d"
          % (len(expected_update_columns),
             len(expected_update_columns) - len(missing_update_columns),
             len(missing_update_columns), len(broken_update_annotations)))
    return 1 if (missing or dead or missing_branches or broken_annotations
                 or missing_update_columns or broken_update_annotations) else 0


def run_manifest(path: str) -> int:
    try:
        rows = read_manifest(path)
    except ValueError as exc:
        print("BROKEN %s" % exc)
        return 2

    cache, bad, todo = {}, 0, 0

    # Baseline: без зелёного исходника мутация ничего не доказывает — шаг мог
    # быть красным и до неё.
    for sql_file in sorted({r[0] for r in rows}):
        source = cache.setdefault(sql_file,
                                  io.open(sql_file, encoding="utf-8").read())
        count, steps = run_sql(source)
        if count != 0:
            print("BROKEN baseline %s не зелёный: провалено %d (%s)"
                  % (sql_file, count, ",".join(steps)))
            return 2

    for sql_file, mutation, expected, status in rows:
        source = cache[sql_file]
        if status.startswith("todo"):
            todo += 1
            print("TODO   %s → шаг %s: %s"
                  % (mutation, expected, status.partition(":")[2].strip()
                     or "сценарий не изолирован"))
            continue
        if not check_one(source, mutation, expected):
            bad += 1

    active = len(rows) - todo
    print(chr(10) + "доказано %d / всего %d / TODO %d / без силы %d"
          % (active - bad, len(rows), todo, bad))
    return 1 if bad else 0


def main(argv) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if len(argv) == 3 and argv[1] == "--coverage":
        return run_coverage(argv[2])
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
