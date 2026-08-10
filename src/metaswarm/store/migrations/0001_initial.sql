CREATE TABLE severity_scale (
  severity TEXT PRIMARY KEY,
  rank     INTEGER NOT NULL UNIQUE
);

INSERT INTO severity_scale(severity, rank) VALUES
  ('low', 10), ('medium', 20), ('high', 30), ('critical', 40);

CREATE TABLE attempt_outcome         (outcome   TEXT PRIMARY KEY);

CREATE TABLE attempt_role            (role      TEXT PRIMARY KEY);

CREATE TABLE heartbeat_source        (source    TEXT PRIMARY KEY);

CREATE TABLE branch_kind             (kind      TEXT PRIMARY KEY);

CREATE TABLE branch_state            (state     TEXT PRIMARY KEY);

CREATE TABLE run_terminal_state      (state     TEXT PRIMARY KEY);

CREATE TABLE campaign_state          (state     TEXT PRIMARY KEY);

CREATE TABLE review_round_kind       (kind      TEXT PRIMARY KEY);

CREATE TABLE round_result            (result    TEXT PRIMARY KEY);

CREATE TABLE finding_round_entry_kind(entry_kind TEXT PRIMARY KEY);

CREATE TABLE subject_kind            (kind      TEXT PRIMARY KEY);

CREATE TABLE link_type               (link_type TEXT PRIMARY KEY);

CREATE TABLE disposition             (value     TEXT PRIMARY KEY);

CREATE TABLE reviewer_decision       (value     TEXT PRIMARY KEY);

CREATE TABLE resolution_authority    (value     TEXT PRIMARY KEY);

CREATE TABLE blocker_kind            (kind      TEXT PRIMARY KEY);

CREATE TABLE task_state              (state     TEXT PRIMARY KEY);

CREATE TABLE title_authority         (authority TEXT PRIMARY KEY);

CREATE TABLE question_reason         (reason    TEXT PRIMARY KEY);

CREATE TABLE transport_kind          (transport TEXT PRIMARY KEY);

CREATE TABLE artifact_kind           (kind      TEXT PRIMARY KEY);

CREATE TABLE artifact_producer       (producer  TEXT PRIMARY KEY);

CREATE TABLE verification_purpose    (purpose   TEXT PRIMARY KEY);

CREATE TABLE verification_plan_source(source   TEXT PRIMARY KEY);

CREATE TABLE verification_status     (status    TEXT PRIMARY KEY);

CREATE TABLE schema_migration (
  version      INTEGER PRIMARY KEY,
  applied_at   INTEGER NOT NULL,
  core_version TEXT    NOT NULL
);

-- Одна строка на запуск процесса сервиса. Точка отсчёта монотонных часов
-- и якорь recovery: попытки прошлых эпох по определению не наши.
CREATE TABLE service_epoch (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at   INTEGER NOT NULL,
  ended_at     INTEGER,
  core_version TEXT    NOT NULL,
  schema_version INTEGER NOT NULL,
  pid          INTEGER NOT NULL,
  boot_id      TEXT,               -- /proc/sys/kernel/random/boot_id, если есть
  host         TEXT    NOT NULL
);

CREATE TABLE run (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  public_id            TEXT    NOT NULL UNIQUE,        -- R-260802-1
  flow_id              TEXT    NOT NULL,
  flow_hash            TEXT    NOT NULL,               -- canonical JSON hash
  project_config_hash  TEXT    NOT NULL,
  profiles_config_hash TEXT    NOT NULL,
  core_version         TEXT    NOT NULL,
  schema_version       INTEGER NOT NULL,
  instance_profile     TEXT    NOT NULL,
  code_repo_path       TEXT    NOT NULL,
  code_sha             TEXT    NOT NULL,
  task_text            TEXT    NOT NULL,
  created_at           INTEGER NOT NULL,
  -- Ниже — факты и намерения, а не состояние. Состояние вычисляется (см. §6.5).
  pause_requested_at   INTEGER,
  cancel_requested_at  INTEGER,
  finished_at          INTEGER,
  terminal_state       TEXT REFERENCES run_terminal_state(state)
);

CREATE TABLE branch (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id     INTEGER NOT NULL REFERENCES run(id),
  public_id  TEXT    NOT NULL,                        -- B-main, B-task-7
  kind       TEXT    NOT NULL REFERENCES branch_kind(kind), -- pipeline | task
  task_id    INTEGER REFERENCES task(id),
  state      TEXT    NOT NULL REFERENCES branch_state(state),
  created_at INTEGER NOT NULL,
  UNIQUE (run_id, public_id),
  UNIQUE (id, run_id),                              -- под scope-FK стадии
  CHECK ((kind = 'task') = (task_id IS NOT NULL))
);

CREATE TABLE stage_execution (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id               INTEGER NOT NULL REFERENCES run(id),
  branch_id            INTEGER NOT NULL REFERENCES branch(id),
  stage_key            TEXT    NOT NULL,              -- ключ стадии во flow
  ordinal              INTEGER NOT NULL,              -- перезапуск = следующий ordinal
  state                TEXT    NOT NULL REFERENCES branch_state(state),
  max_author_revisions INTEGER NOT NULL,
  severity_threshold   TEXT    NOT NULL REFERENCES severity_scale(severity),
  started_at           INTEGER,
  finished_at          INTEGER,
  UNIQUE (branch_id, stage_key, ordinal),
  UNIQUE (id, run_id),                              -- под scope-FK попытки
  -- Стадия принадлежит прогону своей ветки, а не любому.
  FOREIGN KEY (branch_id, run_id) REFERENCES branch(id, run_id),
  CHECK (max_author_revisions >= 1)
);

CREATE TABLE step_attempt (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  public_id          TEXT    NOT NULL UNIQUE,
  run_id             INTEGER NOT NULL REFERENCES run(id),
  stage_id           INTEGER NOT NULL REFERENCES stage_execution(id),
  role               TEXT    NOT NULL REFERENCES attempt_role(role),
                                                        -- author | reviewer | planner | reconciler
  campaign_id        INTEGER REFERENCES review_campaign(id),     -- scope-якорь
  round_id           INTEGER REFERENCES review_round(id),
  lane_id            INTEGER REFERENCES review_lane(id),          -- слот
  lane_assignment_id INTEGER REFERENCES lane_assignment(id),      -- исполнитель
  subject_revision   TEXT,                                        -- что реально
                                                                  -- получил агент
  session_id         INTEGER REFERENCES logical_session(id),
  -- вход: неизменяем
  profile_id         TEXT    NOT NULL,
  requested_model    TEXT    NOT NULL,
  prompt_template_id TEXT    NOT NULL,
  prompt_hash        TEXT    NOT NULL,
  rubric_id          TEXT,
  rubric_hash        TEXT,
  input_sha          TEXT,
  input_refs_json    TEXT    NOT NULL,
  manifest_json      TEXT    NOT NULL,
  started_at         INTEGER NOT NULL,
  -- результат: пишется один раз, при завершении
  outcome            TEXT REFERENCES attempt_outcome(outcome),
  outcome_detail     TEXT,
  actual_model       TEXT,
  output_sha         TEXT,
  finished_at        INTEGER,
  transcript_path    TEXT,
  transcript_digest  TEXT,
  -- Все review-координаты попытки — из одной кампании и одной стадии.
  -- Независимые FK этого не дают: круг кампании A со слотом кампании B
  -- проходит каждый из них по отдельности.
  FOREIGN KEY (stage_id, run_id)            REFERENCES stage_execution(id, run_id),
  FOREIGN KEY (stage_id, campaign_id)       REFERENCES review_campaign(stage_id, id),
  FOREIGN KEY (campaign_id, round_id)       REFERENCES review_round(campaign_id, id),
  FOREIGN KEY (campaign_id, lane_id)        REFERENCES review_lane(campaign_id, id),
  -- Профиль в ключе обязателен: без него попытка ссылается на назначение
  -- слота, но исполняется другим профилем, и roster говорит одно, а
  -- exposure — другое.
  FOREIGN KEY (lane_assignment_id, lane_id, profile_id)
      REFERENCES lane_assignment(id, lane_id, profile_id),
  CHECK ((lane_id IS NULL) = (lane_assignment_id IS NULL)),
  CHECK ((campaign_id IS NULL) = (round_id IS NULL)),
  CHECK (lane_id IS NULL OR round_id IS NOT NULL),
  -- Форма строки по роли: у ревьюера обязаны быть круг и линия, у
  -- reconciler'а — круг, а у автора и планировщика review-координат нет
  -- вовсе. Без этого reviewer-попытка без линии тихо не попадает ни в один
  -- гейт участия.
  CHECK (role <> 'reviewer'   OR (round_id IS NOT NULL AND lane_id IS NOT NULL)),
  CHECK (role <> 'reconciler' OR (round_id IS NOT NULL
                                  AND lane_id IS NULL
                                  AND lane_assignment_id IS NULL)),
  -- Обе review-роли получают ревизию предмета: без неё строка допуска не
  -- на что сослаться, и ошибка всплывёт только в reserve.
  CHECK (role NOT IN ('reviewer', 'reconciler') OR subject_revision IS NOT NULL),
  CHECK (role NOT IN ('author', 'planner')
         OR (campaign_id IS NULL AND round_id IS NULL
             AND lane_id IS NULL AND lane_assignment_id IS NULL))
);

