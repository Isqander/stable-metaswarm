-- Effective roster: слот кворума и сменный исполнитель.
-- Проверяет конструкции db-schema.md §5.2 и §4 (находка C-01b свода ревью
-- P1-A). Родительские таблицы взяты заглушками — здесь важны констрейнты
-- самой модели, а не остальная схема.
--
-- Запуск: python3 scripts/checks/run-sql-check.py scripts/checks/roster-model.sql
-- Ожидания машинные (`-- @expect`), несовпадение даёт код возврата 1.
-- Прогонялось на SQLite 3.45.

PRAGMA foreign_keys = ON;

-- Заглушки родителей, только нужные колонки.
CREATE TABLE run_event (id INTEGER PRIMARY KEY AUTOINCREMENT);
CREATE TABLE human_answer (id INTEGER PRIMARY KEY AUTOINCREMENT);
CREATE TABLE logical_session (id INTEGER PRIMARY KEY AUTOINCREMENT);
CREATE TABLE author_revision (id INTEGER PRIMARY KEY AUTOINCREMENT);
CREATE TABLE attempt_role (role TEXT PRIMARY KEY);
CREATE TABLE attempt_outcome (outcome TEXT PRIMARY KEY);
CREATE TABLE review_round_kind (kind TEXT PRIMARY KEY);

CREATE TABLE review_campaign (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  stage_id INTEGER NOT NULL,
  UNIQUE (stage_id, id)
);

CREATE TABLE review_round (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER NOT NULL REFERENCES review_campaign(id),
  round_no    INTEGER NOT NULL,
  kind        TEXT    NOT NULL REFERENCES review_round_kind(kind),
  preceding_revision_id INTEGER REFERENCES author_revision(id),
  result      TEXT,
  UNIQUE (campaign_id, round_no),
  UNIQUE (campaign_id, id),
  CHECK ((kind = 'discovery') = (preceding_revision_id IS NULL))
);

CREATE TABLE review_lane (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER NOT NULL REFERENCES review_campaign(id),
  lane_index  INTEGER NOT NULL,
  UNIQUE (campaign_id, lane_index),
  UNIQUE (campaign_id, id)
);

CREATE TRIGGER trg_lane_immutable
BEFORE UPDATE ON review_lane
BEGIN
  SELECT RAISE(ABORT, 'review_lane is immutable');
END;

CREATE TRIGGER trg_lane_no_delete
BEFORE DELETE ON review_lane
BEGIN
  SELECT RAISE(ABORT, 'review_lane rows are never deleted');
END;

CREATE TABLE lane_assignment (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  lane_id         INTEGER NOT NULL REFERENCES review_lane(id),
  generation      INTEGER NOT NULL,
  profile_id      TEXT    NOT NULL,
  provider        TEXT    NOT NULL,
  model           TEXT    NOT NULL,
  replaces_id     INTEGER UNIQUE REFERENCES lane_assignment(id),
  session_id      INTEGER REFERENCES logical_session(id),
  human_answer_id INTEGER UNIQUE REFERENCES human_answer(id),
  event_id        INTEGER NOT NULL REFERENCES run_event(id),
  assigned_at     INTEGER NOT NULL,
  UNIQUE (lane_id, generation),
  CHECK ((generation = 1) = (replaces_id IS NULL)),
  CHECK ((generation = 1) = (human_answer_id IS NULL))
);

CREATE UNIQUE INDEX ux_lane_assignment_id_lane ON lane_assignment (id, lane_id);

CREATE TRIGGER trg_lane_assignment_chain
BEFORE INSERT ON lane_assignment
WHEN NEW.replaces_id IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'replaces_id must be previous generation of same lane')
  WHERE NOT EXISTS (
    SELECT 1 FROM lane_assignment p
     WHERE p.id = NEW.replaces_id
       AND p.lane_id = NEW.lane_id
       AND p.generation = NEW.generation - 1
  );
END;

