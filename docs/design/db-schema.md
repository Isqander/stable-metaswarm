# Схема состояния: SQLite

Дата: 2026-08-02. Статус: первый проход, не проверено запуском.

Реализация модели данных из `../metaResearches/decision.md` §5–§6 в конкретном
DDL. Документ отвечает на вопрос, на который решение не отвечает: **что именно
держит база, а что обязан держать код** — и ложатся ли инварианты §13 в SQLite
без натяжек.

Порядок изложения — от review-домена, потому что там больше всего инвариантов;
но DDL приведён связным, иначе внешние ключи повисают в воздухе.

| Если нужно | Смотреть |
|---|---|
| Что решено и почему | `../metaResearches/decision.md` |
| Компоненты, процессы, последовательности | `architecture.md` |
| Что агент присылает и в каком виде | `agent-contracts.md` |
| Порядок работ | `task-plans.md` |

В конце — §14: список мест, где проектирование **уточнило или дополнило**
решение. Читать обязательно: там четыре вещи, которых в `decision.md` нет.

---

## 1. Соглашения

### 1.1. Режим базы

```sql
PRAGMA journal_mode = WAL;      -- читатели не блокируют единственного писателя
PRAGMA foreign_keys = ON;       -- по умолчанию OFF, включается на каждом соединении
PRAGMA synchronous = FULL;      -- см. ниже
PRAGMA busy_timeout = 5000;
PRAGMA trusted_schema = OFF;
```

`synchronous = FULL`, а не `NORMAL`, хотя WAL с `NORMAL` переживает падение
процесса. Причина одна: ответ человека записывается durable **до** снятия
блокировки (инвариант 16), и терять его при потере питания нельзя — переспросить
некого, а напоминаний в системе нет. Цена нулевая: нагрузка — десятки транзакций
в минуту, не десятки тысяч.

`foreign_keys = ON` выставляется при открытии **каждого** соединения. Это не
свойство файла базы, и забытый PRAGMA превращает половину инвариантов ниже в
декорацию.

### 1.2. Идентификаторы: внутренние и публичные

Два разных вида, и путать их нельзя.

| Вид | Форма | Кто видит |
|---|---|---|
| Внутренний | `INTEGER PRIMARY KEY AUTOINCREMENT` | Только код и FK |
| Публичный | Короткая строка: `F-17`, `O-4-12`, `Q-3` | Промпт агента, CLI, Telegram |

Внутренний нужен как монотонный порядок и дешёвый FK. Публичный — потому что
**модель обязана точно переписывать ID в своём ответе**: контракт findings стоит
на том, что каждое открытое замечание закрыто по ID. ULID из 26 символов агент
путает и переставляет символы, и каждая такая ошибка — `contract_error` и
потраченная попытка. `F-17` не путается.

`AUTOINCREMENT`, а не голый rowid: без него SQLite переиспользует освободившиеся
rowid, а нам нужна монотонность как порядок событий.

### 1.3. Время

```
created_at, closed_at, …  INTEGER   -- Unix epoch, миллисекунды, UTC
*_mono_ns                 INTEGER   -- CLOCK_MONOTONIC, наносекунды
```

Wall-clock — для аудита и показа. Живость, таймауты и retry-задержки — по
монотонным (`decision.md` §5).

**Монотонные значения действительны только внутри одного процесса сервиса.**
После рестарта база отсчёта другая, и сравнение даёт мусор. Поэтому каждая
монотонная величина хранится вместе с `service_epoch_id`, и сравнивать их можно
только в пределах одной эпохи. Практически это безопасно, потому что рестарт
сервиса всё равно переводит незавершённые попытки в `interrupted` — но правило
записано, чтобы никто не сравнил монотонные метки через границу перезапуска.

### 1.4. Enum'ы — справочные таблицы, а не CHECK-списки

Каждое перечисление живёт отдельной таблицей с FK на неё:

```sql
CREATE TABLE severity_scale (
  severity TEXT PRIMARY KEY,
  rank     INTEGER NOT NULL UNIQUE
);
INSERT INTO severity_scale(severity, rank) VALUES
  ('low', 10), ('medium', 20), ('high', 30), ('critical', 40);
```

Три причины предпочесть это `CHECK (severity IN (...))`:

1. **«Значение вне enum — contract error» становится нарушением FK**, то есть
   отказом на уровне базы, а не соглашением. `decision.md` §6.3 требует
   отсутствия молчаливых приведений — FK это обеспечивает буквально.
2. **Порядок сравнения хранится рядом со значением.** `low < medium < high <
   critical` — не алфавитный порядок, и любой `MAX(severity)` по тексту дал бы
   `medium` вместо `high`. С `rank` сравнение однозначно.
3. Шаг между рангами — 10, чтобы вставка промежуточного уровня не требовала
   переписывать существующие строки.

Так же оформлены: `attempt_outcome`, `round_result`, `branch_state`,
`campaign_state`, `subject_kind`, `link_type`, `disposition`,
`reviewer_decision`, `resolution_authority`, `blocker_kind`. Значения — из
`decision.md`, полный список в §12.

### 1.5. Что означает «immutable» на практике

SQLite не умеет запрещать UPDATE декларативно. Неизменяемость обеспечивается
двумя средствами:

- **триггером** `BEFORE UPDATE ... RAISE(ABORT)` на таблицах, где неизменяемость
  несущая (`review_observation`, `finding_observation_link`,
  `finding_resolution`, `run_event`);
- **отсутствием кода**, который бы такой UPDATE выполнял: слой доступа не
  предоставляет метода обновления для этих таблиц.

Триггер здесь не паранойя. Слепой вывод ревьюера — это доказательство при
разборе «ревьюер объявил новым то, что было открыто» (`decision.md` §6.3), и
доказательство, которое можно молча поправить, доказательством не является.

---

## 2. Карта таблиц

```
служебное        schema_migration, service_epoch, instance_lock
прогон           run, branch, stage_execution, step_attempt, attempt_liveness,
                 logical_session, run_event, blocker
граф             task, task_dependency, task_graph_import
review-домен     review_subject, review_campaign, review_lane, review_round,
                 author_revision, review_observation, finding,
                 finding_observation_link, finding_round, finding_resolution,
                 severity_override, reviewer_exposure
человек          human_question, human_answer, telegram_outbox, telegram_inbox
артефакты        artifact_revision, artifact_approval, verification_run
справочники      severity_scale, attempt_outcome, round_result, branch_state,
                 campaign_state, subject_kind, link_type, disposition,
                 reviewer_decision, resolution_authority, blocker_kind
```

Никаких хранимых агрегатов: `run.state`, `escalation_severity`,
`author_revision_count`, `review_check_count` — представления, не колонки.
Обоснование в §6 и §7.

---

## 3. Служебное

```sql
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
```