-- Под scope-FK из reviewer_exposure: первая попытка допуска принадлежит той
-- же кампании и тому же прогону, а её профиль — тот, чью пару записал допуск.
CREATE UNIQUE INDEX ux_attempt_id_campaign ON step_attempt (id, campaign_id);

CREATE UNIQUE INDEX ux_attempt_id_run      ON step_attempt (id, run_id);

CREATE UNIQUE INDEX ux_attempt_id_profile  ON step_attempt (id, profile_id);

CREATE UNIQUE INDEX ux_attempt_id_revision ON step_attempt (id, subject_revision);

CREATE UNIQUE INDEX ux_attempt_id_stage    ON step_attempt (id, stage_id);

CREATE UNIQUE INDEX ux_attempt_id_round    ON step_attempt (id, round_id);

CREATE UNIQUE INDEX ux_attempt_id_lane     ON step_attempt (id, lane_id);

-- Инвариант 19: не более одной активной попытки на шаг или линию.
CREATE UNIQUE INDEX ux_attempt_active
  ON step_attempt (stage_id, role, COALESCE(lane_id, -1))
  WHERE outcome IS NULL;

-- Попытка — durable intent до spawn, поэтому INSERT всегда создаёт active
-- строку. Терминальные поля нельзя проставить заранее и тем самым обойти
-- recovery окна intent -> effect -> reconcile.
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

-- Единственный UPDATE строки попытки — active -> terminal. Все identity,
-- scope и input-поля остаются прежними; finished_at обязателен. Heartbeat и
-- process status живут в attempt_liveness и этого UPDATE не требуют.
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

-- Изменяемая часть попытки вынесена отдельно: heartbeat пишется каждые
-- несколько секунд, и он не должен трогать запись результата.
CREATE TABLE attempt_liveness (
  attempt_id             INTEGER PRIMARY KEY REFERENCES step_attempt(id),
  service_epoch_id       INTEGER NOT NULL REFERENCES service_epoch(id),
  pid                    INTEGER NOT NULL,
  pgid                   INTEGER NOT NULL,
  proc_start_ticks       INTEGER NOT NULL,   -- поле 22 из /proc/<pid>/stat
  started_mono_ns        INTEGER NOT NULL,
  last_heartbeat_mono_ns INTEGER NOT NULL,
  last_heartbeat_at      INTEGER NOT NULL,
  heartbeat_source       TEXT    NOT NULL REFERENCES heartbeat_source(source)
                                             -- stdout | stderr | fs
);

CREATE TABLE logical_session (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id            INTEGER NOT NULL REFERENCES run(id),
  profile_id        TEXT    NOT NULL,
  provider          TEXT    NOT NULL,
  model             TEXT    NOT NULL,
  vendor_session_id TEXT,
  purpose           TEXT    NOT NULL,       -- assignment:12 | author:stage:7
  created_at        INTEGER NOT NULL,
  closed_at         INTEGER
);

CREATE TABLE review_subject (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id            INTEGER NOT NULL REFERENCES run(id),
  kind              TEXT    NOT NULL REFERENCES subject_kind(kind),
  target_ref        TEXT    NOT NULL,
  revision          TEXT    NOT NULL,
  parent_subject_id INTEGER REFERENCES review_subject(id),
  created_at        INTEGER NOT NULL,
  UNIQUE (id, run_id),                   -- под scope-FK кампании и родителя
  -- Вложенность не выходит за прогон: иначе рекурсия §5.1 смешает findings
  -- разных запусков.
  FOREIGN KEY (parent_subject_id, run_id) REFERENCES review_subject(id, run_id),
  CHECK (parent_subject_id IS NULL OR parent_subject_id <> id)
);

CREATE INDEX ix_subject_parent ON review_subject (parent_subject_id);

CREATE TABLE review_campaign (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  public_id          TEXT    NOT NULL UNIQUE,       -- C-4
  run_id             INTEGER NOT NULL REFERENCES run(id),
  stage_id           INTEGER NOT NULL REFERENCES stage_execution(id),
  subject_id         INTEGER NOT NULL REFERENCES review_subject(id),
  ordinal            INTEGER NOT NULL,              -- какой кворум по счёту на стадии
  severity_threshold TEXT    NOT NULL REFERENCES severity_scale(severity),
  policy_version     TEXT    NOT NULL,
  expected_lane_count INTEGER NOT NULL,             -- заявленный размер кворума
  state              TEXT    NOT NULL REFERENCES campaign_state(state),
  opened_at          INTEGER NOT NULL,
  closed_at          INTEGER,
  close_reason       TEXT,
  UNIQUE (stage_id, ordinal),
  UNIQUE (stage_id, id),                           -- под scope-FK из step_attempt
  UNIQUE (id, subject_id),                         -- под scope-FK из exposure
  UNIQUE (id, run_id),                             -- под scope-FK из review_lane
  CHECK (expected_lane_count >= 1),
  -- Кампания — корень scope, и он обязан быть согласован в самом корне:
  -- иначе все ключи ниже честно разнесут противоречие дальше.
  FOREIGN KEY (stage_id, run_id)   REFERENCES stage_execution(id, run_id),
  FOREIGN KEY (subject_id, run_id) REFERENCES review_subject(id, run_id),
  CHECK (
    (state IN ('closed_clean', 'closed_escalated', 'closed_cancelled'))
    = (closed_at IS NOT NULL)
  )
);

-- Допустимые переходы кампании — данные, а не комментарий. Таблица закрыта:
-- у трёх терминальных состояний исходящих строк нет.
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

-- Кампания рождается только в discovery: остальные состояния достижимы
-- переходами, и обойти их прямой вставкой нельзя.
CREATE TRIGGER trg_campaign_initial_state
BEFORE INSERT ON review_campaign
WHEN NEW.state <> 'discovery'
BEGIN
  SELECT RAISE(ABORT, 'campaign is created in discovery');
END;

-- Identity и снимок кампании неизменяемы: меняться могут только состояние и
-- поля закрытия. Иначе `expected_lane_count` правится под фактический roster,
-- и оба гейта полноты замолкают, а снимок порога перестаёт быть снимком.
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

-- Слот кворума: позиция, а не исполнитель. Создаётся при открытии кампании
-- и дальше не меняется. Размер кворума = число слотов кампании.
CREATE TABLE review_lane (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER NOT NULL REFERENCES review_campaign(id),
  run_id      INTEGER NOT NULL REFERENCES run(id),  -- якорь резолвинга профилей
  lane_index  INTEGER NOT NULL,                     -- 0,1,… минимальный = владелец
  UNIQUE (campaign_id, lane_index),
  UNIQUE (campaign_id, id),                         -- под scope-FK ниже
  UNIQUE (id, run_id),                              -- под scope-FK назначения
  FOREIGN KEY (campaign_id, run_id) REFERENCES review_campaign(id, run_id)
);

-- Индекс слота лежит внутри заявленного кворума: 0..expected_lane_count-1.
-- Без этого «слоты 0 и 2» при кворуме 2 выглядят полным набором.
CREATE TRIGGER trg_lane_index_bounds
BEFORE INSERT ON review_lane
BEGIN
  SELECT RAISE(ABORT, 'lane_index outside declared quorum')
  WHERE NEW.lane_index < 0
     OR NEW.lane_index >= (SELECT c.expected_lane_count
                             FROM review_campaign c WHERE c.id = NEW.campaign_id);
END;

-- «Дальше не меняется» — это триггеры, а не комментарий. Без них слот
-- переносится в другую кампанию одним UPDATE, а бездетный — удаляется.
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

