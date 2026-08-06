-- Открытие кампании, переходы её состояния и допуск ревьюера.
-- Проверяет конструкции db-schema.md §5.1, §5.2, §5.7, §11 и §14.24–25
-- (находки C-01a, C-01c и C-04 свода ревью P1-A). Родительские таблицы —
-- заглушки: важны констрейнты этих трёх мест, а не остальная схема.
--
-- Запуск: python3 scripts/checks/run-sql-check.py scripts/checks/campaign-open.sql
-- Ожидания машинные (`-- @expect`), несовпадение даёт код возврата 1.

PRAGMA foreign_keys = ON;

CREATE TABLE run_event (id INTEGER PRIMARY KEY AUTOINCREMENT);
CREATE TABLE run (id INTEGER PRIMARY KEY AUTOINCREMENT);
CREATE TABLE attempt_role (role TEXT PRIMARY KEY);
CREATE TABLE attempt_outcome (outcome TEXT PRIMARY KEY);
CREATE TABLE review_round_kind (kind TEXT PRIMARY KEY);
CREATE TABLE campaign_state (state TEXT PRIMARY KEY);

CREATE TABLE review_subject (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id   INTEGER NOT NULL REFERENCES run(id),
  revision TEXT    NOT NULL,
  UNIQUE (id, revision)
);

CREATE TABLE review_campaign (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id     INTEGER NOT NULL REFERENCES run(id),
  stage_id   INTEGER NOT NULL,
  subject_id INTEGER NOT NULL REFERENCES review_subject(id),
  ordinal    INTEGER NOT NULL,
  state      TEXT    NOT NULL REFERENCES campaign_state(state),
  closed_at  INTEGER,
  UNIQUE (stage_id, ordinal),
  UNIQUE (stage_id, id),
  UNIQUE (id, subject_id),
  CHECK (
    (state IN ('closed_clean', 'closed_escalated', 'closed_cancelled'))
    = (closed_at IS NOT NULL)
  )
);

CREATE TABLE campaign_transition (
  from_state TEXT NOT NULL REFERENCES campaign_state(state),
  to_state   TEXT NOT NULL REFERENCES campaign_state(state),
  PRIMARY KEY (from_state, to_state)
);

CREATE TRIGGER trg_campaign_state_transition
BEFORE UPDATE OF state ON review_campaign
BEGIN
  SELECT RAISE(ABORT, 'illegal campaign state transition')
  WHERE NOT EXISTS (
    SELECT 1 FROM campaign_transition t
     WHERE t.from_state = OLD.state AND t.to_state = NEW.state
  );
END;

CREATE TRIGGER trg_campaign_initial_state
BEFORE INSERT ON review_campaign
WHEN NEW.state <> 'discovery'
BEGIN
  SELECT RAISE(ABORT, 'campaign is created in discovery');
END;

CREATE TABLE review_round (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER NOT NULL REFERENCES review_campaign(id),
  round_no    INTEGER NOT NULL,
  kind        TEXT    NOT NULL REFERENCES review_round_kind(kind),
  UNIQUE (campaign_id, round_no),
  UNIQUE (campaign_id, id)
);

CREATE TABLE review_lane (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER NOT NULL REFERENCES review_campaign(id),
  lane_index  INTEGER NOT NULL,
  UNIQUE (campaign_id, lane_index),
  UNIQUE (campaign_id, id)
);

CREATE TABLE lane_assignment (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  lane_id     INTEGER NOT NULL REFERENCES review_lane(id),
  generation  INTEGER NOT NULL,
  profile_id  TEXT    NOT NULL,
  provider    TEXT    NOT NULL,
  model       TEXT    NOT NULL,
  assigned_at INTEGER NOT NULL,
  UNIQUE (lane_id, generation)
);