`instance_lock` — **не таблица**. Замок берётся на файле в каталоге состояния
(`flock`) плюс lease-запись с heartbeat; держать его строкой в той же базе,
доступ к которой он и защищает, — конструкция, которая ломается ровно в тот
момент, когда нужна. Детали — `architecture.md` §4.

---

## 4. Прогон, стадии, попытки

```sql
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
```

Пять полей конфигурации с хешами — это входы drift check (`decision.md` §4).
Семь осей дрейфа: пять хешей отсюда, версии вендорских CLI из манифеста первой
попытки и ревизии артефактов из `artifact_revision`.

```sql
CREATE TABLE branch (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id     INTEGER NOT NULL REFERENCES run(id),
  public_id  TEXT    NOT NULL,                        -- B-main, B-task-7
  kind       TEXT    NOT NULL,                        -- pipeline | task
  task_id    INTEGER REFERENCES task(id),
  state      TEXT    NOT NULL REFERENCES branch_state(state),
  created_at INTEGER NOT NULL,
  UNIQUE (run_id, public_id),
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
  UNIQUE (branch_id, stage_key, ordinal)
);
```

`max_author_revisions` лежит **на стадии**, а не только в конфиге флоу, потому
что человек в ответе на исчерпание кругов может выбрать «разрешить ещё одну
правку» (`decision.md` §8) — и это увеличение относится к одной конкретной
стадии, а не к флоу. Значение при создании берётся из конфига; человеческое
решение делает `+1` здесь и пишет событие. Без этой колонки решение человека
пришлось бы хранить где-то сбоку и учитывать при каждой проверке капа — то есть
завести второй источник правды о том же числе.

```sql
CREATE TABLE step_attempt (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  public_id          TEXT    NOT NULL UNIQUE,
  run_id             INTEGER NOT NULL REFERENCES run(id),
  stage_id           INTEGER NOT NULL REFERENCES stage_execution(id),
  role               TEXT    NOT NULL,                -- author | reviewer | planner | reconciler
  lane_id            INTEGER REFERENCES review_lane(id),
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
  transcript_digest  TEXT
);

-- Инвариант 19: не более одной активной попытки на шаг или линию.
CREATE UNIQUE INDEX ux_attempt_active
  ON step_attempt (stage_id, role, COALESCE(lane_id, -1))
  WHERE outcome IS NULL;
```

Partial unique index — тот случай, где SQLite делает работу за нас: пока
`outcome IS NULL`, вторая попытка той же роли в той же стадии физически не
вставляется. Инвариант перестаёт быть проверкой в сервисе.

`actual_model` заполняется при завершении, потому что до запуска он неизвестен:
у `claude-z` запрос `opus` возвращает `glm-5.2`. Правило свежести ревьюера
считает по фактической паре — см. `reviewer_exposure` в §5.7.

```sql
-- Изменяемая часть попытки вынесена отдельно: heartbeat пишется каждые
-- несколько секунд, и он не должен трогать запись результата.
CREATE TABLE attempt_liveness (
  attempt_id             INTEGER PRIMARY KEY REFERENCES step_attempt(id) ON DELETE CASCADE,
  service_epoch_id       INTEGER NOT NULL REFERENCES service_epoch(id),
  pid                    INTEGER NOT NULL,
  pgid                   INTEGER NOT NULL,
  proc_start_ticks       INTEGER NOT NULL,   -- поле 22 из /proc/<pid>/stat
  started_mono_ns        INTEGER NOT NULL,
  last_heartbeat_mono_ns INTEGER NOT NULL,
  last_heartbeat_at      INTEGER NOT NULL,
  heartbeat_source       TEXT    NOT NULL    -- stdout | fs
);
```

`proc_start_ticks` — не избыточность. PID переиспользуется ОС, и после падения
сервиса проверка «жив ли ещё процесс 4711» без времени старта отвечает «да» про
чужой процесс. Recovery обязан гасить группу перед созданием новой попытки
(`decision.md` §4), и убить не тот pgid — это не теоретическая ошибка.

```sql
CREATE TABLE logical_session (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id            INTEGER NOT NULL REFERENCES run(id),
  profile_id        TEXT    NOT NULL,
  provider          TEXT    NOT NULL,
  model             TEXT    NOT NULL,
  vendor_session_id TEXT,
  purpose           TEXT    NOT NULL,       -- lane:12 | author:stage:7
  created_at        INTEGER NOT NULL,
  closed_at         INTEGER
);
```

---

## 5. Review-домен

Ядро документа. Четыре отношения из `decision.md` §6.3 плюс то, чего там нет
явно, но без чего они не работают: кампания, линия, круг, правка автора,
закрытие, экспозиция ревьюера.

### 5.1. Предмет

```sql
CREATE TABLE review_subject (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id            INTEGER NOT NULL REFERENCES run(id),
  kind              TEXT    NOT NULL REFERENCES subject_kind(kind),
  target_ref        TEXT    NOT NULL,
  revision          TEXT    NOT NULL,
  parent_subject_id INTEGER REFERENCES review_subject(id),
  created_at        INTEGER NOT NULL,
  CHECK (parent_subject_id IS NULL OR parent_subject_id <> id)
);

CREATE INDEX ix_subject_parent ON review_subject (parent_subject_id);
```

Вложенность задаётся флоу при создании кампании. Отбор «какие закрытые findings
видит reconciliation текущей кампании» — рекурсия **вниз** по дереву: предмет
финальной кампании является родителем предметов таск-кампаний, значит финальная
видит их findings, а таск-кампания findings финальной — нет.

```sql
-- Все предметы, входящие в предмет :subject_id, включая его самого.
WITH RECURSIVE scope(id) AS (
  SELECT :subject_id
  UNION
  SELECT s.id FROM review_subject s JOIN scope ON s.parent_subject_id = scope.id
)
SELECT id FROM scope;
```

Цикл в дереве предметов сделал бы эту рекурсию бесконечной. `UNION` (не `UNION
ALL`) обрывает повтор, но правильная защита — проверка при вставке: предок не
может быть потомком. Проверяется кодом в той же транзакции, что и создание
кампании; предметов на прогон — единицы.

### 5.2. Кампания, линия, круг, правка

```sql
CREATE TABLE review_campaign (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  public_id          TEXT    NOT NULL UNIQUE,       -- C-4
  run_id             INTEGER NOT NULL REFERENCES run(id),
  stage_id           INTEGER NOT NULL REFERENCES stage_execution(id),
  subject_id         INTEGER NOT NULL REFERENCES review_subject(id),
  ordinal            INTEGER NOT NULL,              -- какой кворум по счёту на стадии
  severity_threshold TEXT    NOT NULL REFERENCES severity_scale(severity),
  policy_version     TEXT    NOT NULL,
  state              TEXT    NOT NULL REFERENCES campaign_state(state),
  opened_at          INTEGER NOT NULL,
  closed_at          INTEGER,
  close_reason       TEXT,
  UNIQUE (stage_id, ordinal)
);

CREATE TABLE review_lane (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER NOT NULL REFERENCES review_campaign(id),
  lane_index  INTEGER NOT NULL,                     -- 0,1,… минимальный = владелец
  profile_id  TEXT    NOT NULL,
  provider    TEXT    NOT NULL,
  model       TEXT    NOT NULL,
  session_id  INTEGER REFERENCES logical_session(id),
  UNIQUE (campaign_id, lane_index)
);
```

