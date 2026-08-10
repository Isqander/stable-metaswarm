-- Четыре DDL/projection-гейта P1-A: C-21 (минимум правок автора), C-23
-- (ровно один severity override finding'а в одном событии), C-09 (единый
-- event-order резолюций) и C-12 (human/non-human состояния Run).
--
-- Эти ограничения меняются одним schema-заходом, но сценарии разделены:
-- каждый негативный шаг нарушает ровно одно правило, а позитивные шаги
-- доказывают, что составной UNIQUE не стал шире требуемого.
--
-- Запуск: python3 scripts/checks/run-sql-check.py scripts/checks/schema-constraints.sql

PRAGMA foreign_keys = ON;

CREATE TABLE run (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  terminal_state      TEXT,
  cancel_requested_at INTEGER,
  pause_requested_at  INTEGER
);

CREATE TABLE branch (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  state  TEXT NOT NULL,
  UNIQUE (id, run_id)
);

CREATE TABLE stage_execution (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id               INTEGER NOT NULL,
  max_author_revisions INTEGER NOT NULL,
  UNIQUE (id, run_id),
  CHECK (max_author_revisions >= 1)
);

CREATE TABLE task (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  UNIQUE (id, run_id)
);

CREATE TABLE human_question (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  UNIQUE (id, run_id)
);

CREATE TABLE run_event (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  UNIQUE (id, run_id)
);

CREATE TABLE finding (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id INTEGER NOT NULL REFERENCES run_event(id)
);

CREATE TABLE finding_resolution (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  finding_id           INTEGER NOT NULL REFERENCES finding(id),
  resolution           TEXT NOT NULL,
  resolution_authority TEXT NOT NULL,
  event_id              INTEGER NOT NULL REFERENCES run_event(id),
  UNIQUE (finding_id, event_id)
);

CREATE TABLE finding_observation_link (
  observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
  finding_id     INTEGER NOT NULL REFERENCES finding(id),
  link_type      TEXT NOT NULL,
  event_id       INTEGER NOT NULL REFERENCES run_event(id)
);

CREATE TABLE blocker (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id           INTEGER NOT NULL REFERENCES run(id),
  kind             TEXT NOT NULL,
  branch_id        INTEGER,
  task_id          INTEGER,
  stage_id         INTEGER,
  question_id      INTEGER,
  created_event_id INTEGER NOT NULL,
  cleared_at       INTEGER,
  cleared_event_id INTEGER,
  FOREIGN KEY (branch_id, run_id)        REFERENCES branch(id, run_id),
  FOREIGN KEY (task_id, run_id)          REFERENCES task(id, run_id),
  FOREIGN KEY (stage_id, run_id)         REFERENCES stage_execution(id, run_id),
  FOREIGN KEY (question_id, run_id)      REFERENCES human_question(id, run_id),
  FOREIGN KEY (created_event_id, run_id) REFERENCES run_event(id, run_id),
  FOREIGN KEY (cleared_event_id, run_id) REFERENCES run_event(id, run_id)
);

CREATE TABLE severity_override (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  finding_id INTEGER NOT NULL REFERENCES finding(id),
  event_id   INTEGER NOT NULL REFERENCES run_event(id),
  UNIQUE (finding_id, event_id)
);

CREATE TRIGGER trg_severity_override_immutable
BEFORE UPDATE ON severity_override
BEGIN
  SELECT RAISE(ABORT, 'severity_override is append-only');
END;

CREATE TRIGGER trg_severity_override_no_delete
BEFORE DELETE ON severity_override
BEGIN
  SELECT RAISE(ABORT, 'severity_override is append-only');
END;

CREATE VIEW finding_status AS
SELECT f.id AS finding_id,
       CASE WHEN last_res.ev IS NULL              THEN 'open'
            WHEN last_open.ev > last_res.ev       THEN 'open'
            ELSE 'closed' END                     AS status,
       last_res.resolution                        AS last_resolution,
       last_res.resolution_authority              AS last_authority
  FROM finding f
  LEFT JOIN (
      SELECT r.finding_id, r.event_id AS ev, r.resolution, r.resolution_authority
        FROM finding_resolution r
       WHERE r.event_id = (SELECT MAX(event_id) FROM finding_resolution
                            WHERE finding_id = r.finding_id)
  ) last_res ON last_res.finding_id = f.id
  LEFT JOIN (
      SELECT finding_id, MAX(ev) AS ev FROM (
          SELECT id AS finding_id, event_id AS ev FROM finding
          UNION ALL
          SELECT finding_id, event_id FROM finding_observation_link
           WHERE link_type = 'reopening'
      ) GROUP BY finding_id
  ) last_open ON last_open.finding_id = f.id;