CREATE TABLE step_attempt (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id      INTEGER NOT NULL REFERENCES run(id),
  stage_id    INTEGER NOT NULL,
  role        TEXT    NOT NULL REFERENCES attempt_role(role),
  campaign_id INTEGER REFERENCES review_campaign(id),
  round_id    INTEGER REFERENCES review_round(id),
  lane_id     INTEGER REFERENCES review_lane(id),
  profile_id  TEXT    NOT NULL,
  outcome     TEXT REFERENCES attempt_outcome(outcome),
  FOREIGN KEY (stage_id, campaign_id) REFERENCES review_campaign(stage_id, id),
  FOREIGN KEY (campaign_id, round_id) REFERENCES review_round(campaign_id, id),
  FOREIGN KEY (campaign_id, lane_id)  REFERENCES review_lane(campaign_id, id)
);

CREATE UNIQUE INDEX ux_attempt_id_campaign ON step_attempt (id, campaign_id);

CREATE TABLE reviewer_exposure (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id           INTEGER NOT NULL REFERENCES run(id),
  subject_id       INTEGER NOT NULL REFERENCES review_subject(id),
  revision         TEXT    NOT NULL,
  provider         TEXT    NOT NULL,
  model            TEXT    NOT NULL,
  campaign_id      INTEGER NOT NULL REFERENCES review_campaign(id),
  first_attempt_id INTEGER NOT NULL REFERENCES step_attempt(id),
  created_at       INTEGER NOT NULL,
  UNIQUE (subject_id, revision, provider, model, campaign_id),
  FOREIGN KEY (campaign_id, subject_id)       REFERENCES review_campaign(id, subject_id),
  FOREIGN KEY (subject_id, revision)          REFERENCES review_subject(id, revision),
  FOREIGN KEY (first_attempt_id, campaign_id) REFERENCES step_attempt(id, campaign_id)
);

-- === данные ===

INSERT INTO attempt_role(role) VALUES ('author'),('reviewer'),('planner'),('reconciler');
INSERT INTO attempt_outcome(outcome) VALUES ('succeeded'),('failed'),('contract_error');
INSERT INTO review_round_kind(kind) VALUES ('discovery'),('fix_check');
INSERT INTO campaign_state(state) VALUES
  ('discovery'),('reconciliation'),('fix_cycle'),('closed_clean'),
  ('closed_escalated'),('closed_cancelled');
INSERT INTO campaign_transition(from_state, to_state) VALUES
  ('discovery',      'reconciliation'),
  ('discovery',      'closed_cancelled'),
  ('reconciliation', 'fix_cycle'),
  ('reconciliation', 'closed_clean'),
  ('reconciliation', 'closed_cancelled'),
  ('fix_cycle',      'fix_cycle'),
  ('fix_cycle',      'closed_clean'),
  ('fix_cycle',      'closed_escalated'),
  ('fix_cycle',      'closed_cancelled');
INSERT INTO run DEFAULT VALUES;
INSERT INTO run_event DEFAULT VALUES;
INSERT INTO review_subject(id, run_id, revision) VALUES (1, 1, 'sha-1'), (2, 1, 'sha-2');

-- @step 01 кампания рождается в discovery
-- @expect ok
INSERT INTO review_campaign(id, run_id, stage_id, subject_id, ordinal, state)
VALUES (1, 1, 7, 1, 1, 'discovery');

-- @step 02 кампанию нельзя создать сразу в fix_cycle
-- @expect error created in discovery
INSERT INTO review_campaign(id, run_id, stage_id, subject_id, ordinal, state)
VALUES (99, 1, 7, 1, 9, 'fix_cycle');

-- @step 03 повторное открытие той же кампании стадии
-- @expect error review_campaign.stage_id
INSERT INTO review_campaign(id, run_id, stage_id, subject_id, ordinal, state)
VALUES (98, 1, 7, 1, 1, 'discovery');

-- Открытие продолжается: слоты, исполнители, круг 1.
INSERT INTO review_lane(id, campaign_id, lane_index) VALUES (1, 1, 0), (2, 1, 1);
INSERT INTO lane_assignment(id, lane_id, generation, profile_id, provider, model, assigned_at)
VALUES (1, 1, 1, 'p-a', 'anthropic', 'opus', 100),
       (2, 2, 1, 'p-b', 'openai',    'gpt',  100);
INSERT INTO review_round(id, campaign_id, round_no, kind) VALUES (1, 1, 1, 'discovery');