`severity_threshold` и `policy_version` копируются на кампанию при её создании,
а не читаются из конфига в момент решения. Причина в §6.3 `decision.md`: в
событие эскалации пишется snapshot, и вопрос «почему это ушло человеку»
отвечается без пересчёта. Если порог живёт только в конфиге, а конфиг между
остановкой и `continue` поменялся, снапшот врёт.

```sql
-- Круг = одна проверка. Первичный кворум — круг 1.
-- Последовательность: check1 → rev1 → check2 → rev2 → check3 → rev3 → check4.
CREATE TABLE review_round (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id INTEGER NOT NULL REFERENCES review_campaign(id),
  round_no    INTEGER NOT NULL,
  kind        TEXT    NOT NULL,                     -- discovery | fix_check
  preceding_revision_id INTEGER REFERENCES author_revision(id),
  result      TEXT REFERENCES round_result(result), -- NULL = круг ещё идёт
  opened_at   INTEGER NOT NULL,
  closed_at   INTEGER,
  UNIQUE (campaign_id, round_no),
  CHECK ((kind = 'discovery') = (preceding_revision_id IS NULL)),
  CHECK ((result IS NULL) = (closed_at IS NULL))
);

-- Строка появляется ТОЛЬКО когда правка состоялась: попытка succeeded и дала
-- новую ревизию. Это и есть счётчик.
CREATE TABLE author_revision (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id          INTEGER NOT NULL REFERENCES review_campaign(id),
  revision_no          INTEGER NOT NULL,
  attempt_id           INTEGER NOT NULL REFERENCES step_attempt(id),
  input_sha            TEXT,
  output_sha           TEXT,
  artifact_revision_id INTEGER REFERENCES artifact_revision(id),
  completed_at         INTEGER NOT NULL,
  UNIQUE (campaign_id, revision_no),
  UNIQUE (attempt_id)
);
```

**Счётчики капа — это `COUNT(*)`, а не колонки.**

```sql
CREATE VIEW campaign_counters AS
SELECT c.id AS campaign_id,
       (SELECT COUNT(*) FROM author_revision r WHERE r.campaign_id = c.id)
         AS author_revision_count,
       (SELECT COUNT(*) FROM review_round rr
          WHERE rr.campaign_id = c.id AND rr.result IS NOT NULL)
         AS review_check_count
FROM review_campaign c;
```

Инвариант 5 («оба растут только после `succeeded`») тогда держится **структурой,
а не дисциплиной**: строки `author_revision` не существует, пока правка не
состоялась, и `review_round.result` остаётся NULL, пока проверка не дала
валидного вывода. Прерванная, зависшая и контрактно битая попытка не создаёт ни
того, ни другого — двигать счётчик нечему. Хранимая колонка потребовала бы
UPDATE в правильном месте, и «правильное место» пришлось бы удерживать в шести
ветках кода.

### 5.3. Наблюдение

```sql
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
```

Три вещи здесь неочевидны.

**`severity_effective` — разрешённая денормализация, и это не противоречит
запрету хранить `escalation_severity`.** Разница принципиальная: цепочка
`unchanged_from` состоит из immutable-записей, поэтому её разрешение — функция от
данных, которые уже никогда не изменятся, вычисленная один раз в момент вставки.
Второго пути записи не возникает: пересчёт всегда даст то же значение.
`escalation_severity`, наоборот, зависит от событий, которые ещё произойдут
(закрытие периода, переоткрытие), и хранимая копия разошлась бы при падении между
эффектом и коммитом. Практическая выгода велика: `MAX(rank)` по периоду
становится обычным индексированным запросом вместо рекурсивного CTE с
разрешением цепочек на каждом чтении.

**`CHECK ((severity_suggested IS NULL) <> (unchanged_from_id IS NULL))`** — это
инвариант 8 целиком, на уровне базы. Наблюдение без обоих полей не вставляется.

**Обратная ссылка `unchanged_from` проверяется триггером, а не CHECK:** CHECK
видит только собственную строку и не может сравнить с другой.

```sql
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
END;
```

Триггер закрывает «назад по времени» и «в пределах кампании». Два оставшихся
условия — «того же finding'а» и «того же периода открытости» — на момент вставки
наблюдения **проверить невозможно**: наблюдение существует раньше личности, и
привязка к finding'у появляется только на reconciliation. Поэтому они
проверяются кодом на этапе reconciliation, до записи связей, и нарушение даёт
`contract_error` с отклонением всего вывода ревьюера. Это не ослабление: раньше
момента reconciliation данных для проверки просто нет.

**Где `unchanged_from` вообще возможен.** В фазе `blind_discovery` ревьюер не
видит ledger и не знает ID прежних наблюдений — сослаться ему не на что, и любое
наблюдение слепой фазы обязано нести собственную `severity_suggested`.
`unchanged_from` появляется только там, где ревьюер видит открытые замечания: в
кругах проверки исправления, которые ведёт владелец finding'а. Валидатор ответа
знает фазу и требует разного — см. `agent-contracts.md` §3.

### 5.4. Личность и связь

```sql
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
  title_authority      TEXT    NOT NULL DEFAULT 'runtime',   -- runtime | human
  title_changed_reason TEXT,
  event_id             INTEGER NOT NULL REFERENCES run_event(id),
  created_at           INTEGER NOT NULL,
  UNIQUE (run_id, public_id),
  UNIQUE (first_observation_id),
  CHECK (title_authority = 'runtime' OR title_changed_reason IS NOT NULL)
);
```

Севериты на личности нет — намеренно (`decision.md` §6.3). `first_round_id`
хранится, потому что причина остановки цикла требует различить «автор трижды не
смог починить одно и то же» и «ревьюер нашёл новое на четвёртой проверке», а это
и есть круг первого появления.

`event_id` — ссылка на запись в общем журнале. Она несёт **порядок** и нужна для
вычисления периода открытости, см. §5.6.

```sql
CREATE TABLE finding_observation_link (
  observation_id        INTEGER PRIMARY KEY REFERENCES review_observation(id),
  finding_id            INTEGER NOT NULL REFERENCES finding(id),
  link_type             TEXT    NOT NULL REFERENCES link_type(link_type),
  decided_by_attempt_id INTEGER NOT NULL REFERENCES step_attempt(id),
  reason                TEXT,
  event_id              INTEGER NOT NULL REFERENCES run_event(id),
  created_at            INTEGER NOT NULL,
  CHECK (link_type <> 'reopening' OR reason IS NOT NULL)
);

CREATE INDEX ix_link_finding ON finding_observation_link (finding_id);

CREATE TRIGGER trg_link_immutable
BEFORE UPDATE ON finding_observation_link
BEGIN
  SELECT RAISE(ABORT, 'finding_observation_link is immutable');
END;
```

