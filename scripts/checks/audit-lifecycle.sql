-- Adversarial lifecycle audit: что показали человеку, что он ответил и какой
-- reviewer decision вошёл в историю кругов. Проверка отдельно фиксирует
-- INSERT OR REPLACE: прямого DELETE-теста недостаточно без recursive_triggers.

CREATE TABLE run_event (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id       INTEGER NOT NULL,
  kind         TEXT    NOT NULL,
  payload_json TEXT    NOT NULL,
  core_version TEXT    NOT NULL,
  created_at   INTEGER NOT NULL
);

CREATE TRIGGER trg_event_immutable
BEFORE UPDATE ON run_event
BEGIN
  SELECT RAISE(ABORT, 'run_event is append-only');
END;

CREATE TRIGGER trg_event_no_delete
BEFORE DELETE ON run_event
BEGIN
  SELECT RAISE(ABORT, 'run_event is append-only');
END;

CREATE TABLE human_question (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  public_id     TEXT,
  run_id        INTEGER,
  branch_id     INTEGER,
  stage_id      INTEGER,
  campaign_id   INTEGER,
  round_id      INTEGER,
  finding_id    INTEGER,
  reason        TEXT,
  question_text TEXT,
  options_json  TEXT,
  snapshot_json TEXT,
  asked_at      INTEGER,
  answered_at   INTEGER,
  reask_count   INTEGER DEFAULT 0
);

CREATE TRIGGER trg_question_content_immutable
BEFORE UPDATE OF
  id, public_id, run_id, branch_id, stage_id, campaign_id, round_id,
  finding_id, reason, question_text, options_json, snapshot_json, asked_at
ON human_question
BEGIN
  SELECT RAISE(ABORT, 'human_question content is immutable');
END;

CREATE TRIGGER trg_question_no_delete
BEFORE DELETE ON human_question
BEGIN
  SELECT RAISE(ABORT, 'human_question cannot be deleted');
END;

CREATE TABLE human_answer (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  question_id      INTEGER,
  raw_text         TEXT,
  chosen_option    TEXT,
  interpreted_json TEXT,
  transport        TEXT,
  update_id        INTEGER,
  received_at      INTEGER,
  UNIQUE (question_id)
);

CREATE TRIGGER trg_answer_immutable
BEFORE UPDATE ON human_answer
BEGIN
  SELECT RAISE(ABORT, 'human_answer is immutable');
END;

CREATE TRIGGER trg_answer_no_delete
BEFORE DELETE ON human_answer
BEGIN
  SELECT RAISE(ABORT, 'human_answer cannot be deleted');
END;

CREATE TABLE finding_round (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id         INTEGER,
  run_id              INTEGER,
  finding_id          INTEGER,
  round_no            INTEGER,
  round_id             INTEGER,
  owner_lane_id       INTEGER,
  entry_kind          TEXT,
  disposition         TEXT,
  disposition_reason  TEXT,
  author_attempt_id   INTEGER,
  reviewer_decision   TEXT,
  reviewer_attempt_id INTEGER,
  decided_at          INTEGER,
  UNIQUE (campaign_id, finding_id, round_no),
  CHECK ((reviewer_decision IS NULL) = (reviewer_attempt_id IS NULL)),
  CHECK ((reviewer_decision IS NULL) = (decided_at IS NULL))
);

-- Координаты круга и ответ автора задаются при INSERT и потом не меняются.
CREATE TRIGGER trg_finding_round_input_immutable
BEFORE UPDATE OF
  id, campaign_id, run_id, finding_id, round_no, round_id, owner_lane_id,
  entry_kind, disposition, disposition_reason, author_attempt_id
ON finding_round
BEGIN
  SELECT RAISE(ABORT, 'finding_round input is immutable');
END;

-- Reviewer decision не существует при создании roster и записывается позже.
CREATE TRIGGER trg_finding_round_initial_state
BEFORE INSERT ON finding_round
WHEN NEW.reviewer_decision IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'finding_round must start without reviewer decision');
END;