-- @step 04 открытие завершено: запрос незавершённости пуст
-- @expect empty
SELECT c.id FROM review_campaign c
 WHERE c.closed_at IS NULL
   AND (NOT EXISTS (SELECT 1 FROM review_lane l WHERE l.campaign_id = c.id)
        OR NOT EXISTS (SELECT 1 FROM review_round r
                        WHERE r.campaign_id = c.id
                          AND r.round_no = 1 AND r.kind = 'discovery')
        OR EXISTS (SELECT 1 FROM review_lane l
                    WHERE l.campaign_id = c.id
                      AND NOT EXISTS (SELECT 1 FROM lane_assignment a
                                       WHERE a.lane_id = l.id)));

-- Вторая кампания открыта наполовину: слот без исполнителя и без круга.
INSERT INTO review_campaign(id, run_id, stage_id, subject_id, ordinal, state)
VALUES (2, 1, 8, 2, 1, 'discovery');
INSERT INTO review_lane(id, campaign_id, lane_index) VALUES (3, 2, 0);

-- @step 05 незавершённое открытие видно recovery audit
-- @expect rows-json [[2]]
SELECT c.id FROM review_campaign c
 WHERE c.closed_at IS NULL
   AND (NOT EXISTS (SELECT 1 FROM review_lane l WHERE l.campaign_id = c.id)
        OR NOT EXISTS (SELECT 1 FROM review_round r
                        WHERE r.campaign_id = c.id
                          AND r.round_no = 1 AND r.kind = 'discovery')
        OR EXISTS (SELECT 1 FROM review_lane l
                    WHERE l.campaign_id = c.id
                      AND NOT EXISTS (SELECT 1 FROM lane_assignment a
                                       WHERE a.lane_id = l.id)));

-- @step 06 прыжок discovery -> fix_cycle в обход reconciliation
-- @expect error illegal campaign state transition
UPDATE review_campaign SET state = 'fix_cycle' WHERE id = 1;

-- @step 07 штатное завершение слепой фазы
-- @expect ok
UPDATE review_campaign SET state = 'reconciliation' WHERE id = 1;

-- @step 08 повтор перехода после коммита отвергается состоянием
-- @expect error illegal campaign state transition
UPDATE review_campaign SET state = 'reconciliation' WHERE id = 1;

-- @step 09 reconciliation -> fix_cycle
-- @expect ok
UPDATE review_campaign SET state = 'fix_cycle' WHERE id = 1;

-- @step 10 self-transition fix_cycle разрешён (ещё одна правка, human gate)
-- @expect ok
UPDATE review_campaign SET state = 'fix_cycle' WHERE id = 1;

-- @step 11 закрытие кампании требует closed_at
-- @expect error CHECK
UPDATE review_campaign SET state = 'closed_clean' WHERE id = 1;

-- @step 12 закрытие с отметкой времени
-- @expect ok
UPDATE review_campaign SET state = 'closed_clean', closed_at = 500 WHERE id = 1;

-- @step 13 терминальное состояние исходящих переходов не имеет
-- @expect error illegal campaign state transition
UPDATE review_campaign SET state = 'fix_cycle', closed_at = NULL WHERE id = 1;

-- Кампания 2 доводится до рабочего вида: исполнитель и круг.
INSERT INTO lane_assignment(id, lane_id, generation, profile_id, provider, model, assigned_at)
VALUES (3, 3, 1, 'p-a', 'anthropic', 'opus', 100);
INSERT INTO review_round(id, campaign_id, round_no, kind) VALUES (2, 2, 1, 'discovery');

-- Линия 0 кампании 2 получает вход: сначала попытка, затем допуск.
INSERT INTO step_attempt(id, run_id, stage_id, role, campaign_id, round_id, lane_id, profile_id, outcome)
VALUES (1, 1, 8, 'reviewer', 2, 2, 3, 'p-a', NULL);

-- @step 14 допуск ссылается на попытку — обратный порядок невозможен
-- @expect ok
INSERT INTO reviewer_exposure(run_id, subject_id, revision, provider, model, campaign_id, first_attempt_id, created_at)
VALUES (1, 2, 'sha-2', 'anthropic', 'opus', 2, 1, 600);

