-- Два оставшихся DDL-гейта P1-A: C-21 (минимум правок автора) и C-23
-- (ровно один severity override finding'а в одном событии).
--
-- Эти ограничения меняются одним schema-заходом, но сценарии разделены:
-- каждый негативный шаг нарушает ровно одно правило, а позитивные шаги
-- доказывают, что составной UNIQUE не стал шире требуемого.
--
-- Запуск: python3 scripts/checks/run-sql-check.py scripts/checks/schema-constraints.sql

PRAGMA foreign_keys = ON;

CREATE TABLE stage_execution (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  max_author_revisions INTEGER NOT NULL,
  CHECK (max_author_revisions >= 1)
);

CREATE TABLE finding (
  id INTEGER PRIMARY KEY AUTOINCREMENT
);

CREATE TABLE run_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT
);

CREATE TABLE severity_override (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  finding_id INTEGER NOT NULL REFERENCES finding(id),
  event_id   INTEGER NOT NULL REFERENCES run_event(id),
  UNIQUE (finding_id, event_id)
);

-- === данные ===

-- @step 01 C-21: минимальное разрешённое значение
-- @expect ok
INSERT INTO stage_execution(id, max_author_revisions) VALUES (1, 1);

-- @step 02 C-21: штатное значение по умолчанию флоу
-- @expect ok
INSERT INTO stage_execution(id, max_author_revisions) VALUES (2, 3);

-- @step 03 C-21: ноль не является режимом «без правок»
-- @expect error CHECK
INSERT INTO stage_execution(id, max_author_revisions) VALUES (3, 0);

-- @step 04 C-21: отрицательный лимит также отвергается тем же CHECK
-- @expect error CHECK
INSERT INTO stage_execution(id, max_author_revisions) VALUES (4, -1);

INSERT INTO finding(id) VALUES (1), (2);
INSERT INTO run_event(id) VALUES (10), (11);

-- @step 10 C-23: первый override finding'а в событии
-- @expect ok
INSERT INTO severity_override(id, finding_id, event_id) VALUES (1, 1, 10);

-- @step 11 C-23: второй override той же пары не раздваивает view
-- @expect error UNIQUE
INSERT INTO severity_override(id, finding_id, event_id) VALUES (2, 1, 10);

-- @step 12 C-23: тот же finding можно изменить другим событием
-- @expect ok
INSERT INTO severity_override(id, finding_id, event_id) VALUES (3, 1, 11);

-- @step 13 C-23: одно событие может менять разные findings
-- @expect ok
INSERT INTO severity_override(id, finding_id, event_id) VALUES (4, 2, 10);

-- @step 14 C-23: сохранены ровно три допустимые пары
-- @expect rows-json [[1,10],[1,11],[2,10]]
SELECT finding_id, event_id FROM severity_override ORDER BY finding_id, event_id;

-- @step 15 связи с родителями целы
-- @expect empty
PRAGMA foreign_key_check;