CREATE VIEW run_state AS
SELECT r.id AS run_id,
  CASE
    WHEN r.terminal_state IS NOT NULL              THEN r.terminal_state
    WHEN r.cancel_requested_at IS NOT NULL         THEN 'cancelling'
    WHEN r.pause_requested_at  IS NOT NULL         THEN 'paused'
    WHEN EXISTS (SELECT 1 FROM branch b
                  WHERE b.run_id = r.id
                    AND b.state IN ('ready','running','retry_wait'))
                                                   THEN 'running'
    WHEN EXISTS (
           SELECT 1 FROM blocker bl
            WHERE bl.run_id = r.id
              AND bl.cleared_at IS NULL
              AND bl.kind IN ('human_question', 'awaiting_continue')
         )                                         THEN 'waiting_human'
    WHEN EXISTS (
           SELECT 1 FROM blocker bl
            WHERE bl.run_id = r.id
              AND bl.cleared_at IS NULL
         )
                                                   THEN 'stalled'
    ELSE 'idle'
  END AS state
FROM run r;

-- === данные ===

INSERT INTO run(id, terminal_state, cancel_requested_at, pause_requested_at) VALUES
  (1, NULL, NULL, NULL),
  (2, NULL, NULL, NULL),
  (3, NULL, NULL, NULL),
  (4, NULL, NULL, NULL),
  (23, NULL, NULL, NULL),
  (24, NULL, NULL, NULL);

-- @step 01 C-21: минимальное разрешённое значение
-- @expect ok
INSERT INTO stage_execution(id, run_id, max_author_revisions) VALUES (1, 1, 1);

-- @step 02 C-21: штатное значение по умолчанию флоу
-- @expect ok
INSERT INTO stage_execution(id, run_id, max_author_revisions) VALUES (2, 2, 3);

-- @step 03 C-21: ноль не является режимом «без правок»
-- @expect error CHECK
INSERT INTO stage_execution(id, run_id, max_author_revisions) VALUES (3, 3, 0);

-- @step 04 C-21: отрицательный лимит также отвергается тем же CHECK
-- @expect error CHECK
INSERT INTO stage_execution(id, run_id, max_author_revisions) VALUES (4, 4, -1);

INSERT INTO run_event(id, run_id) VALUES
  (1, 1), (10, 1), (11, 1), (20, 1), (30, 1), (35, 1), (40, 1),
  (230, 23), (231, 23), (240, 24);
INSERT INTO finding(id, event_id) VALUES (1, 1), (2, 10);

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

-- @step 16 принятое решение нельзя переписать
-- @expect error append-only
UPDATE severity_override SET finding_id = 2, event_id = 11 WHERE id = 1;

-- @step 17 принятое решение нельзя удалить
-- @expect error append-only
DELETE FROM severity_override WHERE id = 1;

-- @step 18 REPLACE не обходит запрет через скрытый DELETE
-- @expect error append-only
INSERT OR REPLACE INTO severity_override(id, finding_id, event_id)
VALUES (1, 2, 11);

-- @step 19 после отказов сохранены те же три допустимые пары
-- @expect rows-json [[1,10],[1,11],[2,10]]
SELECT finding_id, event_id FROM severity_override ORDER BY finding_id, event_id;

-- === C-09: один канонический порядок резолюций ===

-- @step 20 первая резолюция закрывает finding
-- @expect ok
INSERT INTO finding_resolution(
  id, finding_id, resolution, resolution_authority, event_id
) VALUES (1, 1, 'accepted_reason', 'reviewer', 20);

-- @step 21 статус использует event-order
-- @expect rows-json [[1,"closed","accepted_reason"]]
SELECT finding_id, status, last_resolution FROM finding_status WHERE finding_id = 1;