-- @step 15 допуск на несуществующую попытку
-- @expect error FOREIGN KEY
INSERT INTO reviewer_exposure(run_id, subject_id, revision, provider, model, campaign_id, first_attempt_id, created_at)
VALUES (1, 2, 'sha-2', 'openai', 'gpt', 2, 404, 600);

-- @step 16 допуск с чужой ревизией предмета
-- @expect error FOREIGN KEY
INSERT INTO reviewer_exposure(run_id, subject_id, revision, provider, model, campaign_id, first_attempt_id, created_at)
VALUES (1, 2, 'sha-1', 'openai', 'gpt', 2, 1, 600);

-- @step 17 допуск на предмет, которого кампания не проверяет
-- @expect error FOREIGN KEY
INSERT INTO reviewer_exposure(run_id, subject_id, revision, provider, model, campaign_id, first_attempt_id, created_at)
VALUES (1, 1, 'sha-1', 'openai', 'gpt', 2, 1, 600);

-- Попытка кампании 1 — она понадобится следующему шагу как чужая.
INSERT INTO step_attempt(id, run_id, stage_id, role, campaign_id, round_id, lane_id, profile_id, outcome)
VALUES (2, 1, 7, 'reviewer', 1, 1, 1, 'p-b', NULL);

-- @step 18 попытка допуска принадлежит другой кампании
-- @expect error FOREIGN KEY
INSERT INTO reviewer_exposure(run_id, subject_id, revision, provider, model, campaign_id, first_attempt_id, created_at)
VALUES (1, 2, 'sha-2', 'openai', 'gpt', 2, 2, 600);

-- Штатный путь 1: reconciler наследует профиль линии 0 — та же пара.
INSERT INTO step_attempt(id, run_id, stage_id, role, campaign_id, round_id, lane_id, profile_id, outcome)
VALUES (3, 1, 8, 'reconciler', 2, 2, NULL, 'p-a', NULL);

-- @step 19 reconciler с парой линии 0: второй membership не создаётся
-- @expect error reviewer_exposure.subject_id
INSERT INTO reviewer_exposure(run_id, subject_id, revision, provider, model, campaign_id, first_attempt_id, created_at)
VALUES (1, 2, 'sha-2', 'anthropic', 'opus', 2, 3, 600);

-- Штатный путь 2: retry после contract_error той же парой.
UPDATE step_attempt SET outcome = 'contract_error' WHERE id = 1;
INSERT INTO step_attempt(id, run_id, stage_id, role, campaign_id, round_id, lane_id, profile_id, outcome)
VALUES (4, 1, 8, 'reviewer', 2, 2, 3, 'p-a', NULL);

-- @step 20 retry той же парой: строка допуска по-прежнему одна
-- @expect rows-json [[1, 1]]
SELECT COUNT(*), MIN(first_attempt_id) FROM reviewer_exposure
 WHERE subject_id = 2 AND revision = 'sha-2'
   AND provider = 'anthropic' AND model = 'opus' AND campaign_id = 2;

-- @step 21 та же пара в другой кампании — свой допуск, конфликта нет
-- @expect ok
INSERT INTO step_attempt(id, run_id, stage_id, role, campaign_id, round_id, lane_id, profile_id, outcome)
VALUES (5, 1, 7, 'reviewer', 1, 1, 1, 'p-a', NULL);
INSERT INTO reviewer_exposure(run_id, subject_id, revision, provider, model, campaign_id, first_attempt_id, created_at)
VALUES (1, 1, 'sha-1', 'anthropic', 'opus', 1, 5, 700);

-- @step 22 свежесть: пара, уже видевшая эту ревизию предмета
-- @expect rows-json [["anthropic", "opus"]]
SELECT provider, model FROM reviewer_exposure
 WHERE subject_id = 2 AND revision = 'sha-2';

-- @step 23 foreign_key_check
-- @expect empty
PRAGMA foreign_key_check;

-- @step 24 объекты: 1 новая таблица переходов и 2 триггера кампании
-- @expect rows-json [["table", 1], ["trigger", 2]]
SELECT type, COUNT(*) FROM sqlite_master
 WHERE name IN ('campaign_transition',
                'trg_campaign_state_transition', 'trg_campaign_initial_state')
 GROUP BY type ORDER BY type;
