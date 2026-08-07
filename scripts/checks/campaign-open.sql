-- Открытие кампании, переходы её состояния и допуск ревьюера.
-- Проверяет конструкции db-schema.md §5.1, §5.2, §5.7, §11 и §14.24–25
-- (находки C-01a, C-01c и C-04 свода ревью P1-A). Родительские таблицы —
-- заглушки: важны констрейнты этих трёх мест, а не остальная схема.
--
-- Что прогон доказывает: констрейнты, триггеры и cardinality — то есть какие
-- строки база принимает, а какие нет. Чего он НЕ доказывает: атомарность
-- «переход + событие», поведение при падении между операциями и контракт
-- reserve_reviewer_exposure() — это предмет тестов T1.2 и T1.7b, где есть
-- транзакции и код операций, а не одиночные statement'ы.
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
  public_id  TEXT    NOT NULL UNIQUE,
  run_id     INTEGER NOT NULL REFERENCES run(id),
  stage_id   INTEGER NOT NULL,
  subject_id INTEGER NOT NULL REFERENCES review_subject(id),
  ordinal    INTEGER NOT NULL,
  severity_threshold TEXT NOT NULL,
  policy_version     TEXT NOT NULL,
  expected_lane_count INTEGER NOT NULL,
  state      TEXT    NOT NULL REFERENCES campaign_state(state),
  opened_at  INTEGER NOT NULL,
  closed_at  INTEGER,
  UNIQUE (stage_id, ordinal),
  UNIQUE (stage_id, id),
  UNIQUE (id, subject_id),
  UNIQUE (id, run_id),
  CHECK (expected_lane_count >= 1),
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

CREATE TRIGGER trg_campaign_snapshot_immutable
BEFORE UPDATE ON review_campaign
WHEN NEW.run_id              <> OLD.run_id
  OR NEW.stage_id            <> OLD.stage_id
  OR NEW.subject_id          <> OLD.subject_id
  OR NEW.ordinal             <> OLD.ordinal
  OR NEW.severity_threshold  <> OLD.severity_threshold
  OR NEW.policy_version      <> OLD.policy_version
  OR NEW.expected_lane_count <> OLD.expected_lane_count
  OR NEW.opened_at           <> OLD.opened_at
  OR NEW.public_id           <> OLD.public_id
BEGIN
  SELECT RAISE(ABORT, 'campaign identity and snapshot are immutable');
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
  run_id      INTEGER NOT NULL REFERENCES run(id),
  lane_index  INTEGER NOT NULL,
  UNIQUE (campaign_id, lane_index),
  UNIQUE (campaign_id, id),
  UNIQUE (id, run_id),
  FOREIGN KEY (campaign_id, run_id) REFERENCES review_campaign(id, run_id)
);

CREATE TRIGGER trg_lane_index_bounds
BEFORE INSERT ON review_lane
BEGIN
  SELECT RAISE(ABORT, 'lane_index outside declared quorum')
  WHERE NEW.lane_index < 0
     OR NEW.lane_index >= (SELECT c.expected_lane_count
                             FROM review_campaign c WHERE c.id = NEW.campaign_id);
END;

CREATE TABLE lane_assignment (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  lane_id     INTEGER NOT NULL REFERENCES review_lane(id),
  run_id      INTEGER NOT NULL REFERENCES run(id),
  generation  INTEGER NOT NULL,
  profile_id  TEXT    NOT NULL,
  assigned_at INTEGER NOT NULL,
  UNIQUE (lane_id, generation),
  UNIQUE (id, lane_id, profile_id),
  FOREIGN KEY (lane_id, run_id)    REFERENCES review_lane(id, run_id),
  FOREIGN KEY (run_id, profile_id) REFERENCES run_profile_resolution(run_id, profile_id)
);

