-- Scope: составные ключи review-домена (находка C-06 свода ревью P1-A).
-- Проверяет конструкции db-schema.md §4, §5.2–§5.5 и §5.3.1: связь, у которой
-- родитель принадлежит другой кампании, другому прогону или другой стадии,
-- должна отвергаться базой, а не жить незамеченной.
--
-- Что прогон доказывает: какие сочетания ссылок база принимает. Чего не
-- доказывает: связи, осознанно оставленные коду (§5.3.1) — для них здесь есть
-- только recovery-запрос, но не запрет.
--
-- Запуск: python3 scripts/checks/run-sql-check.py scripts/checks/scope-keys.sql
-- Ожидания машинные (`-- @expect`), несовпадение даёт код возврата 1.

PRAGMA foreign_keys = ON;

CREATE TABLE run (id INTEGER PRIMARY KEY AUTOINCREMENT);
CREATE TABLE run_event (id INTEGER PRIMARY KEY AUTOINCREMENT);
CREATE TABLE attempt_role (role TEXT PRIMARY KEY);
CREATE TABLE attempt_outcome (outcome TEXT PRIMARY KEY);
CREATE TABLE review_round_kind (kind TEXT PRIMARY KEY);
CREATE TABLE link_type (link_type TEXT PRIMARY KEY);

CREATE TABLE branch (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES run(id),
  UNIQUE (id, run_id)
);

CREATE TABLE stage_execution (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id    INTEGER NOT NULL REFERENCES run(id),
  branch_id INTEGER NOT NULL REFERENCES branch(id),
  UNIQUE (id, run_id),
  FOREIGN KEY (branch_id, run_id) REFERENCES branch(id, run_id)
);

CREATE TABLE review_subject (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id   INTEGER NOT NULL REFERENCES run(id),
  revision TEXT    NOT NULL,
  UNIQUE (id, revision)
);

CREATE TABLE review_campaign (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id     INTEGER NOT NULL REFERENCES run(id),
  stage_id   INTEGER NOT NULL REFERENCES stage_execution(id),
  subject_id INTEGER NOT NULL REFERENCES review_subject(id),
  UNIQUE (stage_id, id),
  UNIQUE (id, run_id),
  UNIQUE (id, subject_id)
);

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

CREATE TABLE step_attempt (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id           INTEGER NOT NULL REFERENCES run(id),
  stage_id         INTEGER NOT NULL REFERENCES stage_execution(id),
  role             TEXT    NOT NULL REFERENCES attempt_role(role),
  campaign_id      INTEGER REFERENCES review_campaign(id),
  round_id         INTEGER REFERENCES review_round(id),
  lane_id          INTEGER REFERENCES review_lane(id),
  subject_revision TEXT,
  outcome          TEXT REFERENCES attempt_outcome(outcome),
  FOREIGN KEY (stage_id, run_id)      REFERENCES stage_execution(id, run_id),
  FOREIGN KEY (stage_id, campaign_id) REFERENCES review_campaign(stage_id, id),
  FOREIGN KEY (campaign_id, round_id) REFERENCES review_round(campaign_id, id),
  FOREIGN KEY (campaign_id, lane_id)  REFERENCES review_lane(campaign_id, id)
);

CREATE UNIQUE INDEX ux_attempt_id_campaign ON step_attempt (id, campaign_id);
CREATE UNIQUE INDEX ux_attempt_id_revision ON step_attempt (id, subject_revision);
CREATE UNIQUE INDEX ux_attempt_id_stage    ON step_attempt (id, stage_id);
CREATE UNIQUE INDEX ux_attempt_id_role     ON step_attempt (id, role);
CREATE UNIQUE INDEX ux_attempt_id_outcome  ON step_attempt (id, outcome);

CREATE TABLE author_revision (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id     INTEGER NOT NULL REFERENCES review_campaign(id),
  stage_id        INTEGER NOT NULL REFERENCES stage_execution(id),
  revision_no     INTEGER NOT NULL,
  attempt_id      INTEGER NOT NULL,
  attempt_role    TEXT    NOT NULL,
  attempt_outcome TEXT    NOT NULL,
  UNIQUE (campaign_id, revision_no),
  UNIQUE (attempt_id),
  CHECK (attempt_role = 'author'),
  CHECK (attempt_outcome = 'succeeded'),
  FOREIGN KEY (attempt_id, attempt_role)    REFERENCES step_attempt(id, role),
  FOREIGN KEY (attempt_id, attempt_outcome) REFERENCES step_attempt(id, outcome),
  FOREIGN KEY (stage_id, campaign_id)       REFERENCES review_campaign(stage_id, id),
  FOREIGN KEY (attempt_id, stage_id)        REFERENCES step_attempt(id, stage_id)
);