-- Кто исполняет слот. Замена линии человеком — новое поколение, а не UPDATE
-- прежнего: append-only, поэтому прежний исполнитель и его попытки остаются
-- в аудите.
CREATE TABLE lane_assignment (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  lane_id         INTEGER NOT NULL REFERENCES review_lane(id),
  run_id          INTEGER NOT NULL REFERENCES run(id),
  generation      INTEGER NOT NULL,                 -- 1,2,… внутри слота
  profile_id      TEXT    NOT NULL,                 -- пара берётся из
                                                    -- run_profile_resolution
  replaces_id     INTEGER UNIQUE REFERENCES lane_assignment(id),
  session_id      INTEGER REFERENCES logical_session(id),
  -- чем разрешена замена; UNIQUE — один ответ, одна замена
  human_answer_id INTEGER UNIQUE REFERENCES human_answer(id),
  event_id        INTEGER NOT NULL REFERENCES run_event(id),
  assigned_at     INTEGER NOT NULL,
  UNIQUE (lane_id, generation),
  -- Прогон назначения — прогон его слота, а профиль обязан быть разрешён
  -- именно в этом прогоне. Без второго ключа назначение с неизвестным
  -- профилем принимается базой и исчезает из effective_roster.
  FOREIGN KEY (lane_id, run_id)    REFERENCES review_lane(id, run_id),
  FOREIGN KEY (run_id, profile_id) REFERENCES run_profile_resolution(run_id, profile_id),
  -- Первое назначение никого не заменяет и не требует ответа человека;
  -- любое последующее — и то, и другое.
  CHECK ((generation = 1) = (replaces_id IS NULL)),
  CHECK ((generation = 1) = (human_answer_id IS NULL))
);

-- Для составного FK из step_attempt: попытка ссылается на поколение, слот и
-- профиль сразу, и разойтись они не могут.
CREATE UNIQUE INDEX ux_lane_assignment_id_lane_profile
  ON lane_assignment (id, lane_id, profile_id);

-- Поколение продолжает цепочку того же слота и ровно предыдущее звено.
-- Без этого generation 3 мог бы сослаться на 1, оставив 2 без преемника —
-- то есть два активных исполнителя одного слота.
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

-- Назначение неизменяемо. Единственная дозапись — привязка логической
-- сессии, и только один раз: NULL → значение.
CREATE TRIGGER trg_lane_assignment_immutable
BEFORE UPDATE ON lane_assignment
WHEN NEW.lane_id         <> OLD.lane_id
  OR NEW.generation      <> OLD.generation
  OR NEW.profile_id      <> OLD.profile_id
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

-- Разрешение человека закрыть круг без этой линии: деградированный кворум
-- (`architecture.md` §7.1.1). Строка привязана к кругу, к слоту той же
-- кампании и к ответу.
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

-- Один ответ человека — одно действие. Два раздельных UNIQUE этого не дают:
-- каждый следит за своей таблицей, и тот же ответ ложится и заменой, и
-- waiver'ом. Проверка межтабличная, поэтому триггер, а не констрейнт.
CREATE TRIGGER trg_lane_assignment_answer_xor
BEFORE INSERT ON lane_assignment
WHEN NEW.human_answer_id IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'answer already spent on a lane waiver')
  WHERE EXISTS (SELECT 1 FROM lane_waiver w
                 WHERE w.human_answer_id = NEW.human_answer_id);
END;

CREATE TRIGGER trg_lane_waiver_answer_xor
BEFORE INSERT ON lane_waiver
BEGIN
  SELECT RAISE(ABORT, 'answer already spent on a lane replacement')
  WHERE EXISTS (SELECT 1 FROM lane_assignment a
                 WHERE a.human_answer_id = NEW.human_answer_id);
END;

-- Effective roster: слоты кампании с текущим исполнителем. Активное
-- назначение — то, которое никто не заменил. Пара provider+model не хранится
-- копией, а выводится из резолвинга прогона — источник правды один.
CREATE VIEW effective_roster AS
SELECT l.campaign_id, l.id AS lane_id, l.lane_index,
       a.id AS assignment_id, a.generation, a.profile_id,
       rp.provider, rp.model
  FROM review_lane l
  JOIN lane_assignment  a ON a.lane_id = l.id
  JOIN review_campaign  c ON c.id = l.campaign_id
  JOIN run_profile_resolution rp
       ON rp.run_id = c.run_id AND rp.profile_id = a.profile_id
 WHERE NOT EXISTS (SELECT 1 FROM lane_assignment s WHERE s.replaces_id = a.id);

-- Круг = одна проверка. Первичный кворум — круг 1.
-- Последовательность: check1 → rev1 → check2 → rev2 → check3 → rev3 → check4.
CREATE TABLE review_round (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER NOT NULL REFERENCES review_campaign(id),
  round_no    INTEGER NOT NULL,
  kind        TEXT    NOT NULL REFERENCES review_round_kind(kind),
                                                   -- discovery | fix_check
  preceding_revision_id INTEGER REFERENCES author_revision(id),
  result      TEXT REFERENCES round_result(result), -- NULL = круг ещё идёт
  opened_at   INTEGER NOT NULL,
  closed_at   INTEGER,
  UNIQUE (campaign_id, round_no),
  UNIQUE (campaign_id, id),                        -- под scope-FK из step_attempt
  UNIQUE (campaign_id, id, round_no),              -- под scope-FK из finding_round
  -- Правка, которую проверяет этот fix_check, — из той же кампании.
  FOREIGN KEY (preceding_revision_id, campaign_id)
      REFERENCES author_revision(id, campaign_id),
  CHECK ((kind = 'discovery') = (preceding_revision_id IS NULL)),
  CHECK ((result IS NULL) = (closed_at IS NULL))
);

CREATE TRIGGER trg_round_initial_state
BEFORE INSERT ON review_round
WHEN NEW.result IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'review_round must start open');
END;

-- Единственный UPDATE — open -> closed; identity и вход круга не меняются.
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

-- Строка появляется ТОЛЬКО когда правка состоялась: попытка succeeded и дала
-- новую ревизию. Это и есть счётчик — и он защищён внешними ключами, а не
-- только дисциплиной вызывающего кода.
CREATE TABLE author_revision (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id          INTEGER NOT NULL REFERENCES review_campaign(id),
  stage_id             INTEGER NOT NULL REFERENCES stage_execution(id),
  revision_no          INTEGER NOT NULL,
  attempt_id           INTEGER NOT NULL,
  attempt_role         TEXT    NOT NULL,
  attempt_outcome      TEXT    NOT NULL,
  input_sha            TEXT,
  output_sha           TEXT,
  artifact_revision_id INTEGER REFERENCES artifact_revision(id),
  completed_at         INTEGER NOT NULL,
  UNIQUE (campaign_id, revision_no),
  UNIQUE (attempt_id),
  UNIQUE (id, campaign_id),                 -- под scope-FK круга

  CHECK (attempt_role = 'author'),
  CHECK (attempt_outcome = 'succeeded'),
  FOREIGN KEY (attempt_id, attempt_role)    REFERENCES step_attempt(id, role),
  FOREIGN KEY (attempt_id, attempt_outcome) REFERENCES step_attempt(id, outcome),
  -- Авторская попытка — с той же стадии, что и кампания. Через campaign_id
  -- это не выражается: у author-попытки review-координат нет по CHECK §4,
  -- поэтому общий якорь — стадия.
  FOREIGN KEY (stage_id, campaign_id)       REFERENCES review_campaign(stage_id, id),
  FOREIGN KEY (attempt_id, stage_id)        REFERENCES step_attempt(id, stage_id)
);

CREATE UNIQUE INDEX ux_attempt_id_role    ON step_attempt (id, role);

CREATE UNIQUE INDEX ux_attempt_id_outcome ON step_attempt (id, outcome);

CREATE VIEW campaign_counters AS
SELECT c.id AS campaign_id,
       (SELECT COUNT(*) FROM author_revision r WHERE r.campaign_id = c.id)
         AS author_revision_count,
       (SELECT COUNT(*) FROM review_round rr
          WHERE rr.campaign_id = c.id AND rr.result IS NOT NULL)
         AS review_check_count
FROM review_campaign c;