CREATE TRIGGER trg_lane_assignment_immutable
BEFORE UPDATE ON lane_assignment
WHEN NEW.lane_id         <> OLD.lane_id
  OR NEW.generation      <> OLD.generation
  OR NEW.profile_id      <> OLD.profile_id
  OR NEW.provider        <> OLD.provider
  OR NEW.model           <> OLD.model
  OR NEW.assigned_at     <> OLD.assigned_at
  OR NEW.event_id        <> OLD.event_id
  OR NEW.replaces_id     IS NOT OLD.replaces_id
  OR NEW.human_answer_id IS NOT OLD.human_answer_id
  OR OLD.session_id      IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'lane_assignment is immutable except session binding');
END;

CREATE TRIGGER trg_lane_assignment_no_delete
BEFORE DELETE ON lane_assignment
BEGIN
  SELECT RAISE(ABORT, 'lane_assignment rows are never deleted');
END;

CREATE TABLE lane_waiver (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id     INTEGER NOT NULL REFERENCES review_campaign(id),
  round_no        INTEGER NOT NULL,
  lane_id         INTEGER NOT NULL REFERENCES review_lane(id),
  human_answer_id INTEGER NOT NULL UNIQUE REFERENCES human_answer(id),
  event_id        INTEGER NOT NULL REFERENCES run_event(id),
  created_at      INTEGER NOT NULL,
  UNIQUE (campaign_id, round_no, lane_id),
  FOREIGN KEY (campaign_id, round_no) REFERENCES review_round(campaign_id, round_no),
  FOREIGN KEY (campaign_id, lane_id)  REFERENCES review_lane(campaign_id, id)
);

CREATE TRIGGER trg_lane_waiver_immutable
BEFORE UPDATE ON lane_waiver
BEGIN
  SELECT RAISE(ABORT, 'lane_waiver is immutable');
END;

CREATE TRIGGER trg_lane_waiver_no_delete
BEFORE DELETE ON lane_waiver
BEGIN
  SELECT RAISE(ABORT, 'lane_waiver rows are never deleted');
END;

CREATE TABLE step_attempt (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  stage_id           INTEGER NOT NULL,
  role               TEXT    NOT NULL REFERENCES attempt_role(role),
  campaign_id        INTEGER REFERENCES review_campaign(id),
  round_id           INTEGER REFERENCES review_round(id),
  lane_id            INTEGER REFERENCES review_lane(id),
  lane_assignment_id INTEGER REFERENCES lane_assignment(id),
  outcome            TEXT REFERENCES attempt_outcome(outcome),
  FOREIGN KEY (stage_id, campaign_id)       REFERENCES review_campaign(stage_id, id),
  FOREIGN KEY (campaign_id, round_id)       REFERENCES review_round(campaign_id, id),
  FOREIGN KEY (campaign_id, lane_id)        REFERENCES review_lane(campaign_id, id),
  FOREIGN KEY (lane_assignment_id, lane_id) REFERENCES lane_assignment(id, lane_id),
  CHECK ((lane_id IS NULL) = (lane_assignment_id IS NULL)),
  CHECK ((campaign_id IS NULL) = (round_id IS NULL)),
  CHECK (lane_id IS NULL OR round_id IS NOT NULL),
  CHECK (role <> 'reviewer'   OR (round_id IS NOT NULL AND lane_id IS NOT NULL)),
  CHECK (role <> 'reconciler' OR round_id IS NOT NULL),
  CHECK (role NOT IN ('author', 'planner')
         OR (campaign_id IS NULL AND round_id IS NULL
             AND lane_id IS NULL AND lane_assignment_id IS NULL))
);

CREATE UNIQUE INDEX ux_attempt_active
  ON step_attempt (stage_id, role, COALESCE(lane_id, -1))
  WHERE outcome IS NULL;

CREATE INDEX ix_attempt_by_round ON step_attempt (round_id, lane_id, role);

CREATE VIEW effective_roster AS
SELECT l.campaign_id, l.id AS lane_id, l.lane_index,
       a.id AS assignment_id, a.generation,
       a.profile_id, a.provider, a.model
  FROM review_lane l
  JOIN lane_assignment a ON a.lane_id = l.id
 WHERE NOT EXISTS (SELECT 1 FROM lane_assignment s WHERE s.replaces_id = a.id);