CREATE TABLE step_attempt (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id      INTEGER NOT NULL REFERENCES run(id),
  stage_id    INTEGER NOT NULL,
  role        TEXT    NOT NULL REFERENCES attempt_role(role),
  campaign_id INTEGER REFERENCES review_campaign(id),
  round_id    INTEGER REFERENCES review_round(id),
  lane_id     INTEGER REFERENCES review_lane(id),
  lane_assignment_id INTEGER REFERENCES lane_assignment(id),
  profile_id  TEXT    NOT NULL,
  subject_revision TEXT,
  outcome     TEXT REFERENCES attempt_outcome(outcome),
  FOREIGN KEY (stage_id, campaign_id) REFERENCES review_campaign(stage_id, id),
  FOREIGN KEY (campaign_id, round_id) REFERENCES review_round(campaign_id, id),
  FOREIGN KEY (campaign_id, lane_id)  REFERENCES review_lane(campaign_id, id),
  FOREIGN KEY (lane_assignment_id, lane_id, profile_id)
      REFERENCES lane_assignment(id, lane_id, profile_id),
  CHECK (role NOT IN ('reviewer', 'reconciler') OR subject_revision IS NOT NULL)
);

CREATE UNIQUE INDEX ux_attempt_id_campaign ON step_attempt (id, campaign_id);
CREATE UNIQUE INDEX ux_attempt_id_run      ON step_attempt (id, run_id);
CREATE UNIQUE INDEX ux_attempt_id_profile  ON step_attempt (id, profile_id);
CREATE UNIQUE INDEX ux_attempt_id_revision ON step_attempt (id, subject_revision);

CREATE TABLE run_profile_resolution (
  run_id      INTEGER NOT NULL REFERENCES run(id),
  profile_id  TEXT    NOT NULL,
  provider    TEXT    NOT NULL,
  model       TEXT    NOT NULL,
  resolved_at INTEGER NOT NULL,
  PRIMARY KEY (run_id, profile_id),
  UNIQUE (run_id, profile_id, provider, model)
);

CREATE TRIGGER trg_profile_resolution_immutable
BEFORE UPDATE ON run_profile_resolution
BEGIN
  SELECT RAISE(ABORT, 'run_profile_resolution is immutable');
END;

CREATE TRIGGER trg_profile_resolution_no_delete
BEFORE DELETE ON run_profile_resolution
BEGIN
  SELECT RAISE(ABORT, 'run_profile_resolution rows are never deleted');
END;

CREATE TABLE reviewer_exposure (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id           INTEGER NOT NULL REFERENCES run(id),
  subject_id       INTEGER NOT NULL REFERENCES review_subject(id),
  revision         TEXT    NOT NULL,
  provider         TEXT    NOT NULL,
  model            TEXT    NOT NULL,
  campaign_id      INTEGER NOT NULL REFERENCES review_campaign(id),
  first_attempt_id INTEGER NOT NULL REFERENCES step_attempt(id),
  profile_id       TEXT    NOT NULL,
  created_at       INTEGER NOT NULL,
  UNIQUE (subject_id, revision, provider, model, campaign_id),
  FOREIGN KEY (campaign_id, subject_id)       REFERENCES review_campaign(id, subject_id),
  FOREIGN KEY (first_attempt_id, revision)    REFERENCES step_attempt(id, subject_revision),
  FOREIGN KEY (first_attempt_id, campaign_id) REFERENCES step_attempt(id, campaign_id),
  FOREIGN KEY (first_attempt_id, run_id)      REFERENCES step_attempt(id, run_id),
  FOREIGN KEY (first_attempt_id, profile_id)  REFERENCES step_attempt(id, profile_id),
  FOREIGN KEY (run_id, profile_id, provider, model)
      REFERENCES run_profile_resolution(run_id, profile_id, provider, model)
);

CREATE TRIGGER trg_exposure_immutable
BEFORE UPDATE ON reviewer_exposure
BEGIN
  SELECT RAISE(ABORT, 'reviewer_exposure is immutable');
END;

CREATE TRIGGER trg_exposure_no_delete
BEFORE DELETE ON reviewer_exposure
BEGIN
  SELECT RAISE(ABORT, 'reviewer_exposure rows are never deleted');
