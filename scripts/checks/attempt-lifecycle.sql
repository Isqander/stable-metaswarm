-- C-07/C-08: lifecycle попытки, круга и immutable evidence.
--
-- Это исполняемый срез нормативных триггеров db-schema.md §1.5, §4 и
-- §5.2–§5.5. Он проверяет три разных класса строк:
--   * step_attempt: active intent -> один terminal result;
--   * review_round: open -> одно закрытие;
--   * evidence: ни UPDATE, ни DELETE.

CREATE TABLE step_attempt (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  public_id          TEXT,
  run_id             INTEGER,
  stage_id           INTEGER,
  role               TEXT,
  campaign_id        INTEGER,
  round_id           INTEGER,
  lane_id            INTEGER,
  lane_assignment_id INTEGER,
  subject_revision   TEXT,
  session_id         INTEGER,
  profile_id         TEXT,
  requested_model    TEXT,
  prompt_template_id TEXT,
  prompt_hash        TEXT,
  rubric_id          TEXT,
  rubric_hash        TEXT,
  input_sha          TEXT,
  input_refs_json    TEXT,
  manifest_json      TEXT,
  started_at         INTEGER,
  outcome            TEXT,
  outcome_detail     TEXT,
  actual_model       TEXT,
  output_sha         TEXT,
  finished_at        INTEGER,
  transcript_path    TEXT,
  transcript_digest  TEXT
);

-- @mutation-cover-when trg_attempt_initial_state
CREATE TRIGGER trg_attempt_initial_state
BEFORE INSERT ON step_attempt
WHEN NEW.outcome           IS NOT NULL
  OR NEW.outcome_detail    IS NOT NULL
  OR NEW.actual_model      IS NOT NULL
  OR NEW.output_sha        IS NOT NULL
  OR NEW.finished_at       IS NOT NULL
  OR NEW.transcript_path   IS NOT NULL
  OR NEW.transcript_digest IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'step_attempt must start active');
END;

-- @mutation-cover-when trg_attempt_finish_once
CREATE TRIGGER trg_attempt_finish_once
BEFORE UPDATE ON step_attempt
WHEN NEW.id                 IS NOT OLD.id
  OR NEW.public_id          IS NOT OLD.public_id
  OR NEW.run_id             IS NOT OLD.run_id
  OR NEW.stage_id           IS NOT OLD.stage_id
  OR NEW.role               IS NOT OLD.role
  OR NEW.campaign_id        IS NOT OLD.campaign_id
  OR NEW.round_id           IS NOT OLD.round_id
  OR NEW.lane_id            IS NOT OLD.lane_id
  OR NEW.lane_assignment_id IS NOT OLD.lane_assignment_id
  OR NEW.subject_revision   IS NOT OLD.subject_revision
  OR NEW.session_id         IS NOT OLD.session_id
  OR NEW.profile_id         IS NOT OLD.profile_id
  OR NEW.requested_model    IS NOT OLD.requested_model
  OR NEW.prompt_template_id IS NOT OLD.prompt_template_id
  OR NEW.prompt_hash        IS NOT OLD.prompt_hash
  OR NEW.rubric_id          IS NOT OLD.rubric_id
  OR NEW.rubric_hash        IS NOT OLD.rubric_hash
  OR NEW.input_sha          IS NOT OLD.input_sha
  OR NEW.input_refs_json    IS NOT OLD.input_refs_json
  OR NEW.manifest_json      IS NOT OLD.manifest_json
  OR NEW.started_at         IS NOT OLD.started_at
  OR OLD.outcome            IS NOT NULL
  OR NEW.outcome            IS NULL
  OR NEW.finished_at        IS NULL
BEGIN
  SELECT RAISE(ABORT, 'step_attempt allows one active-to-terminal update');
END;

CREATE TRIGGER trg_attempt_no_delete
BEFORE DELETE ON step_attempt
BEGIN
  SELECT RAISE(ABORT, 'step_attempt cannot be deleted');
