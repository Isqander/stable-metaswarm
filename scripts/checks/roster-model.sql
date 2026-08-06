-- Effective roster: слот кворума и сменный исполнитель.
-- Проверяет конструкции db-schema.md §5.2 и §4 (находка C-01b свода ревью
-- P1-A). Родительские таблицы взяты заглушками — здесь важны констрейнты
-- самой модели, а не остальная схема.
--
-- Запуск: python3 scripts/checks/run-sql-check.py scripts/checks/roster-model.sql
-- Ожидания записаны в подписях шагов; прогонялось на SQLite 3.45.

PRAGMA foreign_keys = ON;

-- Заглушки родителей, только нужные колонки.
CREATE TABLE run_event (id INTEGER PRIMARY KEY AUTOINCREMENT);
CREATE TABLE human_answer (id INTEGER PRIMARY KEY AUTOINCREMENT);
CREATE TABLE logical_session (id INTEGER PRIMARY KEY AUTOINCREMENT);
CREATE TABLE review_campaign (id INTEGER PRIMARY KEY AUTOINCREMENT);
CREATE TABLE attempt_role (role TEXT PRIMARY KEY);
CREATE TABLE attempt_outcome (outcome TEXT PRIMARY KEY);

CREATE TABLE review_round (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER NOT NULL REFERENCES review_campaign(id),
  round_no    INTEGER NOT NULL,
  result      TEXT,
  UNIQUE (campaign_id, round_no)
);

CREATE TABLE review_lane (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER NOT NULL REFERENCES review_campaign(id),
  lane_index  INTEGER NOT NULL,
  UNIQUE (campaign_id, lane_index)
);