CREATE TABLE review_observation (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  public_id          TEXT    NOT NULL UNIQUE,       -- O-4-12
  campaign_id        INTEGER NOT NULL REFERENCES review_campaign(id),
  round_id           INTEGER NOT NULL REFERENCES review_round(id),
  lane_id            INTEGER NOT NULL REFERENCES review_lane(id),
  attempt_id         INTEGER NOT NULL REFERENCES step_attempt(id),
  subject_id         INTEGER NOT NULL REFERENCES review_subject(id),
  revision           TEXT    NOT NULL,
  seq                INTEGER NOT NULL,              -- порядок внутри кампании
  title              TEXT    NOT NULL,
  body               TEXT    NOT NULL,
  file_path          TEXT,
  line_start         INTEGER,
  line_end           INTEGER,
  evidence           TEXT,
  severity_suggested TEXT REFERENCES severity_scale(severity),
  unchanged_from_id  INTEGER REFERENCES review_observation(id),
  severity_effective TEXT    NOT NULL REFERENCES severity_scale(severity),
  dedup_key          TEXT    NOT NULL,
  created_at         INTEGER NOT NULL,
  UNIQUE (campaign_id, seq),
  UNIQUE (id, campaign_id),                    -- под scope-FK личности
  UNIQUE (id, revision),                       -- под scope-FK личности
  UNIQUE (id, round_id),                       -- под scope-FK личности
  -- Круг, слот, попытка и предмет — из той же кампании, а ревизия — та,
  -- которую попытка реально получила. Независимые FK этого не дают:
  -- наблюдение с кругом одной кампании и линией другой проходит их все.
  FOREIGN KEY (campaign_id, round_id)  REFERENCES review_round(campaign_id, id),
  FOREIGN KEY (campaign_id, lane_id)   REFERENCES review_lane(campaign_id, id),
  FOREIGN KEY (campaign_id, subject_id) REFERENCES review_campaign(id, subject_id),
  FOREIGN KEY (attempt_id, campaign_id) REFERENCES step_attempt(id, campaign_id),
  FOREIGN KEY (attempt_id, revision)    REFERENCES step_attempt(id, subject_revision),
  -- Общей кампании мало: попытка обязана быть попыткой ИМЕННО этого круга и
  -- этой линии, иначе наблюдение линии 0 приписывается работе линии 1.
  FOREIGN KEY (attempt_id, round_id)    REFERENCES step_attempt(id, round_id),
  FOREIGN KEY (attempt_id, lane_id)     REFERENCES step_attempt(id, lane_id),
  -- Ровно одно из двух: своя оценка либо ссылка на прежнюю.
  CHECK ((severity_suggested IS NULL) <> (unchanged_from_id IS NULL)),
  -- Своя оценка = она же эффективная.
  CHECK (severity_suggested IS NULL OR severity_suggested = severity_effective),
  CHECK (line_start IS NULL OR line_end IS NULL OR line_end >= line_start)
);

CREATE INDEX ix_observation_round ON review_observation (round_id);

CREATE INDEX ix_observation_dedup ON review_observation (campaign_id, dedup_key);

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

CREATE TRIGGER trg_observation_unchanged_from
BEFORE INSERT ON review_observation
WHEN NEW.unchanged_from_id IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'unchanged_from must point backwards within same campaign')
  WHERE NOT EXISTS (
    SELECT 1 FROM review_observation p
     WHERE p.id = NEW.unchanged_from_id
       AND p.campaign_id = NEW.campaign_id
       AND p.seq < NEW.seq
  );
  -- Наследование обязано быть настоящим: эффективная severity равна
  -- родительской, иначе "подтвердил прежнюю" молча меняет оценку.
  SELECT RAISE(ABORT, 'severity_effective must equal parent severity_effective')
  WHERE NEW.severity_effective <> (
    SELECT p.severity_effective FROM review_observation p
     WHERE p.id = NEW.unchanged_from_id
  );
END;

CREATE TABLE finding (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  public_id            TEXT    NOT NULL,             -- F-17, уникален внутри прогона
  run_id               INTEGER NOT NULL REFERENCES run(id),
  subject_id           INTEGER NOT NULL REFERENCES review_subject(id),
  first_campaign_id    INTEGER NOT NULL REFERENCES review_campaign(id),
  first_round_id       INTEGER NOT NULL REFERENCES review_round(id),
  first_observation_id INTEGER NOT NULL REFERENCES review_observation(id),
  first_revision       TEXT    NOT NULL,
  first_owner_lane_id  INTEGER NOT NULL REFERENCES review_lane(id),
  title                TEXT    NOT NULL,
  title_authority      TEXT    NOT NULL DEFAULT 'runtime'
                      REFERENCES title_authority(authority), -- runtime | human
  title_changed_reason TEXT,
  event_id             INTEGER NOT NULL REFERENCES run_event(id),
  created_at           INTEGER NOT NULL,
  UNIQUE (run_id, public_id),
  UNIQUE (first_observation_id),
  UNIQUE (id, run_id),                         -- под scope-запросы связи
  -- Вся «первая» пятёрка принадлежит одной кампании одного прогона.
  FOREIGN KEY (first_campaign_id, run_id)      REFERENCES review_campaign(id, run_id),
  FOREIGN KEY (first_campaign_id, subject_id)  REFERENCES review_campaign(id, subject_id),
  FOREIGN KEY (first_campaign_id, first_round_id)
      REFERENCES review_round(campaign_id, id),
  FOREIGN KEY (first_campaign_id, first_owner_lane_id)
      REFERENCES review_lane(campaign_id, id),
  FOREIGN KEY (first_observation_id, first_campaign_id)
      REFERENCES review_observation(id, campaign_id),
  FOREIGN KEY (first_observation_id, first_revision)
      REFERENCES review_observation(id, revision),
  -- Первый круг — тот, в котором сделано первое наблюдение, а не любой круг
  -- той же кампании.
  FOREIGN KEY (first_observation_id, first_round_id)
      REFERENCES review_observation(id, round_id),
  CHECK (title_authority = 'runtime' OR title_changed_reason IS NOT NULL)
);

CREATE TABLE finding_observation_link (
  observation_id        INTEGER PRIMARY KEY REFERENCES review_observation(id),
  campaign_id           INTEGER NOT NULL REFERENCES review_campaign(id),
  round_id              INTEGER NOT NULL REFERENCES review_round(id),
  finding_id            INTEGER NOT NULL REFERENCES finding(id),
  link_type             TEXT    NOT NULL REFERENCES link_type(link_type),
  -- Решение принял либо агент, либо человек. Ровно один источник.
  decided_by_attempt_id INTEGER REFERENCES step_attempt(id),
  decided_by_role       TEXT,
  decided_by_outcome    TEXT,
  decided_by_human_answer_id INTEGER REFERENCES human_answer(id),
  reason                TEXT,
  event_id              INTEGER NOT NULL REFERENCES run_event(id),
  created_at            INTEGER NOT NULL,
  -- Наблюдение и решившая попытка — из одной кампании И одного круга: без
  -- второго решение предыдущего круга законно связывает наблюдение текущего.
  FOREIGN KEY (observation_id, campaign_id)
      REFERENCES review_observation(id, campaign_id),
  FOREIGN KEY (observation_id, round_id)
      REFERENCES review_observation(id, round_id),
  FOREIGN KEY (decided_by_attempt_id, campaign_id)
      REFERENCES step_attempt(id, campaign_id),
  FOREIGN KEY (decided_by_attempt_id, round_id)
      REFERENCES step_attempt(id, round_id),
  -- Связь ставит только успешная попытка и только двух ролей — тот же приём
  -- составных FK с денормализацией, что и у author_revision.
  FOREIGN KEY (decided_by_attempt_id, decided_by_role)
      REFERENCES step_attempt(id, role),
  FOREIGN KEY (decided_by_attempt_id, decided_by_outcome)
      REFERENCES step_attempt(id, outcome),
  -- XOR источника и полнота agent-варианта: три его колонки заполняются
  -- только вместе.
  CHECK ((decided_by_attempt_id IS NULL) <> (decided_by_human_answer_id IS NULL)),
  CHECK ((decided_by_attempt_id IS NULL) = (decided_by_role IS NULL)),
  CHECK ((decided_by_attempt_id IS NULL) = (decided_by_outcome IS NULL)),
  CHECK (decided_by_outcome IS NULL OR decided_by_outcome = 'succeeded'),
  CHECK (decided_by_role IS NULL OR decided_by_role IN ('reviewer', 'reconciler')),
  -- Ревьюер напрямую может связать только повтор известного finding;
  -- first_seen, reaffirmation и reopening выдаёт reconciliation либо человек.
  CHECK (decided_by_role IS NULL OR decided_by_role <> 'reviewer'
         OR link_type = 'recurrence'),
  CHECK (link_type <> 'reopening' OR reason IS NOT NULL)
);

-- Прямая связь ревьюера допустима только на его собственном наблюдении и
-- только в fix_check: первичные слепые наблюдения классифицирует
-- reconciliation, у ревьюера в discovery ledger'а нет.
CREATE TRIGGER trg_link_reviewer_direct_path
BEFORE INSERT ON finding_observation_link
WHEN NEW.decided_by_role = 'reviewer'
BEGIN
  SELECT RAISE(ABORT, 'reviewer may link only its own observation')
  WHERE NEW.decided_by_attempt_id <> (
    SELECT o.attempt_id FROM review_observation o WHERE o.id = NEW.observation_id
  );
  SELECT RAISE(ABORT, 'reviewer direct link is allowed only in fix_check')
  WHERE (SELECT r.kind FROM review_round r WHERE r.id = NEW.round_id) <> 'fix_check';