END;

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
INSERT INTO run_profile_resolution(run_id, profile_id, provider, model, resolved_at) VALUES
  (1, 'p-a', 'anthropic', 'opus', 10),
  (1, 'p-b', 'openai',    'gpt',  10);

-- @step 01 кампания рождается в discovery
-- @expect ok
INSERT INTO review_campaign(id, public_id, run_id, stage_id, subject_id, ordinal,
                            severity_threshold, policy_version,
                            expected_lane_count, state, opened_at)
VALUES (1, 'C-1', 1, 7, 1, 1, 'high', 'v1', 2, 'discovery', 50);

-- @step 02 кампанию нельзя создать сразу в fix_cycle
-- @expect error created in discovery
INSERT INTO review_campaign(id, public_id, run_id, stage_id, subject_id, ordinal,
                            severity_threshold, policy_version,
                            expected_lane_count, state, opened_at)
VALUES (99, 'C-99', 1, 7, 1, 9, 'high', 'v1', 2, 'fix_cycle', 50);

-- @step 03 дубль (stage, ordinal) отвергается; идемпотентность — на операции
-- @expect error review_campaign.stage_id
INSERT INTO review_campaign(id, public_id, run_id, stage_id, subject_id, ordinal,
                            severity_threshold, policy_version,
                            expected_lane_count, state, opened_at)
VALUES (98, 'C-98', 1, 7, 1, 1, 'high', 'v1', 2, 'discovery', 50);

-- @step 04 кворум из нуля линий
-- @expect error expected_lane_count
INSERT INTO review_campaign(id, public_id, run_id, stage_id, subject_id, ordinal,
                            severity_threshold, policy_version,
                            expected_lane_count, state, opened_at)
VALUES (97, 'C-97', 1, 7, 1, 8, 'high', 'v1', 0, 'discovery', 50);

INSERT INTO review_lane(id, campaign_id, run_id, lane_index) VALUES (1, 1, 1, 0), (2, 1, 1, 1);

-- @step 05 слот с индексом за пределами заявленного кворума
-- @expect error outside declared quorum
INSERT INTO review_lane(id, campaign_id, run_id, lane_index) VALUES (96, 1, 1, 2);

-- @step 06 слот с отрицательным индексом
-- @expect error outside declared quorum
INSERT INTO review_lane(id, campaign_id, run_id, lane_index) VALUES (95, 1, 1, -1);

INSERT INTO lane_assignment(id, lane_id, run_id, generation, profile_id, assigned_at)
VALUES (1, 1, 1, 1, 'p-a', 100),
       (2, 2, 1, 1, 'p-b', 100);
INSERT INTO review_round(id, campaign_id, round_no, kind) VALUES (1, 1, 1, 'discovery');

-- @step 07 открытие завершено: запрос незавершённости пуст
-- @expect empty
SELECT c.id FROM review_campaign c
 WHERE c.closed_at IS NULL
   AND (NOT EXISTS (SELECT 1 FROM review_round r
                     WHERE r.campaign_id = c.id
                       AND r.round_no = 1 AND r.kind = 'discovery')
        OR (SELECT COUNT(*) FROM review_lane l
             WHERE l.campaign_id = c.id) <> c.expected_lane_count
        OR EXISTS (SELECT 1 FROM review_lane l
                    WHERE l.campaign_id = c.id
                      AND NOT EXISTS (SELECT 1 FROM lane_assignment a
                                       WHERE a.lane_id = l.id)));

-- Кампания 2 оборвалась на первой линии: заявлено две, записана одна.
INSERT INTO review_campaign(id, public_id, run_id, stage_id, subject_id, ordinal,
                            severity_threshold, policy_version,
                            expected_lane_count, state, opened_at)
VALUES (2, 'C-2', 1, 8, 2, 1, 'high', 'v1', 2, 'discovery', 50);
INSERT INTO review_lane(id, campaign_id, run_id, lane_index) VALUES (3, 2, 1, 0);
INSERT INTO lane_assignment(id, lane_id, run_id, generation, profile_id, assigned_at)
VALUES (3, 3, 1, 1, 'p-a', 100);
INSERT INTO review_round(id, campaign_id, round_no, kind) VALUES (2, 2, 1, 'discovery');