INSERT INTO finding_observation_link(observation_id, finding_id, link_type, event_id)
VALUES (1, 1, 'reopening', 30);

-- @step 22 более позднее reopening снова открывает finding
-- @expect rows-json [[1,"open","accepted_reason"]]
SELECT finding_id, status, last_resolution FROM finding_status WHERE finding_id = 1;

INSERT INTO finding_resolution(
  id, finding_id, resolution, resolution_authority, event_id
) VALUES (2, 1, 'verified_fixed', 'reviewer', 40);

-- Поздно записанная историческая строка имеет меньший canonical event_id.
INSERT INTO finding_resolution(
  id, finding_id, resolution, resolution_authority, event_id
) VALUES (3, 1, 'policy_closed', 'policy', 35);

-- @step 23 физический порядок INSERT не затмевает событие 40
-- @expect rows-json [[1,"closed","verified_fixed"]]
SELECT finding_id, status, last_resolution FROM finding_status WHERE finding_id = 1;

-- @step 24 два решения одного finding в одном событии запрещены
-- @expect error UNIQUE
INSERT INTO finding_resolution(
  id, finding_id, resolution, resolution_authority, event_id
) VALUES (4, 1, 'human_decision', 'human', 40);

-- @step 25 одно событие законно решает несколько findings
-- @expect ok
INSERT INTO finding_resolution(
  id, finding_id, resolution, resolution_authority, event_id
) VALUES (5, 2, 'verified_fixed', 'reviewer', 40);

-- @step 26 локального seq больше нет
-- @expect rows-json [["id"],["finding_id"],["resolution"],["resolution_authority"],["event_id"]]
SELECT name FROM pragma_table_info('finding_resolution') ORDER BY cid;

-- === C-12: приоритеты агрегатного состояния Run ===

INSERT INTO run(id, terminal_state, cancel_requested_at, pause_requested_at) VALUES
  (10, NULL,        NULL, NULL),
  (11, NULL,        NULL, NULL),
  (12, NULL,        NULL, NULL),
  (13, NULL,        NULL, NULL),
  (14, NULL,        NULL, NULL),
  (15, NULL,        NULL, NULL),
  (16, NULL,        NULL, NULL),
  (17, NULL,        NULL, 100),
  (18, NULL,        100,  100),
  (19, 'succeeded', 100,  100),
  (20, NULL,        NULL, NULL),
  (21, NULL,        NULL, NULL),
  (22, NULL,        NULL, NULL);

INSERT INTO branch(id, run_id, state) VALUES
  (11, 11, 'ready'),
  (12, 12, 'running'),
  (13, 13, 'blocked'),
  (14, 14, 'blocked'),
  (15, 15, 'blocked'),
  (16, 16, 'blocked'),
  (17, 17, 'ready'),
  (18, 18, 'ready'),
  (19, 19, 'ready'),
  (20, 20, 'blocked'),
  (21, 21, 'blocked'),
  (22, 22, 'blocked');

INSERT INTO run_event(id, run_id) VALUES
  (1012, 12), (1013, 13), (1014, 14), (1015, 15), (1016, 16),
  (1017, 17), (1018, 18), (1019, 19), (1020, 20), (2020, 20),
  (1021, 21), (1022, 22);

INSERT INTO blocker(
  id, run_id, kind, created_event_id, cleared_at, cleared_event_id
) VALUES
  (1, 12, 'human_question',    1012, NULL, NULL),
  (2, 13, 'human_question',    1013, NULL, NULL),
  (3, 14, 'awaiting_continue', 1014, NULL, NULL),
  (4, 15, 'dependency',        1015, NULL, NULL),
  (5, 16, 'invalid_graph',     1016, NULL, NULL),
  (6, 16, 'human_question',    1016, NULL, NULL),
  (7, 17, 'drift',             1017, NULL, NULL),
  (8, 18, 'awaiting_continue', 1018, NULL, NULL),
  (9, 19, 'dependency',        1019, NULL, NULL),
  (10, 20, 'dependency',       1020, 200, 2020),
  (11, 21, 'drift',            1021, NULL, NULL),
  (12, 22, 'invalid_graph',    1022, NULL, NULL);

