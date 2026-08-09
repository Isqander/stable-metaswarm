"""Общие примитивы для исполнения нормативной DDL из Markdown.

Здесь намеренно нет проверок конкретных инвариантов: модуль только извлекает
SQL-fence'ы, отделяет schema/seed statements и читает заявленный inventory.
Так связный прогон и parity-проверка работают с одной и той же DDL.
"""

import re
import sqlite3


def sql_fences(markdown: str):
    """Возвращает statements из fenced-блоков ```sql в порядке документа."""
    inside = False
    buffer = ""
    for line in markdown.splitlines(True):
        marker = line.strip()
        if marker == "```sql":
            inside = True
            buffer = ""
            continue
        if inside and marker == "```":
            if buffer.strip():
                raise ValueError("оборванный SQL statement перед закрытием fence")
            inside = False
            continue
        if not inside:
            continue
        buffer += line
        if sqlite3.complete_statement(buffer):
            yield buffer.strip()
            buffer = ""
    if inside or buffer.strip():
        raise ValueError("незакрытый SQL fence или statement")


def statement_head(statement: str) -> str:
    without_comments = re.sub(r"(?m)^\s*--.*(?:\n|$)", "", statement)
    return without_comments.lstrip().upper()


def design_schema_statements(markdown: str):
    """DDL и seed statements; диагностические SELECT/PRAGMA не исполняются."""
    for statement in sql_fences(markdown):
        head = statement_head(statement)
        if head.startswith("CREATE ") or head.startswith("INSERT INTO "):
            yield statement


def declared_inventory(markdown: str):
    """Читает inventory, не завися от русского склонения числительных."""
    match = re.search(
        r"\*\*(\d+)\s+таблиц\w*,\s*(\d+)\s+явн\w+\s+индекс\w*,\s*"
        r"(\d+)\s+представлен\w+\s+и\s+(\d+)\s+триггер\w*\*\*",
        markdown,
    )
    if match is None:
        return None
    return tuple(int(value) for value in match.groups())


def execute_design_schema(connection: sqlite3.Connection, markdown: str):
    for statement in design_schema_statements(markdown):
        connection.execute(statement)


def table_ddl(connection: sqlite3.Connection) -> str:
    """Возвращает исполнявшиеся CREATE TABLE без внутренних объектов SQLite."""
    rows = connection.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return ";\n".join(row[0] for row in rows if row[0]) + ";\n"


def explicit_indexes(connection: sqlite3.Connection):
    """Именованные индексы; внутренние autoindex'ы table constraints не входят."""
    return {
        row[0]: row[1]
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type='index' AND sql IS NOT NULL ORDER BY name"
        )
    }