-- @step 08 неполный roster виден recovery audit, хотя каждый слот оформлен
-- @expect rows-json [[2]]
SELECT c.id FROM review_campaign c
 WHERE c.closed_at IS NULL
   AND (NOT EXISTS (SELECT 1 FROM review_round r
                     WHERE r.campaign_id = c.id
                       AND r.round_no = 1 AND r.kind = 'discovery')
        OR (SELECT COUNT(*) FROM review_lane l
             WHERE l.campaign_id = c.id) <> c.expected_lane_count
        OR EXISTS (SELECT 1 FROM review_lane l
                    WHERE l.campaign_id = c.id
                      AND NOT EXISTS (SELECT 1 FROM lane_assignment a
                                       WHERE a.lane_id = l.id)));

-- @step 09 recovery-сверка состава ловит тот же недобор.
-- Нормативный третий запрос гейта считает effective_roster, а не слоты
-- (db-schema.md §5.2); его форма проверена в roster-model.sql, шаг 47 —
-- здесь нет представления, потому что стаб не моделирует вытеснение поколений.
-- @expect rows-json [[2]]
SELECT rr.campaign_id FROM review_round rr
  JOIN review_campaign c ON c.id = rr.campaign_id
 WHERE rr.id = 2 AND rr.kind = 'discovery'
   AND (SELECT COUNT(*) FROM review_lane l
         WHERE l.campaign_id = c.id) <> c.expected_lane_count;

-- Кампания 2 дооткрыта: второй слот с исполнителем.
INSERT INTO review_lane(id, campaign_id, run_id, lane_index) VALUES (4, 2, 1, 1);
INSERT INTO lane_assignment(id, lane_id, run_id, generation, profile_id, assigned_at)
VALUES (4, 4, 1, 1, 'p-b', 100);

-- @step 10 после дооткрытия гейт состава пуст
-- @expect empty
SELECT rr.campaign_id FROM review_round rr
  JOIN review_campaign c ON c.id = rr.campaign_id
 WHERE rr.id = 2 AND rr.kind = 'discovery'
   AND (SELECT COUNT(*) FROM review_lane l
         WHERE l.campaign_id = c.id) <> c.expected_lane_count;

-- Кампания 3: слот есть, исполнителя нет.
INSERT INTO review_campaign(id, public_id, run_id, stage_id, subject_id, ordinal,
                            severity_threshold, policy_version,
                            expected_lane_count, state, opened_at)
VALUES (3, 'C-3', 1, 9, 1, 1, 'high', 'v1', 1, 'discovery', 50);
INSERT INTO review_lane(id, campaign_id, run_id, lane_index) VALUES (5, 3, 1, 0);
INSERT INTO review_round(id, campaign_id, round_no, kind) VALUES (3, 3, 1, 'discovery');

-- @step 11 слот без исполнителя тоже считается незавершённым открытием
-- @expect rows-json [[3]]
SELECT c.id FROM review_campaign c
 WHERE c.closed_at IS NULL
   AND EXISTS (SELECT 1 FROM review_lane l
                WHERE l.campaign_id = c.id
                  AND NOT EXISTS (SELECT 1 FROM lane_assignment a
                                   WHERE a.lane_id = l.id));

-- @step 11a заявленный кворум нельзя подогнать под фактический roster
-- @expect error identity and snapshot are immutable
UPDATE review_campaign SET expected_lane_count = 1 WHERE id = 2;

-- @step 11b снимок порога тоже неизменяем
-- @expect error identity and snapshot are immutable
UPDATE review_campaign SET severity_threshold = 'low' WHERE id = 1;

-- @step 12 прыжок discovery -> fix_cycle в обход reconciliation
-- @expect error illegal campaign state transition
UPDATE review_campaign SET state = 'fix_cycle' WHERE id = 1;