CREATE TRIGGER trg_finding_round_decision_once
BEFORE UPDATE OF reviewer_decision, reviewer_attempt_id, decided_at
ON finding_round
WHEN OLD.reviewer_decision IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'finding_round reviewer decision is terminal');
END;

CREATE TRIGGER trg_finding_round_no_delete
BEFORE DELETE ON finding_round
BEGIN
  SELECT RAISE(ABORT, 'finding_round cannot be deleted');
END;

-- === данные ===

-- @step 00 recursive_triggers включён
-- @expect rows-json [[1]]
PRAGMA recursive_triggers;

-- @step 01 вопрос сохраняет показанный snapshot
-- @expect ok
INSERT INTO human_question (
  id, public_id, run_id, campaign_id, round_id, reason, question_text,
  options_json, snapshot_json, asked_at
) VALUES (
  1, 'Q-1', 1, 10, 20, 'dispute', 'Что делать?',
  '["A","B","C"]', '{"finding":"F-1"}', 100
);

-- Каждая content-колонка имеет свой свидетель: UPDATE OF — такой же
-- многосоставной контракт, как WHEN ... OR.
-- @step 02a id вопроса immutable
-- @expect error content is immutable
UPDATE human_question SET id = 101 WHERE id = 1;

-- @step 02b public_id вопроса immutable
-- @expect error content is immutable
UPDATE human_question SET public_id = 'Q-other' WHERE id = 1;

-- @step 02c run_id вопроса immutable
-- @expect error content is immutable
UPDATE human_question SET run_id = 2 WHERE id = 1;

-- @step 02d branch_id вопроса immutable
-- @expect error content is immutable
UPDATE human_question SET branch_id = 3 WHERE id = 1;

-- @step 02e stage_id вопроса immutable
-- @expect error content is immutable
UPDATE human_question SET stage_id = 4 WHERE id = 1;

-- @step 02f campaign_id вопроса immutable
-- @expect error content is immutable
UPDATE human_question SET campaign_id = 11 WHERE id = 1;

-- @step 02g round_id вопроса immutable
-- @expect error content is immutable
UPDATE human_question SET round_id = 21 WHERE id = 1;

-- @step 02h finding_id вопроса immutable
-- @expect error content is immutable
UPDATE human_question SET finding_id = 31 WHERE id = 1;

-- @step 02i reason вопроса immutable
-- @expect error content is immutable
UPDATE human_question SET reason = 'cap_exhausted_same' WHERE id = 1;

-- @step 02j question_text immutable
-- @expect error content is immutable
UPDATE human_question SET question_text = 'Другой вопрос' WHERE id = 1;

-- @step 02k options_json immutable
-- @expect error content is immutable
UPDATE human_question SET options_json = '["A"]' WHERE id = 1;

-- @step 02l snapshot вопроса нельзя переписать
-- @expect error content is immutable
UPDATE human_question SET snapshot_json = '{"finding":"F-2"}' WHERE id = 1;

-- @step 02m asked_at вопроса immutable
-- @expect error content is immutable
UPDATE human_question SET asked_at = 101 WHERE id = 1;

-- @step 03 lifecycle-поле answered_at остаётся разрешённым UPDATE
-- @expect ok
UPDATE human_question SET answered_at = 120 WHERE id = 1;

-- @step 04 INSERT OR REPLACE не переписывает показанный вопрос
-- @expect error cannot be deleted
INSERT OR REPLACE INTO human_question (
  id, public_id, run_id, campaign_id, round_id, reason, question_text,
  snapshot_json, asked_at
) VALUES (1, 'Q-1', 1, 10, 20, 'dispute', 'Другой вопрос', '{}', 121);

-- @step 10 принятый ответ сохраняется один раз
-- @expect ok
INSERT INTO human_answer (
  id, question_id, raw_text, chosen_option, transport, update_id, received_at
) VALUES (1, 1, 'A', 'A', 'cli', NULL, 120);

-- @step 11 второй принятый ответ на тот же вопрос запрещён UNIQUE
-- @expect error UNIQUE constraint failed
INSERT INTO human_answer (
  id, question_id, raw_text, chosen_option, transport, received_at
) VALUES (2, 1, 'B', 'B', 'cli', 121);