`observation_id` как первичный ключ даёт «одна связь на наблюдение» без единой
строчки кода — вторая связь физически не вставляется. Половина инварианта 2
получена констрейнтом.

**Вторая половина — «наблюдение не потеряно» — констрейнтом не выражается**, это
утверждение о полноте, а не об отдельной строке. Оно проверяется кодом при
закрытии reconciliation:

```sql
-- Должен вернуть 0 строк, иначе вывод reconciliation отклоняется целиком.
SELECT o.id, o.public_id
  FROM review_observation o
  LEFT JOIN finding_observation_link l ON l.observation_id = o.id
 WHERE o.round_id = :round_id AND l.observation_id IS NULL;
```

Тот же запрос гоняется в recovery audit — на случай падения между записью части
связей и коммитом. Это дешевле любой схемы и надёжнее.

**Четыре типа связи, по одному на каждый исход reconciliation:**

| Исход reconciliation | `link_type` | Что делает с кругом |
|---|---|---|
| `new` | `first_seen` | Входит в текущий круг, создаётся новый ID |
| `existing_open(id)` | `recurrence` | Входит в текущий круг |
| `reaffirmed_closed(id)` | `reaffirmation` | Круга не порождает |
| `reopen_closed(id, reason)` | `reopening` | Переводит закрытое в текущий круг |

`decision.md` §6.3 перечисляет три типа связи при четырёх исходах — это
пропуск, а не решение: без отдельного `reopening` переоткрытие неотличимо от
повтора, и период открытости вычислить нечем. Уточнение зафиксировано в §14.

### 5.5. Круг по замечанию и закрытие

```sql
CREATE TABLE finding_round (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id         INTEGER NOT NULL REFERENCES review_campaign(id),
  finding_id          INTEGER NOT NULL REFERENCES finding(id),
  round_no            INTEGER NOT NULL,
  owner_lane_id       INTEGER NOT NULL REFERENCES review_lane(id),
  disposition         TEXT REFERENCES disposition(value),
  disposition_reason  TEXT,
  author_attempt_id   INTEGER REFERENCES step_attempt(id),
  reviewer_decision   TEXT REFERENCES reviewer_decision(value),
  reviewer_attempt_id INTEGER REFERENCES step_attempt(id),
  decided_at          INTEGER,
  UNIQUE (campaign_id, finding_id, round_no),
  -- Отказ обязан быть обоснован.
  CHECK (disposition NOT IN ('rejected','wont_fix') OR disposition_reason IS NOT NULL),
  -- Инвариант 10: исход ревьюера совместим с disposition автора.
  CHECK (
    reviewer_decision IS NULL
    OR (disposition = 'fixed'
        AND reviewer_decision IN ('verified_fixed','still_present'))
    OR (disposition IN ('rejected','wont_fix')
        AND reviewer_decision IN ('accepted_reason','insists'))
  )
);

CREATE INDEX ix_finding_round_finding ON finding_round (finding_id);
```

Совместимость пары — не проверка в сервисе, а CHECK. Инвариант 21
(`UNIQUE(campaign_id, finding_id, round_no)`) — тоже. Инвариант 11 («решение
выносит владелец этого круга, и оно единственное») держится тем, что
`reviewer_decision` — одна колонка одной строки: второго решения записать некуда.
Код при этом обязан проверить, что `reviewer_attempt_id` принадлежит линии
`owner_lane_id` — это констрейнтом не выражается, потому что требует join.

```sql
CREATE TABLE finding_resolution (
  id                     INTEGER PRIMARY KEY AUTOINCREMENT,
  finding_id             INTEGER NOT NULL REFERENCES finding(id),
  seq                    INTEGER NOT NULL,
  resolution             TEXT    NOT NULL,   -- verified_fixed | accepted_reason
                                             -- | policy_closed | human_decision
  resolution_authority   TEXT    NOT NULL REFERENCES resolution_authority(value),
  campaign_id            INTEGER NOT NULL REFERENCES review_campaign(id),
  round_no               INTEGER,
  human_answer_id        INTEGER REFERENCES human_answer(id),
  closes_severity_period INTEGER NOT NULL,
  event_id               INTEGER NOT NULL REFERENCES run_event(id),
  created_at             INTEGER NOT NULL,
  UNIQUE (finding_id, seq),
  -- Кто закрыл — однозначно следует из того, чем закрыли.
  CHECK (resolution_authority = CASE resolution
           WHEN 'verified_fixed'  THEN 'reviewer'
           WHEN 'accepted_reason' THEN 'reviewer'
           WHEN 'policy_closed'   THEN 'policy'
           WHEN 'human_decision'  THEN 'human' END),
  -- Период закрывают только два исхода из четырёх.
  CHECK (closes_severity_period = CASE resolution
           WHEN 'verified_fixed' THEN 1
           WHEN 'human_decision' THEN 1
           ELSE 0 END),
  CHECK ((resolution = 'human_decision') = (human_answer_id IS NOT NULL))
);

CREATE INDEX ix_resolution_finding ON finding_resolution (finding_id);

CREATE TRIGGER trg_resolution_immutable
BEFORE UPDATE ON finding_resolution
BEGIN
  SELECT RAISE(ABORT, 'finding_resolution is immutable');
END;
```

Второй CHECK — самое ценное место всей схемы. Правило «принятый отказ и
policy-closure период не закрывают, а `verified_fixed` и решение человека
закрывают» (`decision.md` §6.3) записано как констрейнт базы, а не как условие в
коде. Лазейка, ради закрытия которой правило и придумано — ревьюер пишет
`accepted_reason` на критичном finding'е, накопитель обнуляется, позднее мягкое
наблюдение опускает спор ниже порога, — теперь неисполнима: чтобы обнулить
накопитель, нужно вставить строку, которую база отвергнет.

`resolution_authority` тоже выводится из `resolution` — и CHECK это фиксирует.
Значит инвариант 13 («у каждого закрытия записан authority») не может быть
нарушен рассинхронизацией: два поля не разъедутся.

### 5.6. Период открытости и три величины severity

Периодов нет как таблицы — это выводимый интервал (`decision.md` §6.3).
Вычисляется по общему монотонному порядку `run_event.id`, ссылки на который
хранят все три вида граничных записей.

| Граница | Где лежит | Условие |
|---|---|---|
| Открывающая | `finding.event_id` | Создание ID |
| Открывающая | `finding_observation_link.event_id` | `link_type = 'reopening'` |
| Закрывающая | `finding_resolution.event_id` | `closes_severity_period = 1` |