-- === данные ===

INSERT INTO attempt_role(role) VALUES ('author'),('reviewer'),('planner'),('reconciler');
INSERT INTO attempt_outcome(outcome) VALUES ('succeeded'),('failed'),('interrupted');
INSERT INTO review_round_kind(kind) VALUES ('discovery'),('fix_check');
INSERT INTO run_event DEFAULT VALUES;
INSERT INTO author_revision DEFAULT VALUES;
INSERT INTO logical_session DEFAULT VALUES;
INSERT INTO human_answer(id) VALUES (1),(2),(3),(4),(5),(6),(7);

-- Кампания 1 на стадии 7: два слота, круг discovery и круг fix_check.
-- Кампания 2 на стадии 8: два слота, только discovery.
INSERT INTO review_campaign(id, stage_id) VALUES (1, 7), (2, 8);
INSERT INTO review_round(id, campaign_id, round_no, kind, preceding_revision_id) VALUES
  (1, 1, 1, 'discovery', NULL),
  (2, 1, 2, 'fix_check', 1),
  (3, 2, 1, 'discovery', NULL);
INSERT INTO review_lane(id, campaign_id, lane_index) VALUES
  (1, 1, 0), (2, 1, 1), (3, 2, 0), (4, 2, 1);
INSERT INTO lane_assignment(id, lane_id, generation, profile_id, provider, model, event_id, assigned_at)
VALUES (1, 1, 1, 'p-a', 'anthropic', 'opus', 1, 100),
       (2, 2, 1, 'p-b', 'openai',    'gpt',  1, 100),
       (3, 3, 1, 'p-c', 'google',    'gem',  1, 100),
       (4, 4, 1, 'p-d', 'zai',       'glm',  1, 100);

-- @step 01 roster кампании 1 после набора
-- @expect rows=2
SELECT lane_index, profile_id, generation FROM effective_roster
 WHERE campaign_id = 1 ORDER BY lane_index;

-- Контрпример C-01b: слот 0 вернул [] и завершился, слот 1 не завершился.
INSERT INTO step_attempt(stage_id, role, campaign_id, round_id, lane_id, lane_assignment_id, outcome)
VALUES (7, 'reviewer', 1, 1, 1, 1, 'succeeded');
INSERT INTO step_attempt(stage_id, role, campaign_id, round_id, lane_id, lane_assignment_id, outcome)
VALUES (7, 'reviewer', 1, 1, 2, 2, NULL);

-- @step 02 lane-participation: незавершённая линия держит круг discovery
-- @expect rows=1
SELECT l.id FROM review_round rr
  JOIN review_lane l ON l.campaign_id = rr.campaign_id
 WHERE rr.id = 1 AND rr.kind = 'discovery'
   AND NOT EXISTS (SELECT 1 FROM lane_waiver w
                    WHERE w.campaign_id = rr.campaign_id AND w.round_no = rr.round_no
                      AND w.lane_id = l.id)
   AND NOT EXISTS (SELECT 1 FROM step_attempt a
                    WHERE a.round_id = rr.id AND a.lane_id = l.id
                      AND a.role = 'reviewer' AND a.outcome = 'succeeded');

-- @step 03 вторая активная попытка того же слота
-- @expect error ux_attempt_active
INSERT INTO step_attempt(stage_id, role, campaign_id, round_id, lane_id, lane_assignment_id, outcome)
VALUES (7, 'reviewer', 1, 1, 2, 2, NULL);

-- Линия исчерпала бюджет.
UPDATE step_attempt SET outcome = 'failed' WHERE lane_id = 2 AND outcome IS NULL;

-- @step 04 замена без ответа человека
-- @expect error human_answer_id
INSERT INTO lane_assignment(lane_id, generation, profile_id, provider, model, replaces_id, event_id, assigned_at)
VALUES (2, 2, 'p-e', 'minimax', 'm2', 2, 1, 200);