-- @step 30 без активности и blocker'ов Run честно idle
-- @expect rows-json [[10,"idle"]]
SELECT run_id, state FROM run_state WHERE run_id = 10;

-- @step 31 активная ветка выше любого ожидания
-- @expect rows-json [[11,"running"],[12,"running"]]
SELECT run_id, state FROM run_state WHERE run_id IN (11, 12) ORDER BY run_id;

-- @step 32 вопрос и ожидание continue — один human-агрегат
-- @expect rows-json [[13,"waiting_human"],[14,"waiting_human"]]
SELECT run_id, state FROM run_state WHERE run_id IN (13, 14) ORDER BY run_id;

-- @step 33 каждый нехумановый blocker даёт stalled
-- @expect rows-json [[15,"stalled"],[21,"stalled"],[22,"stalled"]]
SELECT run_id, state FROM run_state WHERE run_id IN (15, 21, 22) ORDER BY run_id;

-- @step 34 human blocker выше соседнего non-human
-- @expect rows-json [[16,"waiting_human"]]
SELECT run_id, state FROM run_state WHERE run_id = 16;

-- @step 35 paused выше active/blocker
-- @expect rows-json [[17,"paused"]]
SELECT run_id, state FROM run_state WHERE run_id = 17;

-- @step 36 cancelling выше paused/active/blocker
-- @expect rows-json [[18,"cancelling"]]
SELECT run_id, state FROM run_state WHERE run_id = 18;

-- @step 37 terminal выше всех производных состояний
-- @expect rows-json [[19,"succeeded"]]
SELECT run_id, state FROM run_state WHERE run_id = 19;

-- @step 38 очищенный blocker не превращает idle в stalled
-- @expect rows-json [[20,"idle"]]
SELECT run_id, state FROM run_state WHERE run_id = 20;

-- === C-12: blocker target обязан принадлежать blocker.run_id ===

INSERT INTO branch(id, run_id, state) VALUES (230, 23, 'blocked');
INSERT INTO stage_execution(id, run_id, max_author_revisions) VALUES (230, 23, 1);
INSERT INTO task(id, run_id) VALUES (230, 23);
INSERT INTO human_question(id, run_id) VALUES (230, 23);

-- @step 39 чужая ветка не делает run stalled
-- @expect error FOREIGN KEY
INSERT INTO blocker(id, run_id, kind, branch_id, created_event_id)
VALUES (20, 24, 'dependency', 230, 240);

-- @step 40 чужая задача не становится причиной run
-- @expect error FOREIGN KEY
INSERT INTO blocker(id, run_id, kind, task_id, created_event_id)
VALUES (20, 24, 'dependency', 230, 240);

-- @step 41 чужая стадия не становится причиной run
-- @expect error FOREIGN KEY
INSERT INTO blocker(id, run_id, kind, stage_id, created_event_id)
VALUES (20, 24, 'dependency', 230, 240);

-- @step 42 чужой вопрос не переводит run в waiting_human
-- @expect error FOREIGN KEY
INSERT INTO blocker(id, run_id, kind, question_id, created_event_id)
VALUES (20, 24, 'human_question', 230, 240);

-- @step 43 событие создания blocker — из того же run
-- @expect error FOREIGN KEY
INSERT INTO blocker(id, run_id, kind, created_event_id)
VALUES (20, 24, 'dependency', 230);

-- @step 44 событие снятия blocker — из того же run
-- @expect error FOREIGN KEY
INSERT INTO blocker(
  id, run_id, kind, created_event_id, cleared_at, cleared_event_id
) VALUES (20, 24, 'dependency', 240, 300, 231);

-- @step 45 согласованный blocker со всеми targets записывается
-- @expect ok
INSERT INTO blocker(
  id, run_id, kind, branch_id, task_id, stage_id, question_id,
  created_event_id
) VALUES (20, 23, 'dependency', 230, 230, 230, 230, 230);

-- @step 46 blocker влияет только на свой run
-- @expect rows-json [[23,"stalled"],[24,"idle"]]
SELECT run_id, state FROM run_state WHERE run_id IN (23, 24) ORDER BY run_id;

-- @step 47 все составные scope-FK целы
-- @expect empty
PRAGMA foreign_key_check;