CREATE TABLE review_observation (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER NOT NULL REFERENCES review_campaign(id),
  round_id    INTEGER NOT NULL REFERENCES review_round(id),
  lane_id     INTEGER NOT NULL REFERENCES review_lane(id),
  attempt_id  INTEGER NOT NULL REFERENCES step_attempt(id),
  subject_id  INTEGER NOT NULL REFERENCES review_subject(id),
  revision    TEXT    NOT NULL,
  seq         INTEGER NOT NULL,
  UNIQUE (campaign_id, seq),
  UNIQUE (id, campaign_id),
  UNIQUE (id, revision),
  FOREIGN KEY (campaign_id, round_id)   REFERENCES review_round(campaign_id, id),
  FOREIGN KEY (campaign_id, lane_id)    REFERENCES review_lane(campaign_id, id),
  FOREIGN KEY (campaign_id, subject_id) REFERENCES review_campaign(id, subject_id),
  FOREIGN KEY (attempt_id, campaign_id) REFERENCES step_attempt(id, campaign_id),
  FOREIGN KEY (attempt_id, revision)    REFERENCES step_attempt(id, subject_revision)
);

CREATE TABLE finding (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id               INTEGER NOT NULL REFERENCES run(id),
  subject_id           INTEGER NOT NULL REFERENCES review_subject(id),
  first_campaign_id    INTEGER NOT NULL REFERENCES review_campaign(id),
  first_round_id       INTEGER NOT NULL REFERENCES review_round(id),
  first_observation_id INTEGER NOT NULL REFERENCES review_observation(id),
  first_revision       TEXT    NOT NULL,
  first_owner_lane_id  INTEGER NOT NULL REFERENCES review_lane(id),
  UNIQUE (first_observation_id),
  UNIQUE (id, run_id),
  FOREIGN KEY (first_campaign_id, run_id)     REFERENCES review_campaign(id, run_id),
  FOREIGN KEY (first_campaign_id, subject_id) REFERENCES review_campaign(id, subject_id),
  FOREIGN KEY (first_campaign_id, first_round_id)
      REFERENCES review_round(campaign_id, id),
  FOREIGN KEY (first_campaign_id, first_owner_lane_id)
      REFERENCES review_lane(campaign_id, id),
  FOREIGN KEY (first_observation_id, first_campaign_id)
      REFERENCES review_observation(id, campaign_id),
  FOREIGN KEY (first_observation_id, first_revision)
      REFERENCES review_observation(id, revision)
);

CREATE TABLE finding_round (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id         INTEGER NOT NULL REFERENCES review_campaign(id),
  finding_id          INTEGER NOT NULL REFERENCES finding(id),
  round_no            INTEGER NOT NULL,
  owner_lane_id       INTEGER NOT NULL REFERENCES review_lane(id),
  reviewer_attempt_id INTEGER REFERENCES step_attempt(id),
  UNIQUE (campaign_id, finding_id, round_no),
  FOREIGN KEY (campaign_id, round_no)
      REFERENCES review_round(campaign_id, round_no),
  FOREIGN KEY (campaign_id, owner_lane_id)
      REFERENCES review_lane(campaign_id, id),
  FOREIGN KEY (reviewer_attempt_id, campaign_id)
      REFERENCES step_attempt(id, campaign_id)
);

CREATE TABLE finding_observation_link (
  observation_id INTEGER PRIMARY KEY REFERENCES review_observation(id),
  finding_id     INTEGER NOT NULL REFERENCES finding(id),
  link_type      TEXT    NOT NULL REFERENCES link_type(link_type)
);

-- === данные ===