```sql
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
       (SELECT MAX(o.ev) FROM opens o
         WHERE o.finding_id = f.id AND o.ev > lc.ev) AS period_start_event_id
  FROM finding f JOIN last_close lc ON lc.finding_id = f.id;
```

`period_start_event_id IS NULL` означает, что последнее закрывающее событие
позже всех открывающих, то есть **замечание закрыто**. Отдельного флага
«открыт/закрыт» не нужно, и он не может разойтись с реальностью.

```sql
CREATE VIEW finding_severity AS
SELECT f.id AS finding_id,
       p.period_start_event_id,
       -- Только порог эскалации читает это значение.
       (SELECT s.severity FROM finding_observation_link l
          JOIN review_observation o  ON o.id = l.observation_id
          JOIN severity_scale     s  ON s.severity = o.severity_effective
         WHERE l.finding_id = f.id
           AND p.period_start_event_id IS NOT NULL
           AND l.event_id >= p.period_start_event_id
           AND l.link_type IN ('first_seen','recurrence','reopening')
         ORDER BY s.rank DESC LIMIT 1)              AS escalation_severity,
       -- Только CLI, как диагноз занижения.
       (SELECT s.severity FROM finding_observation_link l
          JOIN review_observation o ON o.id = l.observation_id
          JOIN severity_scale     s ON s.severity = o.severity_effective
         WHERE l.finding_id = f.id
         ORDER BY s.rank DESC LIMIT 1)              AS historical_max
  FROM finding f JOIN finding_period p ON p.finding_id = f.id;
```

Четыре следствия, которые стоит проверить глазами по таблице границ из
`decision.md` §6.3:

- `accepted_reason` и `policy_closed` не создают закрывающей записи с флагом →
  `period_start_event_id` не меняется → **накопитель живёт**;
- переоткрытие после них даёт `reopening` с `event_id` больше `period_start`, но
  `MAX(ev) WHERE ev > last_close` возвращает то же начало → **прежний период
  продолжается**;
- переоткрытие после `verified_fixed` даёт `reopening` позже закрывающего →
  начало сдвигается → **новый период, счёт с нуля**;
- `reaffirmation` исключён из накопителя намеренно: подтверждение прежнего
  отказа круга не порождает и severity не двигает.

Монотонность вверх (инвариант 9) получается сама: `MAX` не убывает при
добавлении наблюдений, а понижение возможно только сменой периода, то есть через
закрывающее событие.

```sql
-- Единственная мутация severity, не рождённая из наблюдения.
CREATE TABLE severity_override (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  finding_id    INTEGER NOT NULL REFERENCES finding(id),
  old_severity  TEXT    NOT NULL REFERENCES severity_scale(severity),
  new_severity  TEXT    NOT NULL REFERENCES severity_scale(severity),
  reason        TEXT    NOT NULL,
  human_answer_id INTEGER REFERENCES human_answer(id),
  event_id      INTEGER NOT NULL REFERENCES run_event(id),
  created_at    INTEGER NOT NULL
);
```

Override не закрывает период (`decision.md` §6.3), дальше действует
`max(override, наблюдения после override)`. В `finding_severity` это добавляется
как ещё одна ветка `UNION` с фильтром `event_id >= period_start`; вынесено из
основного вью только ради читаемости — в реализации это одна строка.

### 5.7. Свежесть ревьюера

Правило «назначить прежнего ревьюера невозможно технически» (`decision.md` §7.3)
требует хранить, кто что видел. В решении такой сущности нет — она появляется
здесь.

```sql
CREATE TABLE reviewer_exposure (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id      INTEGER NOT NULL REFERENCES run(id),
  subject_id  INTEGER NOT NULL REFERENCES review_subject(id),
  revision    TEXT    NOT NULL,
  provider    TEXT    NOT NULL,
  model       TEXT    NOT NULL,
  campaign_id INTEGER NOT NULL REFERENCES review_campaign(id),
  created_at  INTEGER NOT NULL,
  UNIQUE (subject_id, revision, provider, model, campaign_id)
);

CREATE INDEX ix_exposure_lookup ON reviewer_exposure (subject_id, revision, provider, model);
```

Ключ — **фактическая пара `provider` + `model`**, а не `profile_id`: у `claude-z`
запрос `opus` возвращает `glm-5.2`, и по имени профиля свежесть не определяется.
Запись создаётся при завершении попытки ревьюера, когда `actual_model` уже
известен. Проверка при наборе линий новой кампании — один индексированный SELECT.

Из этого же следует ограничение, которое лучше знать заранее: **если фактическая
модель профиля совпала с уже отработавшей, профиль для новой кампании
недоступен**, сколько бы разных имён профилей на неё ни ссылалось. Четыре
обязательных профиля дают четыре пары — этого хватает на два кворума, но
допущение из `decision.md` §15 стоит именно здесь.

---

## 6. Граф, блокировки, события

```sql
CREATE TABLE task (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id           INTEGER NOT NULL REFERENCES run(id),
  semantic_task_id TEXT    NOT NULL,          -- ID из артефакта разбивки
  import_id        INTEGER NOT NULL REFERENCES task_graph_import(id),
  title            TEXT    NOT NULL,
  body             TEXT    NOT NULL,
  state            TEXT    NOT NULL,          -- pending|ready|running|done|invalidated|cancelled
  carry_over_of    INTEGER REFERENCES task(id),
  created_at       INTEGER NOT NULL,
  closed_at        INTEGER,
  UNIQUE (run_id, semantic_task_id)
);

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
```

Инварианты 23 и 25 закрыты схемой: `PRIMARY KEY` даёт запрет дублей рёбер,
`CHECK` — запрет self-edge, `UNIQUE(run_id, semantic_task_id)` — уникальность
смысловых ID в пределах прогона (и заодно «личность задачи ограничена одним
прогоном»). Ссылка на `import_id` в каждой задаче делает переимпорт видимым:
задачи прежней ревизии инвалидируются, а не удаляются.

**Ациклический граф — единственный инвариант, не выражаемый в SQLite
декларативно.** Проверка делается кодом внутри той же транзакции, что и вставка
рёбер, обходом от каждого нового ребра; при обнаружении цикла возвращается
конкретный путь. Атомарность даёт транзакция: снаружи никогда не видно
промежуточного состояния с циклом. Это ровно то, что требует `decision.md` §6.2
(«проверка цикла атомарна со вставкой ребра»), и никакого окна между основным
циклом и recovery audit не остаётся. Тот же обход гоняется в recovery audit как
дешёвая страховка.

```sql
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
  CHECK (kind <> 'human_question' OR question_id IS NOT NULL),
  CHECK ((cleared_at IS NULL) = (cleared_event_id IS NULL))
);

CREATE INDEX ix_blocker_open ON blocker (run_id, kind) WHERE cleared_at IS NULL;
```

Партиальный индекс по открытым блокировкам — это и есть быстрый ответ на «что
сейчас ждёт человека» (`decision.md` §5, требование индексов с первого дня).