END;

-- Человек связывает наблюдения только там, где это предусмотрено решением
-- Q49: он запасной reconciler при `reconcile_failed` и он же отвечает за
-- переоткрытие при `reopen_human_closed`. Ответ обязан относиться к вопросу
-- этой кампании и одной из двух причин.
CREATE TRIGGER trg_link_human_authority
BEFORE INSERT ON finding_observation_link
WHEN NEW.decided_by_human_answer_id IS NOT NULL
BEGIN
  -- Вопрос — по этой кампании, этому кругу и одной из двух причин.
  SELECT RAISE(ABORT, 'human link requires reconcile_failed or reopen_human_closed answer of this round')
  WHERE NOT EXISTS (
    SELECT 1
      FROM human_answer a
      JOIN human_question q ON q.id = a.question_id
     WHERE a.id = NEW.decided_by_human_answer_id
       AND q.campaign_id = NEW.campaign_id
       AND q.round_id    = NEW.round_id
       AND q.reason IN ('reconcile_failed', 'reopen_human_closed')
  );
  -- И это наблюдение человек действительно видел: одного круга мало, вопрос
  -- может покрывать лишь часть наблюдений.
  SELECT RAISE(ABORT, 'observation was not part of the answered question')
  WHERE NOT EXISTS (
    SELECT 1
      FROM human_answer a
      JOIN human_question_observation qo ON qo.question_id = a.question_id
     WHERE a.id = NEW.decided_by_human_answer_id
       AND qo.observation_id = NEW.observation_id
  );
  -- У reopen-запроса зафиксирован target: чужую личность им переоткрыть нельзя.
  SELECT RAISE(ABORT, 'finding is not the target of the reopen request')
  WHERE EXISTS (
    SELECT 1
      FROM human_answer a
      JOIN human_question_observation qo ON qo.question_id = a.question_id
     WHERE a.id = NEW.decided_by_human_answer_id
       AND qo.observation_id = NEW.observation_id
       AND qo.finding_id IS NOT NULL
       AND qo.finding_id <> NEW.finding_id
  );
END;

CREATE INDEX ix_link_finding ON finding_observation_link (finding_id);

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

CREATE TABLE finding_round (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id         INTEGER NOT NULL REFERENCES review_campaign(id),
  run_id              INTEGER NOT NULL REFERENCES run(id),
  finding_id          INTEGER NOT NULL REFERENCES finding(id),
  round_no            INTEGER NOT NULL,
  round_id            INTEGER NOT NULL REFERENCES review_round(id),
  owner_lane_id       INTEGER NOT NULL REFERENCES review_lane(id),
  entry_kind          TEXT    NOT NULL REFERENCES finding_round_entry_kind(entry_kind),
  disposition         TEXT REFERENCES disposition(value),
  disposition_reason  TEXT,
  author_attempt_id   INTEGER REFERENCES step_attempt(id),
  reviewer_decision   TEXT REFERENCES reviewer_decision(value),
  reviewer_attempt_id INTEGER REFERENCES step_attempt(id),
  decided_at          INTEGER,
  UNIQUE (campaign_id, finding_id, round_no),
  -- C-06a: без этого ключа строка ссылается на round_no, существующий только
  -- в другой кампании, и при этом ИСЧЕЗАЕТ из strict-issued гейта — он
  -- строится на INNER JOIN, так что orphan не диагностируется, а живёт.
  -- Одна связь вместо трёх: она выражает ровно то, что нужно, — кампания,
  -- round_id и round_no описывают ОДИН круг. Пара отдельных ключей это
  -- гарантировала лишь в сумме, а третий был избыточен и размывал тесты.
  FOREIGN KEY (campaign_id, round_id, round_no)
      REFERENCES review_round(campaign_id, id, round_no),
  FOREIGN KEY (campaign_id, owner_lane_id)
      REFERENCES review_lane(campaign_id, id),
  -- Решение владельца — попытка ИМЕННО этого круга. Общей кампании мало:
  -- успешное решение предыдущего круга закрывало бы текущий.
  FOREIGN KEY (reviewer_attempt_id, campaign_id)
      REFERENCES step_attempt(id, campaign_id),
  FOREIGN KEY (reviewer_attempt_id, round_id)
      REFERENCES step_attempt(id, round_id),
  -- Finding и кампания — из одного прогона. Равенства предмета требовать
  -- нельзя: финальная кампания законно работает с findings дочерних предметов.
  FOREIGN KEY (finding_id, run_id)   REFERENCES finding(id, run_id),
  FOREIGN KEY (campaign_id, run_id)  REFERENCES review_campaign(id, run_id),
  -- post_check появился после проверки: авторского и reviewer-ответа ещё нет.
  CHECK (
    entry_kind <> 'post_check'
    OR (disposition IS NULL
        AND disposition_reason IS NULL
        AND author_attempt_id IS NULL
        AND reviewer_decision IS NULL
        AND reviewer_attempt_id IS NULL
        AND decided_at IS NULL)
  ),
  -- Частично записанные пары не считаются состоявшимся ответом.
  CHECK ((disposition IS NULL) = (author_attempt_id IS NULL)),
  CHECK (disposition IS NOT NULL OR disposition_reason IS NULL),
  CHECK ((reviewer_decision IS NULL) = (reviewer_attempt_id IS NULL)),
  CHECK ((reviewer_decision IS NULL) = (decided_at IS NULL)),
  -- Отказ обязан быть обоснован.
  CHECK (disposition NOT IN ('rejected','wont_fix') OR disposition_reason IS NOT NULL),
  -- Инвариант 10: исход ревьюера совместим с disposition автора.
  -- disposition IS NOT NULL внутри — обязательно, см. ниже про NULL в CHECK.
  CHECK (
    reviewer_decision IS NULL
    OR (disposition IS NOT NULL AND (
           (disposition = 'fixed'
            AND reviewer_decision IN ('verified_fixed','still_present'))
        OR (disposition IN ('rejected','wont_fix')
            AND reviewer_decision IN ('accepted_reason','insists'))))
  )
);

CREATE INDEX ix_finding_round_finding ON finding_round (finding_id);

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

-- Справочник обязателен: без FK неизвестное значение resolution проваливает
-- CASE в NULL, и оба CHECK ниже перестают что-либо проверять.
CREATE TABLE resolution_kind (
  resolution           TEXT PRIMARY KEY,
  resolution_authority TEXT NOT NULL REFERENCES resolution_authority(value),
  closes_period        INTEGER NOT NULL
);

CREATE TABLE finding_resolution (
  id                     INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id                 INTEGER NOT NULL REFERENCES run(id),
  finding_id             INTEGER NOT NULL REFERENCES finding(id),
  resolution             TEXT    NOT NULL REFERENCES resolution_kind(resolution),
  resolution_authority   TEXT    NOT NULL REFERENCES resolution_authority(value),
  campaign_id            INTEGER NOT NULL REFERENCES review_campaign(id),
  round_no               INTEGER,
  human_answer_id        INTEGER REFERENCES human_answer(id),
  closes_severity_period INTEGER NOT NULL,
  event_id               INTEGER NOT NULL REFERENCES run_event(id),
  created_at             INTEGER NOT NULL,
  -- run_event.id — единственный порядок изменений finding. Две резолюции
  -- одного finding в одном событии означали бы два взаимоисключающих статуса.
  UNIQUE (finding_id, event_id),
  -- Круг закрытия — из той же кампании; при round_no IS NULL (закрытие
  -- решением человека вне круга) составной FK не проверяется, и это верно.
  FOREIGN KEY (campaign_id, round_no)
      REFERENCES review_round(campaign_id, round_no),
  -- Закрытие меняет вычисляемый статус finding, поэтому прогон обязан
  -- совпадать: строка из чужого прогона иначе молча правит чужой статус.
  FOREIGN KEY (finding_id, run_id)  REFERENCES finding(id, run_id),
  FOREIGN KEY (campaign_id, run_id) REFERENCES review_campaign(id, run_id),
  -- Кто закрыл — однозначно следует из того, чем закрыли.
  -- ELSE обязателен: без него неизвестное значение даёт NULL, а NULL проходит.
  CHECK (resolution_authority = CASE resolution
           WHEN 'verified_fixed'  THEN 'reviewer'
           WHEN 'accepted_reason' THEN 'reviewer'
           WHEN 'policy_closed'   THEN 'policy'
           WHEN 'human_decision'  THEN 'human'
           ELSE '<invalid>' END),
  -- Период закрывают только два исхода из четырёх.
  CHECK (closes_severity_period = CASE resolution
           WHEN 'verified_fixed' THEN 1
           WHEN 'human_decision' THEN 1
           WHEN 'accepted_reason' THEN 0
           WHEN 'policy_closed'  THEN 0
           ELSE -1 END),
  CHECK ((resolution = 'human_decision') = (human_answer_id IS NOT NULL))
);