INSERT INTO attempt_role(role) VALUES ('author'),('reviewer'),('planner'),('reconciler');
INSERT INTO attempt_outcome(outcome) VALUES ('succeeded'),('failed');
INSERT INTO review_round_kind(kind) VALUES ('discovery'),('fix_check');
INSERT INTO link_type(link_type) VALUES ('first_seen'),('recurrence');
INSERT INTO run DEFAULT VALUES;                       -- 1
INSERT INTO run DEFAULT VALUES;                       -- 2
INSERT INTO branch(id, run_id) VALUES (1, 1), (2, 2);
INSERT INTO stage_execution(id, run_id, branch_id) VALUES (1, 1, 1), (2, 2, 2);
INSERT INTO review_subject(id, run_id, revision) VALUES (1, 1, 'sha-1'), (2, 2, 'sha-2');
INSERT INTO review_campaign(id, run_id, stage_id, subject_id) VALUES (1, 1, 1, 1), (2, 2, 2, 2);
INSERT INTO review_round(id, campaign_id, round_no, kind) VALUES
  (1, 1, 1, 'discovery'), (2, 2, 1, 'discovery'), (3, 1, 2, 'fix_check');
INSERT INTO review_lane(id, campaign_id, run_id, lane_index) VALUES
  (1, 1, 1, 0), (2, 2, 2, 0);
INSERT INTO step_attempt(id, run_id, stage_id, role, campaign_id, round_id, lane_id, subject_revision, outcome)
VALUES (1, 1, 1, 'reviewer', 1, 1, 1, 'sha-1', 'succeeded'),
       (2, 2, 2, 'reviewer', 2, 2, 2, 'sha-2', 'succeeded'),
       (3, 1, 1, 'author',   NULL, NULL, NULL, NULL, 'succeeded');

-- @step 01 стадия в прогоне своей ветки
-- @expect ok
INSERT INTO stage_execution(id, run_id, branch_id) VALUES (3, 1, 1);

-- @step 02 стадия, чья ветка принадлежит другому прогону
-- @expect error FOREIGN KEY
INSERT INTO stage_execution(id, run_id, branch_id) VALUES (4, 1, 2);

-- @step 03 попытка на стадии другого прогона
-- @expect error FOREIGN KEY
INSERT INTO step_attempt(id, run_id, stage_id, role, campaign_id, round_id, lane_id, subject_revision, outcome)
VALUES (4, 2, 1, 'reviewer', 1, 1, 1, 'sha-1', NULL);

-- @step 04 наблюдение своей кампании
-- @expect ok
INSERT INTO review_observation(id, campaign_id, round_id, lane_id, attempt_id, subject_id, revision, seq)
VALUES (1, 1, 1, 1, 1, 1, 'sha-1', 1);

-- @step 05 наблюдение: круг кампании 1, слот кампании 2
-- @expect error FOREIGN KEY
INSERT INTO review_observation(id, campaign_id, round_id, lane_id, attempt_id, subject_id, revision, seq)
VALUES (2, 1, 1, 2, 1, 1, 'sha-1', 2);

-- @step 06 наблюдение: круг другой кампании
-- @expect error FOREIGN KEY
INSERT INTO review_observation(id, campaign_id, round_id, lane_id, attempt_id, subject_id, revision, seq)
VALUES (3, 1, 2, 1, 1, 1, 'sha-1', 3);

-- @step 07 наблюдение: попытка другого прогона
-- @expect error FOREIGN KEY
INSERT INTO review_observation(id, campaign_id, round_id, lane_id, attempt_id, subject_id, revision, seq)
VALUES (4, 1, 1, 1, 2, 1, 'sha-1', 4);

-- @step 08 наблюдение: предмет, которого кампания не проверяет
-- @expect error FOREIGN KEY
INSERT INTO review_observation(id, campaign_id, round_id, lane_id, attempt_id, subject_id, revision, seq)
VALUES (5, 1, 1, 1, 1, 2, 'sha-1', 5);

-- @step 09 наблюдение: ревизия не та, которую получила попытка
-- @expect error FOREIGN KEY
INSERT INTO review_observation(id, campaign_id, round_id, lane_id, attempt_id, subject_id, revision, seq)
VALUES (6, 1, 1, 1, 1, 1, 'sha-9', 6);

-- @step 10 личность по своему первому наблюдению
-- @expect ok
INSERT INTO finding(id, run_id, subject_id, first_campaign_id, first_round_id,
                    first_observation_id, first_revision, first_owner_lane_id)
VALUES (1, 1, 1, 1, 1, 1, 'sha-1', 1);

-- Ещё наблюдения: своё в кампании 2 и два свободных в кампании 1.
INSERT INTO review_observation(id, campaign_id, round_id, lane_id, attempt_id, subject_id, revision, seq)
VALUES (7, 2, 2, 2, 2, 2, 'sha-2', 1),
       (8, 1, 1, 1, 1, 1, 'sha-1', 8),
       (9, 1, 1, 1, 1, 1, 'sha-1', 9);

