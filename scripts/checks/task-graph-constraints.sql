-- Базовая половина design-инварианта 23: semantic task ID уникален внутри
-- импорта и среди активных версий; dependency не является self-edge и не
-- дублируется. Проверка цикла остаётся коду той же транзакции.
--
-- Запуск: python3 scripts/checks/run-sql-check.py scripts/checks/task-graph-constraints.sql

PRAGMA foreign_keys = ON;

CREATE TABLE task_graph_import (
  id INTEGER PRIMARY KEY AUTOINCREMENT
);

CREATE TABLE task (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id           INTEGER NOT NULL,
  semantic_task_id TEXT    NOT NULL,
  import_id        INTEGER NOT NULL REFERENCES task_graph_import(id),
  state            TEXT    NOT NULL,
  UNIQUE (import_id, semantic_task_id)
);

CREATE UNIQUE INDEX ux_task_active_semantic
  ON task (run_id, semantic_task_id) WHERE state <> 'invalidated';

CREATE TABLE task_dependency (
  parent_task_id INTEGER NOT NULL REFERENCES task(id),
  child_task_id  INTEGER NOT NULL REFERENCES task(id),
  PRIMARY KEY (parent_task_id, child_task_id),
  CHECK (parent_task_id <> child_task_id)
);

-- === данные ===

INSERT INTO task_graph_import(id) VALUES (1), (2);

-- @step 01 историческая invalidated-версия допустима
-- @expect ok
INSERT INTO task(id, run_id, semantic_task_id, import_id, state)
VALUES (1, 1, 'T-old', 1, 'invalidated');

-- @step 02 один semantic ID нельзя повторить внутри импорта даже в истории
-- @expect error UNIQUE
INSERT INTO task(id, run_id, semantic_task_id, import_id, state)
VALUES (2, 1, 'T-old', 1, 'invalidated');

-- @step 03 первая активная версия
-- @expect ok
INSERT INTO task(id, run_id, semantic_task_id, import_id, state)
VALUES (3, 1, 'T-live', 1, 'pending');

-- @step 04 второй импорт не создаёт вторую активную версию того же ID
-- @expect error UNIQUE
INSERT INTO task(id, run_id, semantic_task_id, import_id, state)
VALUES (4, 1, 'T-live', 2, 'ready');

-- @step 05 invalidated-история того же ID сосуществует с активной версией
-- @expect ok
INSERT INTO task(id, run_id, semantic_task_id, import_id, state)
VALUES (5, 1, 'T-live', 2, 'invalidated');

-- @step 06 self-edge запрещён отдельно от проверки цикла
-- @expect error CHECK
INSERT INTO task_dependency(parent_task_id, child_task_id) VALUES (3, 3);

-- @step 07 обычная зависимость допустима
-- @expect ok
INSERT INTO task_dependency(parent_task_id, child_task_id) VALUES (1, 3);

-- @step 08 дубль ребра запрещён составным PRIMARY KEY
-- @expect error UNIQUE
INSERT INTO task_dependency(parent_task_id, child_task_id) VALUES (1, 3);

-- @step 09 после отказов остались три задачи и одно ребро
-- @expect rows-json [[3,1]]
SELECT (SELECT COUNT(*) FROM task), (SELECT COUNT(*) FROM task_dependency);

-- @step 10 связи с родителями целы
-- @expect empty
PRAGMA foreign_key_check;