-- @step 13 штатное завершение слепой фазы
-- @expect ok
UPDATE review_campaign SET state = 'reconciliation' WHERE id = 1;

-- @step 14 повтор перехода после коммита отвергается состоянием
-- @expect error illegal campaign state transition
UPDATE review_campaign SET state = 'reconciliation' WHERE id = 1;

-- @step 15 reconciliation -> fix_cycle
-- @expect ok
UPDATE review_campaign SET state = 'fix_cycle' WHERE id = 1;

-- @step 16 self-transition fix_cycle разрешён (ещё одна правка, human gate)
-- @expect ok
UPDATE review_campaign SET state = 'fix_cycle' WHERE id = 1;

-- @step 17 закрытие кампании требует closed_at
-- @expect error CHECK
UPDATE review_campaign SET state = 'closed_clean' WHERE id = 1;

-- @step 18 закрытие с отметкой времени
-- @expect ok
UPDATE review_campaign SET state = 'closed_clean', closed_at = 500 WHERE id = 1;

-- @step 19 терминальное состояние исходящих переходов не имеет
-- @expect error illegal campaign state transition
UPDATE review_campaign SET state = 'fix_cycle', closed_at = NULL WHERE id = 1;

-- @step 20 матрица переходов совпадает с таблицей T1.4 целиком
-- @expect rows-json [["discovery", "closed_cancelled"], ["discovery", "reconciliation"], ["fix_cycle", "closed_cancelled"], ["fix_cycle", "closed_clean"], ["fix_cycle", "closed_escalated"], ["fix_cycle", "fix_cycle"], ["reconciliation", "closed_cancelled"], ["reconciliation", "closed_clean"], ["reconciliation", "fix_cycle"]]
SELECT from_state, to_state FROM campaign_transition ORDER BY from_state, to_state;

-- Линия 0 кампании 2 получает вход: сначала попытка, затем допуск.
INSERT INTO step_attempt(id, run_id, stage_id, role, campaign_id, round_id, lane_id,
                         lane_assignment_id, profile_id, subject_revision, outcome)
VALUES (1, 1, 8, 'reviewer', 2, 2, 3, 3, 'p-a', 'sha-2', NULL);

-- @step 21 допуск ссылается на попытку — обратный порядок невозможен
-- @expect ok
INSERT INTO reviewer_exposure(run_id, subject_id, revision, provider, model, campaign_id, first_attempt_id, profile_id, created_at)
VALUES (1, 2, 'sha-2', 'anthropic', 'opus', 2, 1, 'p-a', 600);

-- @step 22 допуск на несуществующую попытку
-- @expect error FOREIGN KEY
INSERT INTO reviewer_exposure(run_id, subject_id, revision, provider, model, campaign_id, first_attempt_id, profile_id, created_at)
VALUES (1, 2, 'sha-2', 'openai', 'gpt', 2, 404, 'p-b', 600);

-- @step 23 допуск с чужой ревизией предмета
-- @expect error FOREIGN KEY
INSERT INTO reviewer_exposure(run_id, subject_id, revision, provider, model, campaign_id, first_attempt_id, profile_id, created_at)
VALUES (1, 2, 'sha-1', 'anthropic', 'opus', 2, 1, 'p-a', 600);

-- @step 24 допуск на предмет, которого кампания не проверяет
-- @expect error FOREIGN KEY
INSERT INTO reviewer_exposure(run_id, subject_id, revision, provider, model, campaign_id, first_attempt_id, profile_id, created_at)
VALUES (1, 1, 'sha-2', 'anthropic', 'opus', 2, 1, 'p-a', 600);

-- @step 25 профиль допуска не тот, которым исполнялась попытка
-- @expect error FOREIGN KEY
INSERT INTO reviewer_exposure(run_id, subject_id, revision, provider, model, campaign_id, first_attempt_id, profile_id, created_at)
VALUES (1, 2, 'sha-2', 'openai', 'gpt', 2, 1, 'p-b', 600);