-- @step 12 принятый ответ нельзя обновить
-- @expect error immutable
UPDATE human_answer SET chosen_option = 'C' WHERE id = 1;

-- @step 13 принятый ответ нельзя удалить
-- @expect error cannot be deleted
DELETE FROM human_answer WHERE id = 1;

-- @step 14 INSERT OR REPLACE не обходит «один принятый ответ»
-- @expect error cannot be deleted
INSERT OR REPLACE INTO human_answer (
  id, question_id, raw_text, chosen_option, transport, received_at
) VALUES (2, 1, 'B', 'B', 'cli', 121);

-- @step 20 issued-строка рождается без reviewer decision
-- @expect ok
INSERT INTO finding_round (
  id, campaign_id, run_id, finding_id, round_no, round_id, owner_lane_id,
  entry_kind, disposition, author_attempt_id
) VALUES (1, 10, 1, 30, 2, 20, 40, 'issued', 'fixed', 50);

-- @step 20a duplicate roster запрещён UNIQUE
-- @expect error UNIQUE constraint failed
INSERT INTO finding_round (
  id, campaign_id, run_id, finding_id, round_no, round_id, owner_lane_id,
  entry_kind, disposition, author_attempt_id
) VALUES (2, 10, 1, 30, 2, 20, 40, 'issued', 'fixed', 50);

-- @step 21 reviewer decision нельзя подложить INSERT-ом
-- @expect error must start without reviewer decision
INSERT INTO finding_round (
  id, campaign_id, run_id, finding_id, round_no, round_id, owner_lane_id,
  entry_kind, disposition, author_attempt_id,
  reviewer_decision, reviewer_attempt_id, decided_at
) VALUES (
  2, 10, 1, 31, 2, 20, 40, 'issued', 'fixed', 50,
  'verified_fixed', 60, 130
);

-- Каждый input/author-атрибут меняется заодно с валидным terminal decision:
-- общий CHECK или decision-trigger не должен быть чужим свидетелем.
-- @step 22a id finding_round immutable
-- @expect error input is immutable
UPDATE finding_round
SET id = 101, reviewer_decision = 'verified_fixed', reviewer_attempt_id = 60,
    decided_at = 130
WHERE id = 1;

-- @step 22b campaign_id finding_round immutable
-- @expect error input is immutable
UPDATE finding_round
SET campaign_id = 11,
    reviewer_decision = 'verified_fixed', reviewer_attempt_id = 60,
    decided_at = 130
WHERE id = 1;

-- @step 22c run_id finding_round immutable
-- @expect error input is immutable
UPDATE finding_round
SET run_id = 2, reviewer_decision = 'verified_fixed', reviewer_attempt_id = 60,
    decided_at = 130
WHERE id = 1;

-- @step 22d finding_id finding_round immutable
-- @expect error input is immutable
UPDATE finding_round
SET finding_id = 31,
    reviewer_decision = 'verified_fixed', reviewer_attempt_id = 60,
    decided_at = 130
WHERE id = 1;

-- @step 22e round_no finding_round immutable
-- @expect error input is immutable
UPDATE finding_round
SET round_no = 3, reviewer_decision = 'verified_fixed', reviewer_attempt_id = 60,
    decided_at = 130
WHERE id = 1;

-- @step 22f round_id finding_round immutable
-- @expect error input is immutable
UPDATE finding_round
SET round_id = 21, reviewer_decision = 'verified_fixed', reviewer_attempt_id = 60,
    decided_at = 130
WHERE id = 1;

-- @step 22g owner_lane_id finding_round immutable
-- @expect error input is immutable
UPDATE finding_round
SET owner_lane_id = 41,
    reviewer_decision = 'verified_fixed', reviewer_attempt_id = 60,
    decided_at = 130
WHERE id = 1;

-- @step 22h entry_kind finding_round immutable
-- @expect error input is immutable
UPDATE finding_round
SET entry_kind = 'post_check',
    reviewer_decision = 'verified_fixed', reviewer_attempt_id = 60,
    decided_at = 130
WHERE id = 1;