CREATE INDEX ix_resolution_finding ON finding_resolution (finding_id);

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

CREATE VIEW finding_period AS
WITH opens AS (
  SELECT f.id AS finding_id, f.event_id AS ev FROM finding f
  UNION ALL
  SELECT l.finding_id, l.event_id FROM finding_observation_link l
   WHERE l.link_type = 'reopening'
),
closes AS (
  SELECT r.finding_id, r.event_id AS ev FROM finding_resolution r
   WHERE r.closes_severity_period = 1
),
last_close AS (
  SELECT f.id AS finding_id,
         COALESCE((SELECT MAX(ev) FROM closes c WHERE c.finding_id = f.id), 0) AS ev
    FROM finding f
)
SELECT f.id AS finding_id,
       -- MIN, а не MAX: период начинается с ПЕРВОГО открывающего события
       -- после последнего закрывающего.
       (SELECT MIN(o.ev) FROM opens o
         WHERE o.finding_id = f.id AND o.ev > lc.ev) AS period_start_event_id
  FROM finding f JOIN last_close lc ON lc.finding_id = f.id;

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

-- Последний override внутри текущего периода, если он есть.
CREATE VIEW finding_last_override AS
SELECT v.finding_id, v.new_severity, v.event_id
  FROM severity_override v
  JOIN finding_period p ON p.finding_id = v.finding_id
 WHERE p.period_start_event_id IS NOT NULL
   AND v.event_id >= p.period_start_event_id
   AND v.event_id = (SELECT MAX(v2.event_id) FROM severity_override v2
                      WHERE v2.finding_id = v.finding_id
                        AND v2.event_id >= p.period_start_event_id);

CREATE VIEW finding_severity AS
SELECT f.id AS finding_id,
       p.period_start_event_id,
       -- Только порог эскалации читает это значение.
       -- Отсечка: наблюдения ДО последнего override в счёт не идут,
       -- иначе понижение человеком не срабатывает.
       (SELECT s.severity FROM (
            SELECT o.severity_effective AS sv
              FROM finding_observation_link l
              JOIN review_observation o ON o.id = l.observation_id
             WHERE l.finding_id = f.id
               AND p.period_start_event_id IS NOT NULL
               AND l.event_id >= p.period_start_event_id
               AND l.event_id >  COALESCE(ov.event_id, -1)
               AND l.link_type IN ('first_seen','recurrence','reopening')
            UNION ALL
            SELECT ov.new_severity WHERE ov.new_severity IS NOT NULL
          ) JOIN severity_scale s ON s.severity = sv
          ORDER BY s.rank DESC LIMIT 1)             AS escalation_severity,
       -- Только CLI, как диагноз занижения. Override не учитывает намеренно.
       (SELECT s.severity FROM finding_observation_link l
          JOIN review_observation o ON o.id = l.observation_id
          JOIN severity_scale     s ON s.severity = o.severity_effective
         WHERE l.finding_id = f.id
         ORDER BY s.rank DESC LIMIT 1)              AS historical_max
  FROM finding f
  JOIN finding_period p        ON p.finding_id = f.id
  LEFT JOIN finding_last_override ov ON ov.finding_id = f.id;