-- @step 26 пара не та, в которую резолвится профиль попытки
-- @expect error FOREIGN KEY
INSERT INTO reviewer_exposure(run_id, subject_id, revision, provider, model, campaign_id, first_attempt_id, profile_id, created_at)
VALUES (1, 2, 'sha-2', 'openai', 'gpt', 2, 1, 'p-a', 600);

-- Попытка кампании 1 — она понадобится следующему шагу как чужая.
INSERT INTO step_attempt(id, run_id, stage_id, role, campaign_id, round_id, lane_id,
                         lane_assignment_id, profile_id, subject_revision, outcome)
VALUES (2, 1, 7, 'reviewer', 1, 1, 2, 2, 'p-b', 'sha-1', NULL);

-- @step 27 попытка допуска принадлежит другой кампании
-- @expect error FOREIGN KEY
INSERT INTO reviewer_exposure(run_id, subject_id, revision, provider, model, campaign_id, first_attempt_id, profile_id, created_at)
VALUES (1, 2, 'sha-1', 'openai', 'gpt', 2, 2, 'p-b', 600);

-- Штатный путь 1: reconciler наследует профиль линии 0 — та же пара.
INSERT INTO step_attempt(id, run_id, stage_id, role, campaign_id, round_id, lane_id,
                         lane_assignment_id, profile_id, subject_revision, outcome)
VALUES (3, 1, 8, 'reconciler', 2, 2, NULL, NULL, 'p-a', 'sha-2', NULL);

-- @step 28 второй строки допуска на ту же пару база не создаёт
-- @expect error reviewer_exposure.subject_id
INSERT INTO reviewer_exposure(run_id, subject_id, revision, provider, model, campaign_id, first_attempt_id, profile_id, created_at)
VALUES (1, 2, 'sha-2', 'anthropic', 'opus', 2, 3, 'p-a', 600);

-- Штатный путь 2: retry после contract_error той же парой.
UPDATE step_attempt SET outcome = 'contract_error' WHERE id = 1;
INSERT INTO step_attempt(id, run_id, stage_id, role, campaign_id, round_id, lane_id,
                         lane_assignment_id, profile_id, subject_revision, outcome)
VALUES (4, 1, 8, 'reviewer', 2, 2, 3, 3, 'p-a', 'sha-2', NULL);

-- @step 29 membership остаётся одна и указывает на первую попытку
-- @expect rows-json [[1, 1]]
SELECT COUNT(*), MIN(first_attempt_id) FROM reviewer_exposure
 WHERE subject_id = 2 AND revision = 'sha-2'
   AND provider = 'anthropic' AND model = 'opus' AND campaign_id = 2;

-- @step 30 та же пара в другой кампании — свой допуск, конфликта нет
-- @expect ok
INSERT INTO step_attempt(id, run_id, stage_id, role, campaign_id, round_id, lane_id,
                         lane_assignment_id, profile_id, subject_revision, outcome)
VALUES (5, 1, 7, 'reviewer', 1, 1, 1, 1, 'p-a', 'sha-1', NULL);

-- @step 31 допуск той же пары в кампании 1
-- @expect ok
INSERT INTO reviewer_exposure(run_id, subject_id, revision, provider, model, campaign_id, first_attempt_id, profile_id, created_at)
VALUES (1, 1, 'sha-1', 'anthropic', 'opus', 1, 5, 'p-a', 700);

-- @step 32 свежесть: пара, уже видевшая эту ревизию предмета
-- @expect rows-json [["anthropic", "opus"]]
SELECT provider, model FROM reviewer_exposure
 WHERE subject_id = 2 AND revision = 'sha-2';

-- @step 32a UPDATE строки допуска
-- @expect error immutable
UPDATE reviewer_exposure SET model = 'sonnet' WHERE campaign_id = 2;

-- @step 32b DELETE строки допуска: свежесть не возвращается удалением
-- @expect error never deleted
DELETE FROM reviewer_exposure WHERE campaign_id = 2;