```sql
CREATE TABLE run_event (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id     INTEGER NOT NULL REFERENCES run(id),
  kind       TEXT    NOT NULL,
  branch_id  INTEGER REFERENCES branch(id),
  stage_id   INTEGER REFERENCES stage_execution(id),
  payload    TEXT    NOT NULL,            -- JSON, схема на kind
  created_at INTEGER NOT NULL
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
```

`run_event.id` служит **общим монотонным порядком** для всей системы — на нём
стоит вычисление периода открытости. Это единственная причина, по которой
`event_id` дублируется в `finding`, `finding_observation_link`,
`finding_resolution`, `severity_override`, `blocker` и `task_graph_import`: без
общего порядка «позже последнего закрывающего» пришлось бы сравнивать
timestamps, которые в одной транзакции совпадают с точностью до миллисекунды.

### 6.5. Состояние прогона — представление

Инвариант 22: `Run` вычисляется из веток и блокировок.

```sql
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
    WHEN EXISTS (SELECT 1 FROM branch b
                  WHERE b.run_id = r.id AND b.state = 'blocked')
                                                   THEN 'waiting_human'
    ELSE 'idle'
  END AS state
FROM run r;
```

`idle` в перечне `decision.md` нет — и это не новое состояние, а честное имя для
положения «активных веток нет, блокировок нет, терминала нет». Оно означает
ошибку планировщика и должно быть видно, а не маскироваться под `waiting_human`.
Ровно тот же принцип, что у `invalid_graph`: тишина без причины — дефект, а не
состояние покоя.

---

## 7. Человек

```sql
CREATE TABLE human_question (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  public_id     TEXT    NOT NULL UNIQUE,            -- Q-3
  run_id        INTEGER NOT NULL REFERENCES run(id),
  branch_id     INTEGER REFERENCES branch(id),
  stage_id      INTEGER REFERENCES stage_execution(id),
  campaign_id   INTEGER REFERENCES review_campaign(id),
  finding_id    INTEGER REFERENCES finding(id),
  reason        TEXT    NOT NULL,      -- cap_exhausted_same | cap_exhausted_new
                                       -- | dispute | contract_error | hang
                                       -- | baseline_red | approval_gate | open_question
  question_text TEXT    NOT NULL,
  options_json  TEXT,                  -- NULL = вопрос без вариантов, это законно
  snapshot_json TEXT,                  -- severity, порог, версия политики на момент решения
  asked_at      INTEGER NOT NULL,
  answered_at   INTEGER,
  reask_count   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX ix_question_open ON human_question (run_id) WHERE answered_at IS NULL;

CREATE TABLE human_answer (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  question_id   INTEGER NOT NULL REFERENCES human_question(id),
  raw_text      TEXT    NOT NULL,
  chosen_option TEXT,                  -- заполнено = закрытие без участия модели
  interpreted_json TEXT,
  transport     TEXT    NOT NULL,
  update_id     INTEGER,
  received_at   INTEGER NOT NULL,
  UNIQUE (question_id)                 -- инвариант 20: один принятый ответ
);
```

`reason` — не украшение. Он отвечает на требование `decision.md` §7.1: причина
остановки живёт в состоянии, а не выводится из счётчика, и `cap_exhausted_same`
против `cap_exhausted_new` — это два разных вопроса человеку с разными
вариантами.

```sql
CREATE TABLE telegram_outbox (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id         INTEGER NOT NULL REFERENCES run(id),
  question_id    INTEGER REFERENCES human_question(id),
  chat_id        INTEGER NOT NULL,
  body           TEXT    NOT NULL,
  reply_markup   TEXT,
  created_at     INTEGER NOT NULL,
  sent_at        INTEGER,
  transport_message_id INTEGER,
  attempts       INTEGER NOT NULL DEFAULT 0,
  last_error     TEXT
);

CREATE INDEX ix_outbox_pending ON telegram_outbox (id) WHERE sent_at IS NULL;

CREATE TABLE telegram_inbox (
  transport   TEXT    NOT NULL,
  update_id   INTEGER NOT NULL,
  payload     TEXT    NOT NULL,
  received_at INTEGER NOT NULL,
  handled_at  INTEGER,
  PRIMARY KEY (transport, update_id)      -- инвариант 20
);

CREATE TABLE telegram_cursor (
  transport   TEXT PRIMARY KEY,
  next_offset INTEGER NOT NULL
);
```

`PRIMARY KEY (transport, update_id)` — дубль update отвергается вставкой, а не
проверкой. Порядок из `decision.md` §6.5 при этом обязателен: входящий update,
ответ и отметка «обработано» пишутся одной транзакцией.

---

## 8. Артефакты и верификация

```sql
CREATE TABLE artifact_revision (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id          INTEGER NOT NULL REFERENCES run(id),
  stage_id        INTEGER REFERENCES stage_execution(id),
  kind            TEXT    NOT NULL,          -- design | breakdown | task_plan
                                             -- | cutoff | verification | notes
  logical_path    TEXT    NOT NULL,
  revision_no     INTEGER NOT NULL,
  content_digest  TEXT    NOT NULL,
  code_sha        TEXT    NOT NULL,          -- всегда явно: артефакт может лежать
                                             -- в другом репозитории
  repo_commit     TEXT,
  produced_by_attempt_id INTEGER REFERENCES step_attempt(id),
  produced_by     TEXT    NOT NULL,          -- agent | human
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
```

Протухание апрувов при новой ревизии не требует кода: апрув привязан к
`revision_id`, у новой ревизии апрувов нет. «Апрувы предыдущей протухают»
(`decision.md` §6.7) — это свойство схемы, а не процедура.

```sql
CREATE TABLE verification_run (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id         INTEGER NOT NULL REFERENCES run(id),
  stage_id       INTEGER REFERENCES stage_execution(id),
  purpose        TEXT    NOT NULL,           -- baseline | after_fix | final
  code_sha       TEXT    NOT NULL,
  plan_json      TEXT    NOT NULL,           -- VerificationPlan как прислал агент
  plan_source    TEXT    NOT NULL,           -- recipe | agent
  policy_verdict TEXT    NOT NULL,           -- allowed | rejected:<причина>
  result_json    TEXT,                       -- команда → код возврата → вывод
  status         TEXT,                       -- green | red | error
  failure_signature TEXT,                    -- для сравнения «та же причина или другая»
  started_at     INTEGER NOT NULL,
  finished_at    INTEGER
);
```

`failure_signature` — техническая опора для таблицы из `decision.md` §10:
«красный baseline, красный сейчас **по той же причине**» уходит вопросом
человеку, «по другой причине» — обычным отказом автору. Без сохранённой подписи
провала это различие пришлось бы каждый раз выяснять у модели, то есть отдавать
суждению то, что должно быть сравнением.

---

## 9. Индексы

Все индексы, кроме созданных выше вместе с таблицами.