-- @step 05 замена с разрывом цепочки поколений
-- @expect error previous generation
INSERT INTO lane_assignment(lane_id, generation, profile_id, provider, model, replaces_id, human_answer_id, event_id, assigned_at)
VALUES (2, 3, 'p-e', 'minimax', 'm2', 2, 1, 1, 200);

-- @step 06 замена ссылается на назначение чужого слота
-- @expect error previous generation
INSERT INTO lane_assignment(lane_id, generation, profile_id, provider, model, replaces_id, human_answer_id, event_id, assigned_at)
VALUES (1, 2, 'p-e', 'minimax', 'm2', 2, 1, 1, 200);

-- @step 07 корректная замена по ответу человека 1
-- @expect ok
INSERT INTO lane_assignment(id, lane_id, generation, profile_id, provider, model, replaces_id, human_answer_id, event_id, assigned_at)
VALUES (5, 2, 2, 'p-e', 'minimax', 'm2', 2, 1, 1, 200);

-- @step 08 roster после замены: размер прежний, исполнитель новый
-- @expect rows=2
SELECT lane_index, profile_id, generation FROM effective_roster
 WHERE campaign_id = 1 ORDER BY lane_index;

-- @step 09 идемпотентность: тот же ответ человека второй замены не даёт
-- @expect error human_answer_id
INSERT INTO lane_assignment(lane_id, generation, profile_id, provider, model, replaces_id, human_answer_id, event_id, assigned_at)
VALUES (2, 3, 'p-f', 'openai', 'o4', 5, 1, 1, 200);

-- @step 10 повтор операции после падения не создаёт третье поколение
-- @expect error generation
INSERT INTO lane_assignment(lane_id, generation, profile_id, provider, model, replaces_id, human_answer_id, event_id, assigned_at)
VALUES (2, 2, 'p-f', 'openai', 'o4', 2, 2, 1, 200);

-- @step 11 UPDATE прежнего назначения
-- @expect error immutable
UPDATE lane_assignment SET model = 'other' WHERE id = 2;

-- @step 12 DELETE назначения: цепочка поколений не переписывается удалением
-- @expect error never deleted
DELETE FROM lane_assignment WHERE id = 5;

-- @step 13 привязка логической сессии к назначению
-- @expect ok
UPDATE lane_assignment SET session_id = 1 WHERE id = 5;

-- @step 14 повторная привязка сессии
-- @expect error immutable
UPDATE lane_assignment SET session_id = 1 WHERE id = 5;

-- @step 15 UPDATE слота
-- @expect error immutable
UPDATE review_lane SET campaign_id = 2 WHERE id = 1;

-- @step 16 DELETE слота
-- @expect error never deleted
DELETE FROM review_lane WHERE id = 1;

-- @step 17 попытка: своё поколение под чужим слотом
-- @expect error FOREIGN KEY
INSERT INTO step_attempt(stage_id, role, campaign_id, round_id, lane_id, lane_assignment_id, outcome)
VALUES (7, 'reviewer', 1, 1, 1, 5, NULL);

-- @step 18 попытка: круг кампании 1 и слот кампании 2
-- @expect error FOREIGN KEY
INSERT INTO step_attempt(stage_id, role, campaign_id, round_id, lane_id, lane_assignment_id, outcome)
VALUES (7, 'reviewer', 1, 1, 3, 3, NULL);

-- @step 19 попытка: стадия не та, что у кампании
-- @expect error FOREIGN KEY
INSERT INTO step_attempt(stage_id, role, campaign_id, round_id, lane_id, lane_assignment_id, outcome)
VALUES (8, 'reviewer', 1, 1, 1, 1, NULL);

-- @step 20 попытка ревьюера без круга
-- @expect error CHECK
INSERT INTO step_attempt(stage_id, role, campaign_id, round_id, lane_id, lane_assignment_id, outcome)
VALUES (7, 'reviewer', NULL, NULL, 1, 1, NULL);

-- @step 21 попытка ревьюера без линии
-- @expect error CHECK
INSERT INTO step_attempt(stage_id, role, campaign_id, round_id, lane_id, lane_assignment_id, outcome)
VALUES (7, 'reviewer', 1, 1, NULL, NULL, NULL);