END;

CREATE TABLE review_round (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id           INTEGER,
  round_no              INTEGER,
  kind                  TEXT,
  preceding_revision_id INTEGER,
  result                TEXT,
  opened_at             INTEGER,
  closed_at             INTEGER,
  CHECK ((result IS NULL) = (closed_at IS NULL))
);

CREATE TRIGGER trg_round_initial_state
BEFORE INSERT ON review_round
WHEN NEW.result IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'review_round must start open');
END;

-- @mutation-cover-when trg_round_finish_once
CREATE TRIGGER trg_round_finish_once
BEFORE UPDATE ON review_round
WHEN NEW.id                    IS NOT OLD.id
  OR NEW.campaign_id           IS NOT OLD.campaign_id
  OR NEW.round_no              IS NOT OLD.round_no
  OR NEW.kind                  IS NOT OLD.kind
  OR NEW.preceding_revision_id IS NOT OLD.preceding_revision_id
  OR NEW.opened_at             IS NOT OLD.opened_at
  OR OLD.result                IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'review_round allows one open-to-closed update');
END;

CREATE TRIGGER trg_round_no_delete
BEFORE DELETE ON review_round
BEGIN
  SELECT RAISE(ABORT, 'review_round cannot be deleted');
END;

CREATE TABLE review_observation (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  payload TEXT
);

CREATE TRIGGER trg_observation_immutable
BEFORE UPDATE ON review_observation
BEGIN
  SELECT RAISE(ABORT, 'review_observation is immutable');
END;

CREATE TRIGGER trg_observation_no_delete
BEFORE DELETE ON review_observation
BEGIN
  SELECT RAISE(ABORT, 'review_observation cannot be deleted');
END;

CREATE TABLE finding_observation_link (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  payload TEXT
);

CREATE TRIGGER trg_link_immutable
BEFORE UPDATE ON finding_observation_link
BEGIN
  SELECT RAISE(ABORT, 'finding_observation_link is immutable');
END;

CREATE TRIGGER trg_link_no_delete
BEFORE DELETE ON finding_observation_link
BEGIN
  SELECT RAISE(ABORT, 'finding_observation_link cannot be deleted');
END;

CREATE TABLE finding_resolution (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  payload TEXT
);

CREATE TRIGGER trg_resolution_immutable
BEFORE UPDATE ON finding_resolution
BEGIN
  SELECT RAISE(ABORT, 'finding_resolution is immutable');
END;

CREATE TRIGGER trg_resolution_no_delete
BEFORE DELETE ON finding_resolution
BEGIN
  SELECT RAISE(ABORT, 'finding_resolution cannot be deleted');
END;

-- === данные ===

-- @step 00 recursive_triggers включён: REPLACE не обходит DELETE-trigger
-- @expect rows-json [[1]]
PRAGMA recursive_triggers;

-- @step 01 active attempt создаётся без terminal-полей
-- @expect ok
INSERT INTO step_attempt (
  id, public_id, run_id, stage_id, role, campaign_id, round_id, lane_id,
  lane_assignment_id, subject_revision, session_id, profile_id,
  requested_model, prompt_template_id, prompt_hash, rubric_id, rubric_hash,
  input_sha, input_refs_json, manifest_json, started_at
) VALUES (
  1, 'A-1', 1, 10, 'reviewer', 20, 30, 40,
  50, 'rev-1', 60, 'profile-a',
  'requested-a', 'prompt-a', 'prompt-hash-a', 'rubric-a', 'rubric-hash-a',
  'input-a', '[]', '{}', 100
);

-- Каждый terminal-атрибут INSERT проверяется отдельно. Иначе один заполненный
-- outcome маскирует потерю остальных ветвей initial-state trigger.
-- @step 02a INSERT с outcome
-- @expect error must start active
INSERT INTO step_attempt(id, outcome) VALUES (2, 'succeeded');