```sql
CREATE INDEX ix_stage_by_branch     ON stage_execution (branch_id, state);
CREATE INDEX ix_attempt_by_stage    ON step_attempt (stage_id, role);
CREATE INDEX ix_attempt_running     ON step_attempt (run_id) WHERE outcome IS NULL;
CREATE INDEX ix_campaign_by_stage   ON review_campaign (stage_id, state);
CREATE INDEX ix_campaign_open       ON review_campaign (run_id) WHERE closed_at IS NULL;
CREATE INDEX ix_finding_by_subject  ON finding (subject_id);
CREATE INDEX ix_task_ready          ON task (run_id, state);
CREATE INDEX ix_dep_child           ON task_dependency (child_task_id);
```

Три из них — прямое требование `decision.md` §5 («индексы под `review_campaign`
и `human_question` — с первого дня»): `ix_campaign_open`, `ix_question_open`,
`ix_blocker_open`. Причина в том же месте: раз напоминаний нет, команда «что
ждёт человека» — единственная защита от потерянной задачи, и она обязана быть
мгновенной.

`ix_dep_child` — под запрос готовности: задача готова, когда у неё нет
незакрытых родителей.

```sql
-- Готовые задачи.
SELECT t.id FROM task t
 WHERE t.run_id = :run_id AND t.state = 'pending'
   AND NOT EXISTS (
     SELECT 1 FROM task_dependency d JOIN task p ON p.id = d.parent_task_id
      WHERE d.child_task_id = t.id AND p.state <> 'done');

-- Защита от ложного успеха (инвариант 24): незакрытые есть, готовых нет,
-- активных попыток нет, легитимной блокировки нет.
SELECT EXISTS (SELECT 1 FROM task WHERE run_id = :r AND state NOT IN ('done','cancelled'))
   AND NOT EXISTS (/* готовые, запрос выше */)
   AND NOT EXISTS (SELECT 1 FROM step_attempt WHERE run_id = :r AND outcome IS NULL)
   AND NOT EXISTS (SELECT 1 FROM blocker WHERE run_id = :r AND cleared_at IS NULL)
  AS should_raise_invalid_graph;
```

---

## 10. Разметка инвариантов: база или код

Все 29 инвариантов `decision.md` §13. Колонка «Чем» — что именно не даст
нарушить.

| # | Инвариант | Держит | Чем |
|---|---|---|---|
| 1 | Каждое открытое замечание закрыто явным статусом | **Код** | Проверка покрытия открытых ID до записи dispositions; круг не закрывается частично |
| 2 | Наблюдение классифицировано ровно одним исходом | **База + код** | `finding_observation_link.observation_id` PK — не даст двух связей; полнота — запрос-проверка при закрытии reconciliation |
| 3 | Ссылка на закрытый ID только в `reaffirmed_closed` / `reopen_closed` | **Код** | Валидация на приёме: `existing_open` на закрытый ID = contract error |
| 4 | Счётчиков два, независимы; настраивается только `max_author_revisions` | **База** | `campaign_counters` — представление; хранимых счётчиков нет, второй ручки нет |
| 5 | Оба растут только после `succeeded` | **База** | Строка `author_revision` создаётся только по факту; `review_round.result` NULL до валидного вывода |
| 6 | Кап проверяется в момент решения «продолжать ли» | **Код** | Единственная точка принятия решения в `review.transition`; проверка `review_check_count <= max_author_revisions + 1` — assert, а не гейт |
| 7 | Личность выдаётся один раз | **База** | `finding.public_id` UNIQUE в прогоне; `first_observation_id` UNIQUE; пересчёта нет в коде |
| 8 | Каждое наблюдение несёт `severity_suggested` либо `unchanged_from`; severity в enum; ссылка назад, без циклов | **База** | `CHECK ((a IS NULL) <> (b IS NULL))`, FK на `severity_scale`, триггер обратной ссылки; «тот же finding и период» — код на reconciliation |
| 9 | `escalation_severity` монотонна вверх | **База** | Вычисляется `MAX(rank)` по периоду; понизить нечего |
| 10 | Исход ревьюера совместим с disposition | **База** | CHECK в `finding_round` |
| 11 | Решение выносит владелец круга, оно единственное | **База + код** | Одна колонка `reviewer_decision`; принадлежность попытки владельцу — код |
| 12 | Новая кампания не получает прежнюю сессию | **База + код** | `reviewer_exposure` + отбор линий по свободным парам provider+model |
| 13 | У каждого закрытия записан `resolution_authority`; reopen следует ему | **База + код** | CHECK выводит authority из resolution; маршрутизация reopen — код |
| 14 | Переход и событие — одна транзакция | **Код** | Единственный writer, `store.transaction()` пишет событие вместе с переходом |
| 15 | Все переходы §6.6 атомарны целиком | **Код** | Шесть именованных транзакционных операций, см. `architecture.md` §6 |
| 16 | Ответ записан до снятия блокировки | **Код** | Порядок внутри одной транзакции |
| 17 | После перезапуска нет двойного перехода и двойной работы | **Код** | Recovery audit: `outcome IS NULL` → `interrupted`; новая попытка после подтверждения смерти pgid |
| 18 | Один экземпляр сервиса на каталог | **ОС** | `flock` на файле + lease с heartbeat |
| 19 | Не более одной активной попытки на линию или шаг | **База** | Partial unique index `ux_attempt_active` |
| 20 | Один принятый ответ; `UNIQUE(transport, update_id)` | **База** | `UNIQUE(question_id)` в `human_answer`; PK в `telegram_inbox` |
| 21 | FK между кампанией, кругом, замечанием, наблюдением, попыткой; `UNIQUE(campaign_id, finding_id, round_no)` | **База** | FK и UNIQUE прямо в DDL |
| 22 | Состояние `Run` вычисляется | **База** | `run_state` — представление; колонки состояния нет |
| 23 | Нет self-edge, дублей, циклов | **База + код** | CHECK и PK; цикл — обход внутри той же транзакции |
| 24 | Пустая готовность без блокировки = `invalid_graph` | **Код** | Запрос §9, выполняется планировщиком и recovery audit |
| 25 | Запись в граф только атомарным импортом | **Код** | Единственный метод `task_graph.import_revision()`; прямых INSERT нет |
| 26 | Секрет не появляется в промпте, событии, манифесте, артефакте; в транскрипт — после redaction | **Код** | Redaction до записи, allowlist переменных профиля |
| 27 | РабОрк не создаёт файлов и коммитов в репозитории кода | **Конфиг + код** | Instance profile: artifact repo вне клона; git-сервис не имеет операции записи в клон, кроме checkpoint-коммита ветки задачи |
| 28 | Мутация ревьюера аннулирует результат | **Код** | Сверка tracked/untracked/index до и после; `mutation_violation` |
| 29 | Прореживание меняет только файл заметок | **Код** | Шаг с явным allowlist путей, проверка diff перед коммитом |