-- Единственная мутация severity, не рождённая из наблюдения.
CREATE TABLE severity_override (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  finding_id    INTEGER NOT NULL REFERENCES finding(id),
  old_severity  TEXT    NOT NULL REFERENCES severity_scale(severity),
  new_severity  TEXT    NOT NULL REFERENCES severity_scale(severity),
  reason        TEXT    NOT NULL,
  human_answer_id INTEGER REFERENCES human_answer(id),
  event_id      INTEGER NOT NULL REFERENCES run_event(id),
  created_at    INTEGER NOT NULL,
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

-- Строка = факт допуска пары провайдер+модель к этой версии предмета в этой
-- кампании (Q50 A). Журнал попыток живёт в step_attempt и здесь не дублируется.
CREATE TABLE reviewer_exposure (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id           INTEGER NOT NULL REFERENCES run(id),
  subject_id       INTEGER NOT NULL REFERENCES review_subject(id),
  revision         TEXT    NOT NULL,
  provider         TEXT    NOT NULL,
  model            TEXT    NOT NULL,
  campaign_id      INTEGER NOT NULL REFERENCES review_campaign(id),
  first_attempt_id INTEGER NOT NULL REFERENCES step_attempt(id),
  profile_id       TEXT    NOT NULL,               -- профиль первой попытки
  created_at       INTEGER NOT NULL,
  UNIQUE (subject_id, revision, provider, model, campaign_id),
  -- Кампания смотрит на тот же предмет, а первая попытка принадлежит этой
  -- кампании и этому прогону. Иначе строка допуска говорит про одно, а
  -- ссылается на другое.
  FOREIGN KEY (campaign_id, subject_id)       REFERENCES review_campaign(id, subject_id),
  -- Ревизия — та, которую попытка реально получила, а не та, с которой
  -- кампания открылась: в fix_check агент видит output_sha правки автора.
  FOREIGN KEY (first_attempt_id, revision)    REFERENCES step_attempt(id, subject_revision),
  FOREIGN KEY (first_attempt_id, campaign_id) REFERENCES step_attempt(id, campaign_id),
  FOREIGN KEY (first_attempt_id, run_id)      REFERENCES step_attempt(id, run_id),
  -- И главное: пара provider+model — та, в которую резолвится профиль именно
  -- этой попытки. Без этих двух ключей допуск можно записать на любую пару.
  FOREIGN KEY (first_attempt_id, profile_id)  REFERENCES step_attempt(id, profile_id),
  FOREIGN KEY (run_id, profile_id, provider, model)
      REFERENCES run_profile_resolution(run_id, profile_id, provider, model)
);

CREATE INDEX ix_exposure_lookup ON reviewer_exposure (subject_id, revision, provider, model);

CREATE INDEX ix_exposure_subject ON reviewer_exposure (subject_id, provider, model);

-- Свежесть держится на этих строках, поэтому они того же класса, что
-- наблюдения и решения: правка или удаление молча возвращает уже видевшую
-- модель в пул свежих.
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

CREATE TABLE run_profile_resolution (
  run_id     INTEGER NOT NULL REFERENCES run(id),
  profile_id TEXT    NOT NULL,
  provider   TEXT    NOT NULL,
  model      TEXT    NOT NULL,
  resolved_at INTEGER NOT NULL,
  PRIMARY KEY (run_id, profile_id),
  -- Под FK из reviewer_exposure: пара берётся только отсюда.
  UNIQUE (run_id, profile_id, provider, model)
);

-- Резолвинг однократен по определению, а теперь он ещё и единственный
-- источник пары provider+model для roster: правка строки молча меняла бы
-- исполнителя линии без нового поколения и без ответа человека.
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

CREATE TABLE task (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id           INTEGER NOT NULL REFERENCES run(id),
  semantic_task_id TEXT    NOT NULL,          -- ID из артефакта разбивки
  import_id        INTEGER NOT NULL REFERENCES task_graph_import(id),
  title            TEXT    NOT NULL,
  body             TEXT    NOT NULL,
  state            TEXT    NOT NULL REFERENCES task_state(state),
                                             -- pending|ready|running|done|invalidated|cancelled
  carry_over_of    INTEGER REFERENCES task(id),
  created_at       INTEGER NOT NULL,
  closed_at        INTEGER,
  UNIQUE (import_id, semantic_task_id),
  UNIQUE (id, run_id)                       -- под scope-FK blocker
);

-- Уникальность смыслового ID — среди АКТИВНЫХ версий задачи.
CREATE UNIQUE INDEX ux_task_active_semantic
  ON task (run_id, semantic_task_id) WHERE state <> 'invalidated';

CREATE TABLE task_dependency (
  parent_task_id INTEGER NOT NULL REFERENCES task(id),
  child_task_id  INTEGER NOT NULL REFERENCES task(id),
  PRIMARY KEY (parent_task_id, child_task_id),
  CHECK (parent_task_id <> child_task_id)
);

CREATE TABLE task_graph_import (
  id                      INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id                  INTEGER NOT NULL REFERENCES run(id),
  source_artifact_revision INTEGER NOT NULL REFERENCES artifact_revision(id),
  imported_at             INTEGER NOT NULL,
  event_id                INTEGER NOT NULL REFERENCES run_event(id)
);

CREATE TABLE blocker (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id       INTEGER NOT NULL REFERENCES run(id),
  kind         TEXT    NOT NULL REFERENCES blocker_kind(kind),
  branch_id    INTEGER REFERENCES branch(id),
  task_id      INTEGER REFERENCES task(id),
  stage_id     INTEGER REFERENCES stage_execution(id),
  question_id  INTEGER REFERENCES human_question(id),
  detail       TEXT,
  created_at   INTEGER NOT NULL,
  created_event_id INTEGER NOT NULL REFERENCES run_event(id),
  cleared_at   INTEGER,
  cleared_event_id INTEGER REFERENCES run_event(id),
  -- `run_id` — несущий scope вычисляемого run_state и CLI. Любой заданный
  -- target и оба audit-события обязаны принадлежать тому же прогону.
  FOREIGN KEY (branch_id, run_id)        REFERENCES branch(id, run_id),
  FOREIGN KEY (task_id, run_id)          REFERENCES task(id, run_id),
  FOREIGN KEY (stage_id, run_id)         REFERENCES stage_execution(id, run_id),
  FOREIGN KEY (question_id, run_id)      REFERENCES human_question(id, run_id),
  FOREIGN KEY (created_event_id, run_id) REFERENCES run_event(id, run_id),
  FOREIGN KEY (cleared_event_id, run_id) REFERENCES run_event(id, run_id),
  CHECK (kind <> 'human_question' OR question_id IS NOT NULL),
  CHECK ((cleared_at IS NULL) = (cleared_event_id IS NULL))
);

CREATE INDEX ix_blocker_open ON blocker (run_id, kind) WHERE cleared_at IS NULL;

CREATE TABLE run_event (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id     INTEGER NOT NULL REFERENCES run(id),
  kind       TEXT    NOT NULL,
  branch_id  INTEGER REFERENCES branch(id),
  stage_id   INTEGER REFERENCES stage_execution(id),
  payload    TEXT    NOT NULL,            -- JSON, схема на kind
  created_at INTEGER NOT NULL,
  UNIQUE (id, run_id)                  -- под scope-FK blocker
);

CREATE INDEX ix_event_run ON run_event (run_id, id);

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

CREATE TABLE human_question (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  public_id     TEXT    NOT NULL UNIQUE,            -- Q-3
  run_id        INTEGER NOT NULL REFERENCES run(id),
  branch_id     INTEGER REFERENCES branch(id),
  stage_id      INTEGER REFERENCES stage_execution(id),
  campaign_id   INTEGER REFERENCES review_campaign(id),
  round_id      INTEGER REFERENCES review_round(id),
  finding_id    INTEGER REFERENCES finding(id),
  reason        TEXT    NOT NULL REFERENCES question_reason(reason),
                                       -- cap_exhausted_same | cap_exhausted_new
                                       -- | dispute | contract_error | hang
                                       -- | baseline_red | approval_gate | open_question
                                       -- | reopen_human_closed | reconcile_failed
                                       -- | lane_failure | verification_policy
  question_text TEXT    NOT NULL,
  options_json  TEXT,                  -- NULL = вопрос без вариантов, это законно
  snapshot_json TEXT,                  -- воспроизводимое объяснение решения; состав
                                       -- зависит от reason: dispute — severity,
                                       -- порог и версия политики; cap_exhausted_same
                                       -- — счётчики и история кругов по открытым
                                       -- findings; cap_exhausted_new — счётчики и
                                       -- first_round_id новой личности;
                                       -- reconcile_failed — ledger и validation
                                       -- issues; reopen_human_closed — pending
                                       -- requests со snapshot severity
  asked_at      INTEGER NOT NULL,
  answered_at   INTEGER,
  reask_count   INTEGER NOT NULL DEFAULT 0,
  UNIQUE (id, round_id),
  UNIQUE (id, reason),
  UNIQUE (id, run_id),                         -- под scope-FK blocker
  FOREIGN KEY (campaign_id, round_id) REFERENCES review_round(campaign_id, id),
  -- Все координаты вопроса — из его прогона. Иначе вопрос показывается в
  -- одном прогоне, а ответ применяется к кампании другого.
  FOREIGN KEY (campaign_id, run_id)   REFERENCES review_campaign(id, run_id),
  FOREIGN KEY (stage_id, run_id)      REFERENCES stage_execution(id, run_id),
  FOREIGN KEY (branch_id, run_id)     REFERENCES branch(id, run_id),
  FOREIGN KEY (finding_id, run_id)    REFERENCES finding(id, run_id),
  -- Две причины, где человек классифицирует наблюдения, обязаны нести круг:
  -- без него ответ нельзя отличить от ответа на прошлый круг той же кампании.
  CHECK (reason NOT IN ('reconcile_failed', 'reopen_human_closed')
         OR (campaign_id IS NOT NULL AND round_id IS NOT NULL))
);

-- Scope, причина и presentation/snapshot — то, что увидел человек. Меняются
-- только lifecycle-поля answered_at/reask_count; переписать сам вопрос нельзя.
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

-- Что именно человек видел, когда отвечал. Нормализованное членство вместо
-- разбора snapshot_json: только по этим строкам его решение и применимо.
CREATE TABLE human_question_observation (
  question_id    INTEGER NOT NULL REFERENCES human_question(id),
  observation_id INTEGER NOT NULL REFERENCES review_observation(id),
  campaign_id    INTEGER NOT NULL REFERENCES review_campaign(id),
  round_id       INTEGER NOT NULL REFERENCES review_round(id),
  run_id         INTEGER NOT NULL REFERENCES run(id),
  reason         TEXT    NOT NULL REFERENCES question_reason(reason),
  finding_id     INTEGER REFERENCES finding(id),   -- target у reopen-запроса
  PRIMARY KEY (question_id, observation_id),
  -- Вопрос и наблюдение — из одной кампании и одного круга. Строка «человек
  -- это видел» обязана быть истинной сама по себе, а не после того, как её
  -- отбракует создание связи.
  -- Круг определяет кампанию, поэтому отдельные ключи «вопрос той же
  -- кампании» и «наблюдение той же кампании» избыточны: достаточно привязать
  -- к кругу обе стороны и сам круг — к кампании строки.
  FOREIGN KEY (campaign_id, round_id)       REFERENCES review_round(campaign_id, id),
  FOREIGN KEY (question_id, round_id)       REFERENCES human_question(id, round_id),
  FOREIGN KEY (question_id, reason)         REFERENCES human_question(id, reason),
  FOREIGN KEY (observation_id, round_id)    REFERENCES review_observation(id, round_id),
  FOREIGN KEY (campaign_id, run_id)         REFERENCES review_campaign(id, run_id),
  FOREIGN KEY (finding_id, run_id)          REFERENCES finding(id, run_id),
  -- Членство существует только у двух причин, и форма target у них разная:
  -- reopen переоткрывает НАЗВАННУЮ личность, а запасной reconciler
  -- классифицирует наблюдение сам и цели не имеет.
  CHECK (reason IN ('reconcile_failed', 'reopen_human_closed')),
  CHECK ((reason = 'reopen_human_closed') = (finding_id IS NOT NULL))
);

CREATE TRIGGER trg_question_observation_immutable
BEFORE UPDATE ON human_question_observation
BEGIN
  SELECT RAISE(ABORT, 'human_question_observation is immutable');
END;

-- Набор замораживается вместе с вопросом: дописать его после ответа значит
-- задним числом объявить, что человек видел то, чего не видел.
CREATE TRIGGER trg_question_observation_frozen_after_answer
BEFORE INSERT ON human_question_observation
BEGIN
  SELECT RAISE(ABORT, 'question membership is frozen once the answer exists')
  WHERE EXISTS (SELECT 1 FROM human_answer a WHERE a.question_id = NEW.question_id);
END;

CREATE TRIGGER trg_question_observation_no_delete
BEFORE DELETE ON human_question_observation
BEGIN
  SELECT RAISE(ABORT, 'human_question_observation rows are never deleted');
END;

CREATE INDEX ix_question_open ON human_question (run_id) WHERE answered_at IS NULL;

CREATE TABLE human_answer (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  question_id   INTEGER NOT NULL REFERENCES human_question(id),
  raw_text      TEXT    NOT NULL,
  chosen_option TEXT,                  -- заполнено = закрытие без участия модели
  interpreted_json TEXT,
  transport     TEXT    NOT NULL REFERENCES transport_kind(transport),
  update_id     INTEGER,
  received_at   INTEGER NOT NULL,
  UNIQUE (question_id)                 -- инвариант 20: один принятый ответ
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

-- Транспортно-нейтрален: домен пишет сюда, не зная про Telegram.
-- target_ref интерпретирует транспорт (chat_id для Telegram, NULL для CLI).
CREATE TABLE notification_outbox (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id         INTEGER NOT NULL REFERENCES run(id),
  question_id    INTEGER REFERENCES human_question(id),
  transport      TEXT    NOT NULL REFERENCES transport_kind(transport),
                                             -- telegram | cli
  target_ref     TEXT,
  body           TEXT    NOT NULL,
  reply_markup   TEXT,
  created_at     INTEGER NOT NULL,
  sent_at        INTEGER,
  transport_message_id TEXT,
  attempts       INTEGER NOT NULL DEFAULT 0,
  last_error     TEXT
);

CREATE INDEX ix_outbox_pending ON notification_outbox (id) WHERE sent_at IS NULL;

CREATE TABLE telegram_inbox (
  transport   TEXT    NOT NULL REFERENCES transport_kind(transport),
  update_id   INTEGER NOT NULL,
  payload     TEXT    NOT NULL,
  received_at INTEGER NOT NULL,
  handled_at  INTEGER,
  PRIMARY KEY (transport, update_id)      -- инвариант 20
);

CREATE TABLE telegram_cursor (
  transport   TEXT PRIMARY KEY REFERENCES transport_kind(transport),
  next_offset INTEGER NOT NULL
);

CREATE TABLE artifact_revision (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id          INTEGER NOT NULL REFERENCES run(id),
  stage_id        INTEGER REFERENCES stage_execution(id),
  kind            TEXT    NOT NULL REFERENCES artifact_kind(kind),
                                             -- design | breakdown | task_plan
                                             -- | cutoff | verification | notes
  logical_path    TEXT    NOT NULL,
  revision_no     INTEGER NOT NULL,
  content_digest  TEXT    NOT NULL,
  code_sha        TEXT    NOT NULL,          -- всегда явно: артефакт может лежать
                                             -- в другом репозитории
  repo_commit     TEXT,
  produced_by_attempt_id INTEGER REFERENCES step_attempt(id),
  produced_by     TEXT    NOT NULL REFERENCES artifact_producer(producer),
                                             -- agent | human
  manifest_json   TEXT    NOT NULL,
  created_at      INTEGER NOT NULL,
  UNIQUE (run_id, logical_path, revision_no)
);

CREATE TABLE artifact_approval (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  revision_id  INTEGER NOT NULL REFERENCES artifact_revision(id),
  approved_by  TEXT    NOT NULL,             -- human | campaign:<id>
  question_id  INTEGER REFERENCES human_question(id),
  created_at   INTEGER NOT NULL,
  UNIQUE (revision_id, approved_by)
);

CREATE TABLE verification_run (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id         INTEGER NOT NULL REFERENCES run(id),
  stage_id       INTEGER REFERENCES stage_execution(id),
  purpose        TEXT    NOT NULL REFERENCES verification_purpose(purpose),
                                             -- baseline | after_fix | final
  code_sha       TEXT    NOT NULL,
  plan_json      TEXT    NOT NULL,           -- VerificationPlan как прислал агент
  plan_source    TEXT    NOT NULL REFERENCES verification_plan_source(source),
                                             -- recipe | agent
  policy_allowed INTEGER NOT NULL CHECK (policy_allowed IN (0, 1)),
  policy_rejection_reason TEXT,
  result_json    TEXT,                       -- команда → код возврата → вывод
  status         TEXT REFERENCES verification_status(status),
                                             -- green | red | error
  failure_signature TEXT,                    -- для сравнения «та же причина или другая»
  started_at     INTEGER NOT NULL,
  finished_at    INTEGER,
  CHECK ((policy_allowed = 1) = (policy_rejection_reason IS NULL))
);

CREATE INDEX ix_stage_by_branch     ON stage_execution (branch_id, state);

CREATE INDEX ix_attempt_by_stage    ON step_attempt (stage_id, role);

CREATE INDEX ix_attempt_running     ON step_attempt (run_id) WHERE outcome IS NULL;

CREATE INDEX ix_attempt_by_round    ON step_attempt (round_id, lane_assignment_id, role);

CREATE INDEX ix_campaign_by_stage   ON review_campaign (stage_id, state);

CREATE INDEX ix_campaign_open       ON review_campaign (run_id) WHERE closed_at IS NULL;

CREATE INDEX ix_finding_by_subject  ON finding (subject_id);

CREATE INDEX ix_task_ready          ON task (run_id, state);

CREATE INDEX ix_dep_child           ON task_dependency (child_task_id);

INSERT INTO attempt_outcome(outcome) VALUES
  ('succeeded'),('interrupted'),('hung'),('transient'),('contract_error'),('failed');

INSERT INTO attempt_role(role) VALUES
  ('author'),('reviewer'),('planner'),('reconciler');

INSERT INTO heartbeat_source(source) VALUES ('stdout'),('stderr'),('fs');

INSERT INTO branch_kind(kind) VALUES ('pipeline'),('task');

INSERT INTO round_result(result) VALUES
  ('clean'),('needs_revision'),('escalated');

INSERT INTO review_round_kind(kind) VALUES ('discovery'),('fix_check');

INSERT INTO finding_round_entry_kind(entry_kind) VALUES ('issued'),('post_check');

INSERT INTO branch_state(state) VALUES
  ('ready'),('running'),('retry_wait'),('blocked'),('done'),('failed'),('cancelled');

INSERT INTO run_terminal_state(state) VALUES
  ('succeeded'),('failed'),('cancelled');

INSERT INTO campaign_state(state) VALUES
  ('discovery'),('reconciliation'),('fix_cycle'),('closed_clean'),
  ('closed_escalated'),('closed_cancelled');

-- Таблица переходов T1.4 целиком. Начальная вставка `[*] → discovery`
-- переходом не является и строки не имеет; у терминальных состояний
-- исходящих строк нет, поэтому они терминальны по построению.
INSERT INTO campaign_transition(from_state, to_state) VALUES
  ('discovery',      'reconciliation'),    -- discovery_completed
  ('discovery',      'closed_cancelled'),
  ('reconciliation', 'fix_cycle'),         -- reconciliation_has_findings
  ('reconciliation', 'closed_clean'),      -- reconciliation_clean
  ('reconciliation', 'closed_cancelled'),
  ('fix_cycle',      'fix_cycle'),         -- check_needs_revision,
                                           -- human_gate_opened,
                                           -- human_extra_revision
  ('fix_cycle',      'closed_clean'),      -- check_clean
  ('fix_cycle',      'closed_escalated'),  -- human_finalized
  ('fix_cycle',      'closed_cancelled');

INSERT INTO task_state(state) VALUES
  ('pending'),('ready'),('running'),('done'),('invalidated'),('cancelled');

INSERT INTO subject_kind(kind) VALUES ('code'),('artifact'),('task'),('stage');

INSERT INTO link_type(link_type) VALUES
  ('first_seen'),('recurrence'),('reaffirmation'),('reopening');

INSERT INTO disposition(value) VALUES ('fixed'),('rejected'),('wont_fix');

INSERT INTO reviewer_decision(value) VALUES
  ('verified_fixed'),('still_present'),('accepted_reason'),('insists');

INSERT INTO resolution_authority(value) VALUES ('reviewer'),('human'),('policy');

INSERT INTO resolution_kind VALUES
  ('verified_fixed',  'reviewer', 1),
  ('accepted_reason', 'reviewer', 0),
  ('policy_closed',   'policy',   0),
  ('human_decision',  'human',    1);

INSERT INTO blocker_kind(kind) VALUES
  ('human_question'),('awaiting_continue'),('dependency'),('drift'),('invalid_graph');

INSERT INTO title_authority(authority) VALUES ('runtime'),('human');

INSERT INTO question_reason(reason) VALUES
  ('cap_exhausted_same'),('cap_exhausted_new'),('dispute'),('contract_error'),
  ('hang'),('baseline_red'),('approval_gate'),('open_question'),
  ('reopen_human_closed'),('reconcile_failed'),('lane_failure'),
  ('verification_policy');

INSERT INTO transport_kind(transport) VALUES ('telegram'),('cli');

INSERT INTO artifact_kind(kind) VALUES
  ('design'),('breakdown'),('task_plan'),('cutoff'),('verification'),('notes');

INSERT INTO artifact_producer(producer) VALUES ('agent'),('human');

INSERT INTO verification_purpose(purpose) VALUES ('baseline'),('after_fix'),('final');

INSERT INTO verification_plan_source(source) VALUES ('recipe'),('agent');

INSERT INTO verification_status(status) VALUES ('green'),('red'),('error');