-- @step 02b INSERT с outcome_detail
-- @expect error must start active
INSERT INTO step_attempt(id, outcome_detail) VALUES (2, 'detail');

-- @step 02c INSERT с actual_model
-- @expect error must start active
INSERT INTO step_attempt(id, actual_model) VALUES (2, 'model-a');

-- @step 02d INSERT с output_sha
-- @expect error must start active
INSERT INTO step_attempt(id, output_sha) VALUES (2, 'output-a');

-- @step 02e INSERT с finished_at
-- @expect error must start active
INSERT INTO step_attempt(id, finished_at) VALUES (2, 110);

-- @step 02f INSERT с transcript_path
-- @expect error must start active
INSERT INTO step_attempt(id, transcript_path) VALUES (2, 'transcript.jsonl');

-- @step 02g INSERT с transcript_digest
-- @expect error must start active
INSERT INTO step_attempt(id, transcript_digest) VALUES (2, 'digest-a');

-- Каждый шаг ниже делает реальный terminal UPDATE и заодно меняет ровно одно
-- input/scope-поле. UPDATE незавершённой строки без outcome ничего не доказывал
-- бы: его отклоняет общая ветка NEW.outcome IS NULL.
-- @step 03a id immutable
-- @expect error active-to-terminal
UPDATE step_attempt
SET id = 101, outcome = 'succeeded', finished_at = 200
WHERE id = 1 AND outcome IS NULL;

-- @step 03b public_id immutable
-- @expect error active-to-terminal
UPDATE step_attempt
SET public_id = 'A-other', outcome = 'succeeded', finished_at = 200
WHERE id = 1 AND outcome IS NULL;

-- @step 03c run_id immutable
-- @expect error active-to-terminal
UPDATE step_attempt
SET run_id = 2, outcome = 'succeeded', finished_at = 200
WHERE id = 1 AND outcome IS NULL;

-- @step 03d stage_id immutable
-- @expect error active-to-terminal
UPDATE step_attempt
SET stage_id = 11, outcome = 'succeeded', finished_at = 200
WHERE id = 1 AND outcome IS NULL;

-- @step 03e role immutable
-- @expect error active-to-terminal
UPDATE step_attempt
SET role = 'reconciler', outcome = 'succeeded', finished_at = 200
WHERE id = 1 AND outcome IS NULL;

-- @step 03f campaign_id immutable
-- @expect error active-to-terminal
UPDATE step_attempt
SET campaign_id = 21, outcome = 'succeeded', finished_at = 200
WHERE id = 1 AND outcome IS NULL;

-- @step 03g round_id immutable
-- @expect error active-to-terminal
UPDATE step_attempt
SET round_id = 31, outcome = 'succeeded', finished_at = 200
WHERE id = 1 AND outcome IS NULL;

-- @step 03h lane_id immutable
-- @expect error active-to-terminal
UPDATE step_attempt
SET lane_id = 41, outcome = 'succeeded', finished_at = 200
WHERE id = 1 AND outcome IS NULL;

-- @step 03i lane_assignment_id immutable
-- @expect error active-to-terminal
UPDATE step_attempt
SET lane_assignment_id = 51, outcome = 'succeeded', finished_at = 200
WHERE id = 1 AND outcome IS NULL;

-- @step 03j subject_revision immutable
-- @expect error active-to-terminal
UPDATE step_attempt
SET subject_revision = 'rev-2', outcome = 'succeeded', finished_at = 200
WHERE id = 1 AND outcome IS NULL;

-- @step 03k session_id immutable
-- @expect error active-to-terminal
UPDATE step_attempt
SET session_id = 61, outcome = 'succeeded', finished_at = 200
WHERE id = 1 AND outcome IS NULL;

-- @step 03l profile_id immutable
-- @expect error active-to-terminal
UPDATE step_attempt
SET profile_id = 'profile-b', outcome = 'succeeded', finished_at = 200
WHERE id = 1 AND outcome IS NULL;