**Итог: 12 инвариантов из 29 держит база целиком, 5 — совместно, 12 — код.**
Это и был главный вопрос к схеме, ради которого стоило начинать с неё.

Наблюдение, которое стоит записать: **все инварианты, оставшиеся за кодом, —
это утверждения о полноте («каждое из множества покрыто») или о порядке
операций.** Ни один из них не является утверждением об отдельной строке. Это
разумная граница: реляционная схема хорошо держит форму записи и плохо — полноту
множества.

---

## 11. Что реализуется транзакцией

Шесть переходов из `decision.md` §6.6 в терминах таблиц.

| Переход | Таблицы в одной транзакции |
|---|---|
| Задан вопрос человеку | `human_question` + `telegram_outbox` + `blocker` + `run_event` |
| Получен ответ | `telegram_inbox.handled_at` + `human_answer` + `blocker.cleared_at` + новый `blocker(awaiting_continue)` + `branch.state` + `run_event` |
| Задача выполнена | `step_attempt.outcome` + `task.state='done'` + пересчёт готовности зависимых + `run_event` |
| Круг ревью закрыт | Все `finding_round` круга + `review_round.result` + `review_campaign.state` + `finding_resolution` по закрытым + `run_event` |
| Импорт графа | `task_graph_import` + `task` + `task_dependency` + инвалидация задач прежней ревизии + `run_event` |
| Эскалация | `human_question(snapshot_json)` + `telegram_outbox` + `blocker` + `run_event` |

Обратите внимание на второй: **на место снятой блокировки `human_question`
ставится `awaiting_continue`** — в той же транзакции. Иначе между снятием одной
и постановкой другой существует момент, когда ветка выглядит готовой к работе, и
планировщик её подхватит, не дождавшись команды человека.

---

## 12. Значения справочников

```sql
INSERT INTO attempt_outcome(outcome) VALUES
  ('succeeded'),('interrupted'),('hung'),('transient'),('contract_error'),('failed');

INSERT INTO round_result(result) VALUES
  ('clean'),('needs_revision'),('disputed'),('escalated');

INSERT INTO branch_state(state) VALUES
  ('ready'),('running'),('retry_wait'),('blocked'),('done'),('failed'),('cancelled');

INSERT INTO run_terminal_state(state) VALUES
  ('succeeded'),('failed'),('cancelled');

INSERT INTO campaign_state(state) VALUES
  ('discovery'),('reconciliation'),('fix_cycle'),('closed_clean'),
  ('closed_escalated'),('closed_cancelled');

INSERT INTO subject_kind(kind) VALUES ('code'),('artifact'),('task'),('stage');

INSERT INTO link_type(link_type) VALUES
  ('first_seen'),('recurrence'),('reaffirmation'),('reopening');

INSERT INTO disposition(value) VALUES ('fixed'),('rejected'),('wont_fix');

INSERT INTO reviewer_decision(value) VALUES
  ('verified_fixed'),('still_present'),('accepted_reason'),('insists');

INSERT INTO resolution_authority(value) VALUES ('reviewer'),('human'),('policy');

INSERT INTO blocker_kind(kind) VALUES
  ('human_question'),('awaiting_continue'),('dependency'),('drift'),('invalid_graph');
```

---

## 13. Миграции

Нумерованные, применяются при старте сервиса **до** recovery audit
(`decision.md` §5). Форма — каталог `migrations/NNNN_<имя>.sql`, применение в
транзакции, запись в `schema_migration`.

Правила:

1. Вниз не мигрируем. Старое ядро на новой базе не стартует: сравнивается
   `MAX(schema_migration.version)` с версией, зашитой в ядро, и при превышении
   выдаётся сообщение о несовместимости.
2. Каждая миграция идёт с проверкой `PRAGMA foreign_key_check` в конце
   транзакции — SQLite не проверяет FK при `ALTER TABLE`.
3. Изменение справочной таблицы — тоже миграция, потому что от значений зависят
   CHECK'и и FK.
4. Миграция, меняющая семантику существующих записей (а не только форму),
   поднимает major-версию ядра — а значит попадает в drift check и требует
   явного решения человека (`decision.md` §4).

Первая миграция создаёт схему целиком. Дробить историю ради красоты до первого
рабочего прогона незачем.

---

## 14. Уточнения к `decision.md`

Проектирование схемы обнаружило четыре пропуска и одну возможность. Ни одно из
принятых решений не отменяется — но без этих уточнений часть из них
нереализуема.

**1. Четвёртый тип связи `reopening`.** §6.3 перечисляет три типа
`finding_observation_link` (`first_seen`, `recurrence`, `reaffirmation`) при
четырёх исходах reconciliation. Переоткрытие оказывается без своего типа, и
тогда:

- его нельзя отличить от повтора при чтении связей;
- **период открытости вычислить нечем** — открывающих событий второго рода не
  существует, и всё правило про «новый период после `verified_fixed`» не
  работает.

Введён `reopening` с обязательным `reason`. Соответствие исход ↔ тип связи стало
биективным, и это само по себе проверяемое свойство.

**2. `unchanged_from` невозможен в слепой фазе.** §6.3 требует от каждого
наблюдения одного из двух полей, не оговаривая фазу. Но в `blind_discovery`
ревьюер не видит ledger и не знает ID прежних наблюдений — сослаться не на что.
Значит правило имеет две формы: в слепой фазе `severity_suggested` обязательна, а
`unchanged_from` возможен только в кругах проверки исправления. Валидатор ответа
обязан знать фазу.

**3. Свежесть ревьюера требует своей записи.** §7.3 говорит «система знает, кто
уже видел эту ревизию», но сущности для этого знания в модели данных нет.
Добавлена `reviewer_exposure` с ключом по фактической паре `provider` + `model`.
Отсюда же следует ограничение, которого раньше не было видно: два профиля,
резолвящиеся в одну и ту же фактическую модель, для правила свежести — один
участник.

**4. `max_author_revisions` живёт на стадии.** §8 разрешает человеку добавить
одну правку сверх лимита. Если лимит читается только из конфига флоу, это решение
негде хранить, кроме как отдельным счётчиком-исключением. Значение хранится на
`stage_execution` и увеличивается решением человека.

**5. Возможность: `severity_effective` на наблюдении.** Разрешение цепочки
`unchanged_from` выполняется один раз при вставке и хранится. Это не нарушает
запрет на хранение `escalation_severity`, потому что цепочка состоит из
immutable-записей и пересчёт всегда даст тот же результат. Выигрыш — вычисление
порога эскалации становится индексированным `MAX(rank)` вместо рекурсивного CTE
на каждом чтении.

Отдельно стоит отметить, что **`run_event.id` получил вторую роль** — общего
монотонного порядка, на котором стоит вычисление периода открытости. §6.3
говорит «период определяется событиями в `run_event`»; конкретно это означает,
что записи-границы обязаны хранить `event_id`, и это учтено в шести таблицах.