-- @step 22i disposition finding_round immutable
-- @expect error input is immutable
UPDATE finding_round
SET disposition = 'rejected',
    reviewer_decision = 'verified_fixed', reviewer_attempt_id = 60,
    decided_at = 130
WHERE id = 1;

-- @step 22j disposition_reason finding_round immutable
-- @expect error input is immutable
UPDATE finding_round
SET disposition_reason = 'reason',
    reviewer_decision = 'verified_fixed', reviewer_attempt_id = 60,
    decided_at = 130
WHERE id = 1;

-- @step 22k author_attempt_id finding_round immutable
-- @expect error input is immutable
UPDATE finding_round
SET author_attempt_id = 51,
    reviewer_decision = 'verified_fixed', reviewer_attempt_id = 60,
    decided_at = 130
WHERE id = 1;

-- @step 23 частичный reviewer decision отвергают CHECK пары
-- @expect error CHECK constraint failed
UPDATE finding_round
SET reviewer_decision = 'verified_fixed', reviewer_attempt_id = 60
WHERE id = 1;

-- @step 23a reviewer_attempt_id обязателен вместе с decision
-- @expect error CHECK constraint failed
UPDATE finding_round
SET reviewer_decision = 'verified_fixed', decided_at = 130
WHERE id = 1;

-- @step 24 reviewer decision записывается один раз
-- @expect ok
UPDATE finding_round
SET reviewer_decision = 'verified_fixed', reviewer_attempt_id = 60,
    decided_at = 130
WHERE id = 1;

-- @step 25 reviewer decision нельзя переписать
-- @expect error reviewer decision is terminal
UPDATE finding_round
SET reviewer_decision = 'still_present', reviewer_attempt_id = 61,
    decided_at = 131
WHERE id = 1;

-- UPDATE OF — три независимых входа trigger'а; каждый получает свой шаг.
-- @step 25a reviewer_decision нельзя переписать отдельно
-- @expect error reviewer decision is terminal
UPDATE finding_round SET reviewer_decision = 'insists' WHERE id = 1;

-- @step 25b reviewer_attempt_id нельзя переписать отдельно
-- @expect error reviewer decision is terminal
UPDATE finding_round SET reviewer_attempt_id = 61 WHERE id = 1;

-- @step 25c decided_at нельзя переписать отдельно
-- @expect error reviewer decision is terminal
UPDATE finding_round SET decided_at = 131 WHERE id = 1;

-- @step 26 finding_round нельзя удалить
-- @expect error cannot be deleted
DELETE FROM finding_round WHERE id = 1;

-- @step 27 INSERT OR REPLACE не переписывает историю круга
-- @expect error cannot be deleted
INSERT OR REPLACE INTO finding_round (
  id, campaign_id, run_id, finding_id, round_no, round_id, owner_lane_id,
  entry_kind, disposition, author_attempt_id
) VALUES (1, 10, 1, 30, 2, 20, 40, 'issued', 'rejected', 51);

-- @step 28 все три audit-факта сохранились
-- @expect rows-json [["{\"finding\":\"F-1\"}","A","verified_fixed",60,130]]
SELECT q.snapshot_json, a.chosen_option, fr.reviewer_decision,
       fr.reviewer_attempt_id, fr.decided_at
FROM human_question q
JOIN human_answer a ON a.question_id = q.id
JOIN finding_round fr ON fr.id = 1;

-- @step 30 событие сохраняется append-only
-- @expect ok
INSERT INTO run_event(run_id, kind, payload_json, core_version, created_at)
VALUES (1, 'test', '{}', 'v1', 100);

-- @step 31 событие нельзя обновить
-- @expect error append-only
UPDATE run_event SET payload_json = '{"changed":true}' WHERE id = 1;

-- @step 32 событие нельзя удалить
-- @expect error append-only
DELETE FROM run_event WHERE id = 1;

-- @step 33 REPLACE не обходит append-only событие
-- @expect error append-only
INSERT OR REPLACE INTO run_event(
  id, run_id, kind, payload_json, core_version, created_at
) VALUES (1, 1, 'changed', '{}', 'v2', 200);