-- @step 03m requested_model immutable
-- @expect error active-to-terminal
UPDATE step_attempt
SET requested_model = 'requested-b', outcome = 'succeeded', finished_at = 200
WHERE id = 1 AND outcome IS NULL;

-- @step 03n prompt_template_id immutable
-- @expect error active-to-terminal
UPDATE step_attempt
SET prompt_template_id = 'prompt-b', outcome = 'succeeded', finished_at = 200
WHERE id = 1 AND outcome IS NULL;

-- @step 03o prompt_hash immutable
-- @expect error active-to-terminal
UPDATE step_attempt
SET prompt_hash = 'prompt-hash-b', outcome = 'succeeded', finished_at = 200
WHERE id = 1 AND outcome IS NULL;

-- @step 03p rubric_id immutable
-- @expect error active-to-terminal
UPDATE step_attempt
SET rubric_id = 'rubric-b', outcome = 'succeeded', finished_at = 200
WHERE id = 1 AND outcome IS NULL;

-- @step 03q rubric_hash immutable
-- @expect error active-to-terminal
UPDATE step_attempt
SET rubric_hash = 'rubric-hash-b', outcome = 'succeeded', finished_at = 200
WHERE id = 1 AND outcome IS NULL;

-- @step 03r input_sha immutable
-- @expect error active-to-terminal
UPDATE step_attempt
SET input_sha = 'input-b', outcome = 'succeeded', finished_at = 200
WHERE id = 1 AND outcome IS NULL;

-- @step 03s input_refs_json immutable
-- @expect error active-to-terminal
UPDATE step_attempt
SET input_refs_json = '[1]', outcome = 'succeeded', finished_at = 200
WHERE id = 1 AND outcome IS NULL;

-- @step 03t manifest_json immutable
-- @expect error active-to-terminal
UPDATE step_attempt
SET manifest_json = '{"changed":true}', outcome = 'succeeded', finished_at = 200
WHERE id = 1 AND outcome IS NULL;

-- @step 03u started_at immutable
-- @expect error active-to-terminal
UPDATE step_attempt
SET started_at = 101, outcome = 'succeeded', finished_at = 200
WHERE id = 1 AND outcome IS NULL;

-- @step 04a outcome без finished_at не терминализирует attempt
-- @expect error active-to-terminal
UPDATE step_attempt SET outcome = 'succeeded' WHERE id = 1;

-- @step 04b finished_at без outcome не терминализирует attempt
-- @expect error active-to-terminal
UPDATE step_attempt SET finished_at = 200 WHERE id = 1;

-- @step 04c result нельзя дозаписывать до terminal-перехода
-- @expect error active-to-terminal
UPDATE step_attempt SET outcome_detail = 'too early' WHERE id = 1;

-- @step 05 единственный CAS active -> terminal
-- @expect ok
UPDATE step_attempt
SET outcome = 'succeeded', outcome_detail = 'ok', actual_model = 'model-a',
    output_sha = 'output-a', finished_at = 200,
    transcript_path = 'transcript-a.jsonl', transcript_digest = 'digest-a'
WHERE id = 1 AND outcome IS NULL;

-- @step 06 terminal result записан целиком, input не изменился
-- @expect rows-json [["profile-a","rev-1","succeeded","ok","model-a","output-a",200,"transcript-a.jsonl","digest-a"]]
SELECT profile_id, subject_revision, outcome, outcome_detail, actual_model,
       output_sha, finished_at, transcript_path, transcript_digest
FROM step_attempt WHERE id = 1;

-- @step 07 terminal outcome нельзя переписать
-- @expect error active-to-terminal
UPDATE step_attempt SET outcome = 'failed', finished_at = 201 WHERE id = 1;

-- @step 08 terminal outcome нельзя вернуть в NULL
-- @expect error active-to-terminal
UPDATE step_attempt SET outcome = NULL, finished_at = NULL WHERE id = 1;