-- @step 22 авторская попытка с review-координатами
-- @expect error CHECK
INSERT INTO step_attempt(stage_id, role, campaign_id, round_id, lane_id, lane_assignment_id, outcome)
VALUES (7, 'author', 1, 1, 1, 1, NULL);

-- @step 23 авторская попытка без review-координат
-- @expect ok
INSERT INTO step_attempt(stage_id, role, campaign_id, round_id, lane_id, lane_assignment_id, outcome)
VALUES (7, 'author', NULL, NULL, NULL, NULL, NULL);

-- Новый исполнитель слота 1 отработал круг discovery.
INSERT INTO step_attempt(stage_id, role, campaign_id, round_id, lane_id, lane_assignment_id, outcome)
VALUES (7, 'reviewer', 1, 1, 2, 5, 'succeeded');

-- @step 24 lane-participation: после работы заменившего исполнителя гейт пуст
-- @expect empty
SELECT l.id FROM review_round rr
  JOIN review_lane l ON l.campaign_id = rr.campaign_id
 WHERE rr.id = 1 AND rr.kind = 'discovery'
   AND NOT EXISTS (SELECT 1 FROM lane_waiver w
                    WHERE w.campaign_id = rr.campaign_id AND w.round_no = rr.round_no
                      AND w.lane_id = l.id)
   AND NOT EXISTS (SELECT 1 FROM step_attempt a
                    WHERE a.round_id = rr.id AND a.lane_id = l.id
                      AND a.role = 'reviewer' AND a.outcome = 'succeeded');

-- @step 25 в fix_check участия всех линий не требуется: отвечают владельцы
-- @expect empty
SELECT l.id FROM review_round rr
  JOIN review_lane l ON l.campaign_id = rr.campaign_id
 WHERE rr.id = 2 AND rr.kind = 'discovery'
   AND NOT EXISTS (SELECT 1 FROM lane_waiver w
                    WHERE w.campaign_id = rr.campaign_id AND w.round_no = rr.round_no
                      AND w.lane_id = l.id)
   AND NOT EXISTS (SELECT 1 FROM step_attempt a
                    WHERE a.round_id = rr.id AND a.lane_id = l.id
                      AND a.role = 'reviewer' AND a.outcome = 'succeeded');

-- @step 25b тот же запрос без фильтра по kind закрыть fix_check не дал бы
-- @expect rows=2
SELECT l.id FROM review_round rr
  JOIN review_lane l ON l.campaign_id = rr.campaign_id
 WHERE rr.id = 2
   AND NOT EXISTS (SELECT 1 FROM lane_waiver w
                    WHERE w.campaign_id = rr.campaign_id AND w.round_no = rr.round_no
                      AND w.lane_id = l.id)
   AND NOT EXISTS (SELECT 1 FROM step_attempt a
                    WHERE a.round_id = rr.id AND a.lane_id = l.id
                      AND a.role = 'reviewer' AND a.outcome = 'succeeded');

-- Кампания 2: слот 3 отработал, слот 4 отпущен waiver'ом (ответ 2).
INSERT INTO step_attempt(stage_id, role, campaign_id, round_id, lane_id, lane_assignment_id, outcome)
VALUES (8, 'reviewer', 2, 3, 3, 3, 'succeeded');
INSERT INTO lane_waiver(campaign_id, round_no, lane_id, human_answer_id, event_id, created_at)
VALUES (2, 1, 4, 2, 1, 300);

-- @step 26 lane-participation: waiver заменяет работу линии
-- @expect empty
SELECT l.id FROM review_round rr
  JOIN review_lane l ON l.campaign_id = rr.campaign_id
 WHERE rr.id = 3 AND rr.kind = 'discovery'
   AND NOT EXISTS (SELECT 1 FROM lane_waiver w
                    WHERE w.campaign_id = rr.campaign_id AND w.round_no = rr.round_no
                      AND w.lane_id = l.id)
   AND NOT EXISTS (SELECT 1 FROM step_attempt a
                    WHERE a.round_id = rr.id AND a.lane_id = l.id
                      AND a.role = 'reviewer' AND a.outcome = 'succeeded');