-- @step 11 личность: первое наблюдение из другой кампании
-- @expect error FOREIGN KEY
INSERT INTO finding(id, run_id, subject_id, first_campaign_id, first_round_id,
                    first_observation_id, first_revision, first_owner_lane_id)
VALUES (2, 1, 1, 1, 1, 7, 'sha-2', 1);

-- @step 12 личность: прогон не совпадает с прогоном первой кампании
-- @expect error FOREIGN KEY
INSERT INTO finding(id, run_id, subject_id, first_campaign_id, first_round_id,
                    first_observation_id, first_revision, first_owner_lane_id)
VALUES (3, 2, 1, 1, 1, 8, 'sha-1', 1);

-- @step 13 личность: слот-владелец из другой кампании
-- @expect error FOREIGN KEY
INSERT INTO finding(id, run_id, subject_id, first_campaign_id, first_round_id,
                    first_observation_id, first_revision, first_owner_lane_id)
VALUES (4, 1, 1, 1, 1, 9, 'sha-1', 2);

-- @step 14 строка круга по замечанию в своей кампании
-- @expect ok
INSERT INTO finding_round(id, campaign_id, finding_id, round_no, owner_lane_id, reviewer_attempt_id)
VALUES (1, 1, 1, 1, 1, 1);

-- @step 15 C-06a: round_no существует только в другой кампании
-- @expect error FOREIGN KEY
INSERT INTO finding_round(id, campaign_id, finding_id, round_no, owner_lane_id, reviewer_attempt_id)
VALUES (2, 2, 1, 2, 2, 2);

-- @step 16 строка круга: попытка ревьюера другой кампании
-- @expect error FOREIGN KEY
INSERT INTO finding_round(id, campaign_id, finding_id, round_no, owner_lane_id, reviewer_attempt_id)
VALUES (3, 1, 1, 2, 1, 2);

-- @step 17 строка круга: владелец из другой кампании
-- @expect error FOREIGN KEY
INSERT INTO finding_round(id, campaign_id, finding_id, round_no, owner_lane_id, reviewer_attempt_id)
VALUES (4, 1, 1, 2, 2, 1);

-- @step 18 авторская правка на стадии своей кампании
-- @expect ok
INSERT INTO author_revision(id, campaign_id, stage_id, revision_no, attempt_id, attempt_role, attempt_outcome)
VALUES (1, 1, 1, 1, 3, 'author', 'succeeded');

-- Ещё авторские попытки: одна на чужой стадии, одна на своей.
INSERT INTO step_attempt(id, run_id, stage_id, role, campaign_id, round_id, lane_id, subject_revision, outcome)
VALUES (5, 2, 2, 'author', NULL, NULL, NULL, NULL, 'succeeded'),
       (6, 1, 1, 'author', NULL, NULL, NULL, NULL, 'succeeded');

-- @step 19 авторская правка: попытка с другой стадии
-- @expect error FOREIGN KEY
INSERT INTO author_revision(id, campaign_id, stage_id, revision_no, attempt_id, attempt_role, attempt_outcome)
VALUES (2, 1, 1, 2, 5, 'author', 'succeeded');

-- @step 20 авторская правка: стадия не та, что у кампании
-- @expect error FOREIGN KEY
INSERT INTO author_revision(id, campaign_id, stage_id, revision_no, attempt_id, attempt_role, attempt_outcome)
VALUES (3, 1, 2, 3, 6, 'author', 'succeeded');

-- Связь наблюдения и личности разных прогонов база не запрещает — §5.3.1
-- относит её к коду. Здесь проверяется, что recovery-запрос её находит.
INSERT INTO finding(id, run_id, subject_id, first_campaign_id, first_round_id,
                    first_observation_id, first_revision, first_owner_lane_id)
VALUES (5, 2, 2, 2, 2, 7, 'sha-2', 2);
INSERT INTO finding_observation_link(observation_id, finding_id, link_type)
VALUES (1, 5, 'recurrence');

-- @step 21 recovery-запрос ловит связь между прогонами
-- @expect rows-json [[1]]
SELECT l.observation_id
  FROM finding_observation_link l
  JOIN review_observation o ON o.id = l.observation_id
  JOIN review_campaign    c ON c.id = o.campaign_id
  JOIN finding            f ON f.id = l.finding_id
 WHERE f.run_id <> c.run_id;

-- @step 22 foreign_key_check
-- @expect empty
PRAGMA foreign_key_check;