-- @step 09 terminal payload нельзя переписать при прежнем outcome
-- @expect error active-to-terminal
UPDATE step_attempt SET outcome_detail = 'rewritten' WHERE id = 1;

-- @step 10 input нельзя переписать после завершения
-- @expect error active-to-terminal
UPDATE step_attempt SET profile_id = 'profile-c' WHERE id = 1;

-- @step 11 attempt нельзя удалить
-- @expect error cannot be deleted
DELETE FROM step_attempt WHERE id = 1;

-- @step 11a INSERT OR REPLACE не возвращает terminal attempt в active
-- @expect error cannot be deleted
INSERT OR REPLACE INTO step_attempt (
  id, public_id, run_id, stage_id, role, campaign_id, round_id, lane_id,
  lane_assignment_id, subject_revision, session_id, profile_id,
  requested_model, prompt_template_id, prompt_hash, rubric_id, rubric_hash,
  input_sha, input_refs_json, manifest_json, started_at
) VALUES (
  1, 'A-replaced', 1, 10, 'reviewer', 20, 30, 40,
  50, 'rev-replaced', 60, 'profile-a',
  'requested-a', 'prompt-a', 'prompt-hash-a', 'rubric-a', 'rubric-hash-a',
  'input-a', '[]', '{}', 400
);

INSERT INTO step_attempt (
  id, public_id, run_id, stage_id, role, profile_id, requested_model,
  prompt_template_id, prompt_hash, input_refs_json, manifest_json, started_at
) VALUES (
  3, 'A-3', 1, 10, 'planner', 'profile-a', 'requested-a',
  'prompt-a', 'prompt-hash-a', '[]', '{}', 300
);

-- @step 12 failure outcome допускает nullable диагностические поля
-- @expect ok
UPDATE step_attempt
SET outcome = 'interrupted', finished_at = 301
WHERE id = 3 AND outcome IS NULL;

-- @step 13 nullable terminal payload не подменяется выдуманными значениями
-- @expect rows-json [["interrupted",301,null,null,null,null,null]]
SELECT outcome, finished_at, outcome_detail, actual_model, output_sha,
       transcript_path, transcript_digest
FROM step_attempt WHERE id = 3;

-- @step 20 open round создаётся с пустой парой result/closed_at
-- @expect ok
INSERT INTO review_round (
  id, campaign_id, round_no, kind, preceding_revision_id, opened_at
) VALUES (1, 20, 1, 'discovery', NULL, 100);

-- @step 21 closed round нельзя создать INSERT-ом
-- @expect error must start open
INSERT INTO review_round (
  id, campaign_id, round_no, kind, preceding_revision_id,
  result, opened_at, closed_at
) VALUES (2, 20, 2, 'fix_check', 90, 'clean', 200, 210);

-- @step 21a result без closed_at отвергает CHECK пары
-- @expect error CHECK constraint failed
UPDATE review_round SET result = 'clean' WHERE id = 1;

-- @step 21b closed_at без result отвергает CHECK пары
-- @expect error CHECK constraint failed
UPDATE review_round SET closed_at = 150 WHERE id = 1;

-- Каждый input-дизъюнкт проверяется terminal UPDATE, а не open→open UPDATE,
-- который целиком поймало бы общее правило lifecycle.
-- @step 22a id круга immutable
-- @expect error open-to-closed
UPDATE review_round
SET id = 101, result = 'clean', closed_at = 150 WHERE id = 1;

-- @step 22b campaign_id круга immutable
-- @expect error open-to-closed
UPDATE review_round
SET campaign_id = 21, result = 'clean', closed_at = 150 WHERE id = 1;

-- @step 22c round_no круга immutable
-- @expect error open-to-closed
UPDATE review_round
SET round_no = 2, result = 'clean', closed_at = 150 WHERE id = 1;

-- @step 22d kind круга immutable
-- @expect error open-to-closed
UPDATE review_round
SET kind = 'fix_check', result = 'clean', closed_at = 150 WHERE id = 1;