CREATE TABLE lane_assignment (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  lane_id         INTEGER NOT NULL REFERENCES review_lane(id),
  generation      INTEGER NOT NULL,
  profile_id      TEXT    NOT NULL,
  provider        TEXT    NOT NULL,
  model           TEXT    NOT NULL,
  replaces_id     INTEGER UNIQUE REFERENCES lane_assignment(id),
  session_id      INTEGER REFERENCES logical_session(id),
  human_answer_id INTEGER REFERENCES human_answer(id),
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

CREATE TABLE lane_waiver (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id     INTEGER NOT NULL REFERENCES review_campaign(id),
  round_no        INTEGER NOT NULL,
  lane_id         INTEGER NOT NULL REFERENCES review_lane(id),
  human_answer_id INTEGER NOT NULL REFERENCES human_answer(id),
  event_id        INTEGER NOT NULL REFERENCES run_event(id),
  created_at      INTEGER NOT NULL,
  UNIQUE (campaign_id, round_no, lane_id),
  FOREIGN KEY (campaign_id, round_no) REFERENCES review_round(campaign_id, round_no)
);

CREATE TABLE step_attempt (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  stage_id           INTEGER NOT NULL,
  role               TEXT    NOT NULL REFERENCES attempt_role(role),
  round_id           INTEGER REFERENCES review_round(id),
  lane_id            INTEGER REFERENCES review_lane(id),
  lane_assignment_id INTEGER REFERENCES lane_assignment(id),
  outcome            TEXT REFERENCES attempt_outcome(outcome),
  FOREIGN KEY (lane_assignment_id, lane_id) REFERENCES lane_assignment(id, lane_id),
  CHECK ((lane_id IS NULL) = (lane_assignment_id IS NULL)),
  CHECK (role NOT IN ('reviewer', 'reconciler') OR round_id IS NOT NULL)
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
INSERT INTO run_event DEFAULT VALUES;         -- 1
INSERT INTO run_event DEFAULT VALUES;         -- 2
INSERT INTO human_answer DEFAULT VALUES;      -- 1
INSERT INTO logical_session DEFAULT VALUES;   -- 1
INSERT INTO review_campaign DEFAULT VALUES;   -- 1
INSERT INTO review_round(campaign_id, round_no) VALUES (1, 1);   -- id 1
INSERT INTO review_lane(campaign_id, lane_index) VALUES (1,0),(1,1);  -- слоты 1,2
INSERT INTO lane_assignment(lane_id, generation, profile_id, provider, model, event_id, assigned_at)
VALUES (1,1,'p-a','anthropic','opus',1,100),
       (2,1,'p-b','openai','gpt',1,100);

SELECT '01 roster после набора (ждём 2 строки):';
SELECT lane_index, profile_id, generation FROM effective_roster ORDER BY lane_index;

-- Контрпример C-01b: слот 0 вернул [] и завершился, слот 1 не завершился.
INSERT INTO step_attempt(stage_id, role, round_id, lane_id, lane_assignment_id, outcome)
VALUES (7,'reviewer',1,1,1,'succeeded');
INSERT INTO step_attempt(stage_id, role, round_id, lane_id, lane_assignment_id, outcome)
VALUES (7,'reviewer',1,2,2,NULL);

SELECT '02 roster-гейт (ждём слот 2 = нарушение):';
SELECT l.id FROM review_lane l
 WHERE l.campaign_id = 1
   AND NOT EXISTS (SELECT 1 FROM lane_waiver w WHERE w.campaign_id=1 AND w.round_no=1 AND w.lane_id=l.id)
   AND NOT EXISTS (SELECT 1 FROM step_attempt a WHERE a.round_id=1 AND a.lane_id=l.id
                     AND a.role='reviewer' AND a.outcome='succeeded');

SELECT '03 вторая активная попытка того же слота (ждём ошибку UNIQUE):';
INSERT INTO step_attempt(stage_id, role, round_id, lane_id, lane_assignment_id, outcome)
VALUES (7,'reviewer',1,2,2,NULL);

-- Линия исчерпала бюджет.
UPDATE step_attempt SET outcome='failed' WHERE lane_id=2 AND outcome IS NULL;

SELECT '04 замена без ответа человека (ждём ошибку CHECK):';
INSERT INTO lane_assignment(lane_id, generation, profile_id, provider, model, replaces_id, event_id, assigned_at)
VALUES (2,2,'p-c','google','gemini',2,2,200);

SELECT '05 замена с разрывом цепочки (ждём ошибку триггера):';
INSERT INTO lane_assignment(lane_id, generation, profile_id, provider, model, replaces_id, human_answer_id, event_id, assigned_at)
VALUES (2,3,'p-c','google','gemini',2,1,2,200);

SELECT '06 замена чужого слота (ждём ошибку триггера):';
INSERT INTO lane_assignment(lane_id, generation, profile_id, provider, model, replaces_id, human_answer_id, event_id, assigned_at)
VALUES (1,2,'p-c','google','gemini',2,1,2,200);

SELECT '07 корректная замена (ждём тишину):';
INSERT INTO lane_assignment(lane_id, generation, profile_id, provider, model, replaces_id, human_answer_id, event_id, assigned_at)
VALUES (2,2,'p-c','google','gemini',2,1,2,200);

SELECT '08 roster после замены (ждём 2 строки, слот 1 -> p-c gen 2):';
SELECT lane_index, profile_id, generation FROM effective_roster ORDER BY lane_index;

SELECT '09 повтор замены после падения (ждём ошибку UNIQUE):';
INSERT INTO lane_assignment(lane_id, generation, profile_id, provider, model, replaces_id, human_answer_id, event_id, assigned_at)
VALUES (2,2,'p-d','google','gemini',2,1,2,200);

SELECT '10 второе поколение поверх того же заменённого (ждём ошибку UNIQUE replaces_id):';
INSERT INTO lane_assignment(lane_id, generation, profile_id, provider, model, replaces_id, human_answer_id, event_id, assigned_at)
VALUES (2,3,'p-d','google','gemini',2,1,2,200);

SELECT '11 UPDATE прежнего назначения (ждём ошибку триггера):';
UPDATE lane_assignment SET model='other' WHERE id=2;

SELECT '12 привязка сессии к назначению (ждём тишину):';
UPDATE lane_assignment SET session_id=1 WHERE id=3;

SELECT '13 повторная привязка сессии (ждём ошибку триггера):';
UPDATE lane_assignment SET session_id=1 WHERE id=3;

SELECT '14 попытка с чужим слотом при своём поколении (ждём ошибку FK):';
INSERT INTO step_attempt(stage_id, role, round_id, lane_id, lane_assignment_id, outcome)
VALUES (7,'reviewer',1,1,3,NULL);

SELECT '15 попытка ревьюера без круга (ждём ошибку CHECK):';
INSERT INTO step_attempt(stage_id, role, round_id, lane_id, lane_assignment_id, outcome)
VALUES (7,'reviewer',NULL,1,1,NULL);

SELECT '16 авторская попытка без круга и линии (ждём тишину):';
INSERT INTO step_attempt(stage_id, role, round_id, lane_id, lane_assignment_id, outcome)
VALUES (7,'author',NULL,NULL,NULL,NULL);

SELECT '17 новое поколение отработало (ждём пустой гейт):';
INSERT INTO step_attempt(stage_id, role, round_id, lane_id, lane_assignment_id, outcome)
VALUES (7,'reviewer',1,2,3,'succeeded');
SELECT l.id FROM review_lane l
 WHERE l.campaign_id = 1
   AND NOT EXISTS (SELECT 1 FROM lane_waiver w WHERE w.campaign_id=1 AND w.round_no=1 AND w.lane_id=l.id)
   AND NOT EXISTS (SELECT 1 FROM step_attempt a WHERE a.round_id=1 AND a.lane_id=l.id
                     AND a.role='reviewer' AND a.outcome='succeeded');

SELECT '18 waiver вместо работы слота (ждём пустой гейт для круга 2):';
INSERT INTO review_round(campaign_id, round_no) VALUES (1, 2);
INSERT INTO step_attempt(stage_id, role, round_id, lane_id, lane_assignment_id, outcome)
VALUES (7,'reviewer',2,1,1,'succeeded');
INSERT INTO lane_waiver(campaign_id, round_no, lane_id, human_answer_id, event_id, created_at)
VALUES (1,2,2,1,2,300);
SELECT l.id FROM review_lane l
 WHERE l.campaign_id = 1
   AND NOT EXISTS (SELECT 1 FROM lane_waiver w WHERE w.campaign_id=1 AND w.round_no=2 AND w.lane_id=l.id)
   AND NOT EXISTS (SELECT 1 FROM step_attempt a WHERE a.round_id=2 AND a.lane_id=l.id
                     AND a.role='reviewer' AND a.outcome='succeeded');

SELECT '19 waiver на несуществующий круг (ждём ошибку FK):';
INSERT INTO lane_waiver(campaign_id, round_no, lane_id, human_answer_id, event_id, created_at)
VALUES (1,9,2,1,2,300);

SELECT '20 повторный waiver (ждём ошибку UNIQUE):';
INSERT INTO lane_waiver(campaign_id, round_no, lane_id, human_answer_id, event_id, created_at)
VALUES (1,2,2,1,2,300);

SELECT '21 foreign_key_check (ждём тишину):';
PRAGMA foreign_key_check;

SELECT '22 инвентарь добавленного:';
SELECT type, COUNT(*) FROM sqlite_master
 WHERE name IN ('review_lane','lane_assignment','lane_waiver','effective_roster',
                'trg_lane_assignment_chain','trg_lane_assignment_immutable',
                'ux_lane_assignment_id_lane','ix_attempt_by_round')
 GROUP BY type;