-- Штатный fix_check: та же линия проверяет уже другую ревизию предмета.
INSERT INTO review_round(id, campaign_id, round_no, kind) VALUES (4, 2, 2, 'fix_check');
INSERT INTO step_attempt(id, run_id, stage_id, role, campaign_id, round_id, lane_id,
                         lane_assignment_id, profile_id, subject_revision, outcome)
VALUES (6, 1, 8, 'reviewer', 2, 4, 3, 3, 'p-a', 'sha-2-fix1', NULL);

-- @step 32c допуск на ревизию правки: своя строка, конфликта с discovery нет
-- @expect ok
INSERT INTO reviewer_exposure(run_id, subject_id, revision, provider, model, campaign_id, first_attempt_id, profile_id, created_at)
VALUES (1, 2, 'sha-2-fix1', 'anthropic', 'opus', 2, 6, 'p-a', 800);

-- @step 32d ledger свежести помнит обе ревизии, а не только исходную
-- @expect rows-json [["sha-2"], ["sha-2-fix1"]]
SELECT revision FROM reviewer_exposure
 WHERE campaign_id = 2 AND provider = 'anthropic' AND model = 'opus'
 ORDER BY revision;

-- @step 32e допуск на ревизию, которой попытка не получала
-- @expect error FOREIGN KEY
INSERT INTO reviewer_exposure(run_id, subject_id, revision, provider, model, campaign_id, first_attempt_id, profile_id, created_at)
VALUES (1, 2, 'sha-2-fix9', 'anthropic', 'opus', 2, 6, 'p-a', 800);

-- @step 32f UPDATE резолвинга профилей: пара исполнителя не меняется задним числом
-- @expect error immutable
UPDATE run_profile_resolution SET model = 'sonnet' WHERE profile_id = 'p-a';

-- @step 32g DELETE резолвинга профилей
-- @expect error never deleted
DELETE FROM run_profile_resolution WHERE profile_id = 'p-b';

-- Второй прогон и новая ревизия — чтобы проверить последний непокрытый ключ
-- допуска, не задев уникальность.
INSERT INTO run DEFAULT VALUES;
INSERT INTO run_profile_resolution(run_id, profile_id, provider, model, resolved_at)
VALUES (2, 'p-a', 'anthropic', 'opus', 10);
INSERT INTO step_attempt(id, run_id, stage_id, role, campaign_id, round_id, lane_id,
                         lane_assignment_id, profile_id, subject_revision, outcome)
VALUES (7, 1, 8, 'reviewer', 2, 4, 3, 3, 'p-a', 'sha-2-fix2', 'succeeded');

-- @step 32h допуск, у которого прогон не совпадает с прогоном попытки
-- @expect error FOREIGN KEY
INSERT INTO reviewer_exposure(run_id, subject_id, revision, provider, model, campaign_id, first_attempt_id, profile_id, created_at)
VALUES (2, 2, 'sha-2-fix2', 'anthropic', 'opus', 2, 7, 'p-a', 800);

-- @step 32i попытка ревьюера без ревизии предмета
-- @expect error CHECK
INSERT INTO step_attempt(id, run_id, stage_id, role, campaign_id, round_id, lane_id,
                         lane_assignment_id, profile_id, subject_revision, outcome)
VALUES (8, 1, 8, 'reviewer', 2, 4, 3, 3, 'p-a', NULL, NULL);

-- @step 33 foreign_key_check
-- @expect empty
PRAGMA foreign_key_check;

-- @step 34 объекты: таблица переходов и восемь триггеров кампании, слота,
-- допуска и резолвинга
-- @expect rows-json [["table", 1], ["trigger", 8]]
SELECT type, COUNT(*) FROM sqlite_master
 WHERE name IN ('campaign_transition', 'trg_campaign_state_transition',
                'trg_campaign_initial_state', 'trg_campaign_snapshot_immutable',
                'trg_lane_index_bounds',
                'trg_exposure_immutable', 'trg_exposure_no_delete',
                'trg_profile_resolution_immutable', 'trg_profile_resolution_no_delete')
 GROUP BY type ORDER BY type;