-- @step 22e preceding_revision_id круга immutable
-- @expect error open-to-closed
UPDATE review_round
SET preceding_revision_id = 90, result = 'clean', closed_at = 150 WHERE id = 1;

-- @step 22f opened_at круга immutable
-- @expect error open-to-closed
UPDATE review_round
SET opened_at = 101, result = 'clean', closed_at = 150 WHERE id = 1;

-- @step 23 единственный CAS open -> closed
-- @expect ok
UPDATE review_round
SET result = 'clean', closed_at = 150
WHERE id = 1 AND result IS NULL;

-- @step 24 закрытие сохранило identity круга
-- @expect rows-json [[20,1,"discovery",null,"clean",100,150]]
SELECT campaign_id, round_no, kind, preceding_revision_id,
       result, opened_at, closed_at
FROM review_round WHERE id = 1;

-- @step 25 result закрытого круга нельзя заменить
-- @expect error open-to-closed
UPDATE review_round SET result = 'escalated', closed_at = 151 WHERE id = 1;

-- @step 26 закрытый круг нельзя вернуть в open
-- @expect error open-to-closed
UPDATE review_round SET result = NULL, closed_at = NULL WHERE id = 1;

-- @step 27 input закрытого круга нельзя переписать
-- @expect error open-to-closed
UPDATE review_round SET kind = 'fix_check' WHERE id = 1;

-- @step 28 круг нельзя удалить
-- @expect error cannot be deleted
DELETE FROM review_round WHERE id = 1;

-- @step 28a INSERT OR REPLACE не переоткрывает закрытый круг
-- @expect error cannot be deleted
INSERT OR REPLACE INTO review_round (
  id, campaign_id, round_no, kind, preceding_revision_id, opened_at
) VALUES (1, 20, 1, 'discovery', NULL, 200);

INSERT INTO review_observation (id, payload) VALUES (1, 'observation');
INSERT INTO finding_observation_link (id, payload) VALUES (1, 'link');
INSERT INTO finding_resolution (id, payload) VALUES (1, 'resolution');

-- @step 30 observation нельзя обновить
-- @expect error immutable
UPDATE review_observation SET payload = 'changed' WHERE id = 1;

-- @step 31 observation нельзя удалить
-- @expect error cannot be deleted
DELETE FROM review_observation WHERE id = 1;

-- @step 31a INSERT OR REPLACE не переписывает слепой вывод
-- @expect error cannot be deleted
INSERT OR REPLACE INTO review_observation(id, payload)
VALUES (1, 'подменённый вывод');

-- @step 32 link нельзя обновить
-- @expect error immutable
UPDATE finding_observation_link SET payload = 'changed' WHERE id = 1;

-- @step 33 link нельзя удалить
-- @expect error cannot be deleted
DELETE FROM finding_observation_link WHERE id = 1;

-- @step 33a INSERT OR REPLACE не меняет identity-связь
-- @expect error cannot be deleted
INSERT OR REPLACE INTO finding_observation_link(id, payload)
VALUES (1, 'replaced link');

-- @step 34 resolution нельзя обновить
-- @expect error immutable
UPDATE finding_resolution SET payload = 'changed' WHERE id = 1;

-- @step 35 resolution нельзя удалить
-- @expect error cannot be deleted
DELETE FROM finding_resolution WHERE id = 1;

-- @step 35a INSERT OR REPLACE не переписывает закрытие
-- @expect error cannot be deleted
INSERT OR REPLACE INTO finding_resolution(id, payload)
VALUES (1, 'replaced resolution');

-- @step 36 строки всех трёх evidence-таблиц остались прежними
-- @expect rows-json [["observation","link","resolution"]]
SELECT o.payload, l.payload, r.payload
FROM review_observation o
CROSS JOIN finding_observation_link l
CROSS JOIN finding_resolution r;