-- @step 27 минимум одного мнения: в кампании 2 оно есть
-- @expect empty
SELECT 1 AS violation
 WHERE (SELECT COUNT(*) FROM step_attempt a
         WHERE a.round_id = 3 AND a.role = 'reviewer' AND a.outcome = 'succeeded') = 0;

-- @step 28 waiver кампании 2 на слот кампании 1
-- @expect error FOREIGN KEY
INSERT INTO lane_waiver(campaign_id, round_no, lane_id, human_answer_id, event_id, created_at)
VALUES (2, 1, 1, 3, 1, 300);

-- @step 29 waiver на несуществующий круг
-- @expect error FOREIGN KEY
INSERT INTO lane_waiver(campaign_id, round_no, lane_id, human_answer_id, event_id, created_at)
VALUES (2, 9, 4, 3, 1, 300);

-- @step 30 идемпотентность: тот же ответ второго waiver не даёт
-- @expect error human_answer_id
INSERT INTO lane_waiver(campaign_id, round_no, lane_id, human_answer_id, event_id, created_at)
VALUES (2, 1, 3, 2, 1, 300);

-- @step 31 повторный waiver того же слота другим ответом
-- @expect error lane_waiver.campaign_id
INSERT INTO lane_waiver(campaign_id, round_no, lane_id, human_answer_id, event_id, created_at)
VALUES (2, 1, 4, 3, 1, 300);

-- @step 32 UPDATE waiver
-- @expect error immutable
UPDATE lane_waiver SET lane_id = 3 WHERE campaign_id = 2;

-- @step 33 DELETE waiver: закрытый круг не становится задним числом неполным
-- @expect error never deleted
DELETE FROM lane_waiver WHERE campaign_id = 2;

-- Худший случай: waiver выписан и второй линии кампании 2 (ответ 4),
-- то есть кворум понижен полностью.
INSERT INTO lane_waiver(campaign_id, round_no, lane_id, human_answer_id, event_id, created_at)
VALUES (2, 1, 3, 4, 1, 300);
UPDATE step_attempt SET outcome = 'failed'
 WHERE round_id = 3 AND role = 'reviewer' AND outcome = 'succeeded';

-- @step 34 lane-participation пропускает круг вообще без мнений
-- @expect empty
SELECT l.id FROM review_round rr
  JOIN review_lane l ON l.campaign_id = rr.campaign_id
 WHERE rr.id = 3 AND rr.kind = 'discovery'
   AND NOT EXISTS (SELECT 1 FROM lane_waiver w
                    WHERE w.campaign_id = rr.campaign_id AND w.round_no = rr.round_no
                      AND w.lane_id = l.id)
   AND NOT EXISTS (SELECT 1 FROM step_attempt a
                    WHERE a.round_id = rr.id AND a.lane_id = l.id
                      AND a.role = 'reviewer' AND a.outcome = 'succeeded');

-- @step 35 и потому нужен отдельный запрос: он такой круг ловит
-- @expect rows=1
SELECT 1 AS violation
 WHERE (SELECT COUNT(*) FROM step_attempt a
         WHERE a.round_id = 3 AND a.role = 'reviewer' AND a.outcome = 'succeeded') = 0;

-- @step 36 foreign_key_check
-- @expect empty
PRAGMA foreign_key_check;

-- @step 37 инвентарь добавленного: 3 таблицы, 2 индекса, 1 представление, 7 триггеров
-- @expect rows=4
SELECT type, COUNT(*) FROM sqlite_master
 WHERE name IN ('review_lane', 'lane_assignment', 'lane_waiver', 'effective_roster',
                'trg_lane_immutable', 'trg_lane_no_delete',
                'trg_lane_assignment_chain', 'trg_lane_assignment_immutable',
                'trg_lane_assignment_no_delete',
                'trg_lane_waiver_immutable', 'trg_lane_waiver_no_delete',
                'ux_lane_assignment_id_lane', 'ix_attempt_by_round')
 GROUP BY type ORDER BY type;
