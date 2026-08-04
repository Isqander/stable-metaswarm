# Схема состояния: SQLite

Дата: 2026-08-03. Статус: третья редакция после внешнего ревью. Связная DDL из
документа впервые исполнена целиком в SQLite in-memory: 60 таблиц, 24 индекса,
6 представлений и 6 триггеров; `PRAGMA foreign_key_check` чист. Отдельными
негативными вставками проверены enum-FK, policy verdict и производные состояния
`idle`/`cancelling`. Ранее проверенные несущие конструкции также остаются в
наборе: составные FK `author_revision`, партиальные уникальные индексы, XOR
наблюдения, триггер наследования severity и оба исправленных CHECK.

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

### 1.4. Закрытые enum'ы — справочные таблицы, а не CHECK-списки

Каждое **закрытое** перечисление живёт отдельной таблицей с FK на неё. Первая
редакция декларировала это правило, но реально создавала только
`severity_scale` и `resolution_kind`; остальные имена существовали лишь в
`INSERT` из §12. Теперь определения входят в связный DDL:

```sql
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

Значения справочников — из `decision.md` и закрытых контрактов v1, полный список
в §12. Добавление значения — миграция, а не молчаливое принятие нового текста.

Не являются enum'ами и не получают справочник:

- идентификаторы конфигурации и tagged references (`flow_id`,
  `logical_session.purpose = lane:<id> | author:stage:<id>`,
  `artifact_approval.approved_by = human | campaign:<id>`);
- открытый реестр версионированных событий `run_event.kind`;
- свободные диагностические детали (`outcome_detail`, `close_reason`,
  `failure_signature`).

Если поле выглядит как «статус плюс причина», оно разделяется на типизированный
факт и detail, а не кодируется строкой вида `rejected:<причина>`.

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
служебное        schema_migration, service_epoch
прогон           run, branch, stage_execution, step_attempt, attempt_liveness,
                 logical_session, run_event, blocker
граф             task, task_dependency, task_graph_import
review-домен     review_subject, review_campaign, review_lane, review_round,
                 author_revision, review_observation, finding,
                 finding_observation_link, finding_round, finding_resolution,
                 severity_override, reviewer_exposure, run_profile_resolution
человек          human_question, human_answer, notification_outbox,
                 telegram_inbox, telegram_cursor
артефакты        artifact_revision, artifact_approval, verification_run
справочники      severity_scale, attempt_outcome, attempt_role,
                 heartbeat_source, branch_kind, branch_state,
                 run_terminal_state, campaign_state, review_round_kind,
                 round_result, subject_kind, link_type, disposition,
                 reviewer_decision, resolution_authority, resolution_kind,
                 blocker_kind, task_state, title_authority, question_reason,
                 transport_kind, artifact_kind, artifact_producer,
                 verification_purpose, verification_plan_source,
                 verification_status
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
  kind       TEXT    NOT NULL REFERENCES branch_kind(kind), -- pipeline | task
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
  role               TEXT    NOT NULL REFERENCES attempt_role(role),
                                                        -- author | reviewer | planner | reconciler
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
  heartbeat_source       TEXT    NOT NULL REFERENCES heartbeat_source(source)
                                             -- stdout | stderr | fs
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
  kind        TEXT    NOT NULL REFERENCES review_round_kind(kind),
                                                   -- discovery | fix_check
  preceding_revision_id INTEGER REFERENCES author_revision(id),
  result      TEXT REFERENCES round_result(result), -- NULL = круг ещё идёт
  opened_at   INTEGER NOT NULL,
  closed_at   INTEGER,
  UNIQUE (campaign_id, round_no),
  CHECK ((kind = 'discovery') = (preceding_revision_id IS NULL)),
  CHECK ((result IS NULL) = (closed_at IS NULL))
);

-- Строка появляется ТОЛЬКО когда правка состоялась: попытка succeeded и дала
-- новую ревизию. Это и есть счётчик — и он защищён внешними ключами, а не
-- только дисциплиной вызывающего кода.
CREATE TABLE author_revision (
  id                   INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id          INTEGER NOT NULL REFERENCES review_campaign(id),
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
  CHECK (attempt_role = 'author'),
  CHECK (attempt_outcome = 'succeeded'),
  FOREIGN KEY (attempt_id, attempt_role)    REFERENCES step_attempt(id, role),
  FOREIGN KEY (attempt_id, attempt_outcome) REFERENCES step_attempt(id, outcome)
);
```

Составные внешние ключи требуют в родительской таблице:

```sql
CREATE UNIQUE INDEX ux_attempt_id_role    ON step_attempt (id, role);
CREATE UNIQUE INDEX ux_attempt_id_outcome ON step_attempt (id, outcome);
```

**Зачем эта конструкция.** Голый `attempt_id INTEGER REFERENCES step_attempt(id)`
позволяет сослаться на попытку ревьюера или на попытку с исходом `failed` — и
тогда инвариант 5 («счётчик растёт только после `succeeded` соответствующей
роли») держится не базой, а надеждой на вызывающий код. С парой составных FK
плюс двумя CHECK база проверяет и роль, и исход: вставить строку счётчика,
ссылающуюся на неавторскую или незавершённую попытку, физически нельзя.
Стоимость — два индекса и две денормализованные колонки, которые не могут
разойтись с источником, потому что связаны внешним ключом.

Порядок внутри транзакции при этом обязателен: сначала `UPDATE step_attempt SET
outcome = 'succeeded'`, потом `INSERT INTO author_revision`. Обратный порядок
отвергнет FK — что и требуется.

**Строка `author_revision` — свершившийся факт, а не намерение.** Роль durable
intent для checkpoint-коммита несёт сама запись `step_attempt`: она создаётся до
запуска процесса, её `id` попадает в сообщение коммита, и по нему выполняется
сверка при восстановлении. Разделение важно: если бы одна и та же строка была и
намерением, и фактом, счётчик капа рос бы до того, как правка состоялась (при
вставке до `git commit`) либо намерение не было бы durable (при вставке после).
Подробнее — `architecture.md` §8.

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

**Круг `discovery` считается первой проверкой** — на этом стоит вся арифметика
«три правки, четыре проверки». Первичный кворум, нашедший замечания, закрывается
с `result = 'needs_revision'` и попадает в `review_check_count` наравне с
кругами `fix_check`. Дальше: правка 1 → проверка 2 → правка 2 → проверка 3 →
правка 3 → проверка 4. Если бы discovery не считался, четвёртая проверка
оказалась бы пятой, и кап поехал бы на единицу — та самая ошибка, от которой
защищает таблица переходов в `decision.md` §7.1.

Разница между `discovery` и `fix_check` только в наличии
`preceding_revision_id`: у первого его нет, потому что правки ещё не было.

Инвариант 5 («оба растут только после `succeeded`») держится **структурой** для
левого счётчика и **кодом** для правого, и эту асимметрию надо назвать честно.

`author_revision` защищён составными FK: строки не существует, пока правка не
состоялась, и сослаться на попытку ревьюера или на `failed` нельзя.

`review_check_count` считает `review_round.result`, и **связать его с успешной
попыткой ревьюера тем же приёмом нельзя**: круг проверки закрывается не одной
попыткой, а набором решений — у разных findings в одном круге разные владельцы
(`finding_round.owner_lane_id`), значит и разные попытки. Одной колонки-ссылки
здесь не хватит, а вводить ради этого таблицу «попытки, закрывшие круг» —
усложнение ради галочки в таблице инвариантов.

Поэтому проверка остаётся за кодом и относится к тому же классу, что «наблюдение
не потеряно»: **утверждение о полноте множества**, а не об отдельной записи.
Формулируется так же — запросом:

```sql
-- Круг нельзя закрыть, пока не выполнены оба условия.
-- 1. У каждого открытого finding'а есть решение владельца в этом круге.
-- 2. Каждое такое решение вынесено попыткой с outcome = 'succeeded'.
SELECT fr.finding_id
  FROM finding_round fr
  LEFT JOIN step_attempt a ON a.id = fr.reviewer_attempt_id
 WHERE fr.campaign_id = :campaign_id AND fr.round_no = :round_no
   AND (fr.reviewer_decision IS NULL
        OR a.id IS NULL
        OR a.outcome <> 'succeeded'
        OR a.lane_id <> fr.owner_lane_id);
```

Пустой результат — необходимое условие для записи `review_round.result`. Тот же
запрос гоняется в recovery audit: если сервис упал между записью решений и
закрытием круга, круг останется открытым, и это правильно.

Что база всё же гарантирует: `review_round.result` и `closed_at` выставляются
только вместе (`CHECK`), так что «закрытый круг без результата» невозможен.

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

**`dedup_key` — только внутри круга, и ошибается он в безопасную сторону.**
Состав ключа взят у Gas City (`severity + title + body + file + start + end`,
нормализованные), но обосновывать его надо не происхождением, а направлением
ошибки:

| Ошибка ключа | Что происходит | Цена |
|---|---|---|
| **Промах** — две линии описали одну проблему разными словами | Обе записи уходят reconciliation-агенту, он их сгруппирует | Ноль: это и есть его работа |
| **Ложное слияние** — разные проблемы совпали по ключу | Одно замечание вместо двух, вторая проблема исчезает | Высокая, но требует почти дословного совпадения `title` **и** `body` у двух независимых линий в одном круге на одной ревизии |

То есть ключ работает как дешёвый префильтр перед моделью, а не как механизм
установления личности. Личность выдаёт рантайм, и это разные вещи: ключ живёт
внутри одного круга и никогда не используется между кругами — именно там он и
ломается, потому что после правки автора номера строк сдвигаются.

Отсюда правило разрешения сомнений то же, что у reconciliation: **при
неоднозначности лучше два замечания, чем одно.** И два приёмочных теста:
дословное совпадение склеивается ключом; разные формулировки одной проблемы
ключом не склеиваются, а доходят до reconciliation.

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
  -- Наследование обязано быть настоящим: эффективная severity равна
  -- родительской, иначе "подтвердил прежнюю" молча меняет оценку.
  SELECT RAISE(ABORT, 'severity_effective must equal parent severity_effective')
  WHERE NEW.severity_effective <> (
    SELECT p.severity_effective FROM review_observation p
     WHERE p.id = NEW.unchanged_from_id
  );
END;
```

Второй `RAISE` — не формальность. Без него вызывающий код может записать
`unchanged_from` на мягкое наблюдение и любую `severity_effective` по своему
усмотрению, и денормализация из бесплатной функции превращается в третий путь
записи severity. Проверка стоит один индексированный SELECT на вставку.

Триггер закрывает «назад по времени», «в пределах кампании» и корректность
наследования. Два оставшихся условия — «того же finding'а» и «того же периода
открытости» — на момент вставки наблюдения **проверить невозможно**: наблюдение
существует раньше личности, и привязка к finding'у появляется только на
reconciliation. Поэтому они проверяются кодом на этапе reconciliation, до записи
связей, и нарушение даёт `contract_error` с отклонением всего вывода ревьюера.
Это не ослабление: раньше момента reconciliation данных для проверки просто нет.

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
  title_authority      TEXT    NOT NULL DEFAULT 'runtime'
                      REFERENCES title_authority(authority), -- runtime | human
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
```

**Про `disposition IS NOT NULL` внутри CHECK — это не избыточность, а починка
дыры.** SQLite считает нарушением только результат FALSE; **NULL проходит**.
Первая редакция этого CHECK без явной проверки на NULL пропускала строку
`disposition = NULL, reviewer_decision = 'insists'`: сравнение `NULL = 'fixed'`
даёт NULL, `NULL AND TRUE` даёт NULL, и всё выражение становится NULL. То есть
ревьюер мог вынести решение по замечанию, на которое автор не ответил — ровно
то, что инвариант 10 должен запрещать. Проверено вставкой: без `IS NOT NULL`
строка вставляется, с ним — отвергается.

Правило, которое стоит держать при написании любого CHECK в этой схеме:
**каждая колонка, участвующая в сравнении, должна быть либо `NOT NULL` в
объявлении, либо явно проверена на NULL внутри самого CHECK.** Иначе констрейнт
молча превращается в декорацию именно на тех строках, ради которых написан.

Инвариант 21 (`UNIQUE(campaign_id, finding_id, round_no)`) держится напрямую.
Инвариант 11 («решение выносит владелец круга, и оно единственное») — тем, что
`reviewer_decision` одна колонка одной строки: второго решения записать некуда.
Код при этом обязан проверить, что `reviewer_attempt_id` принадлежит линии
`owner_lane_id` — это констрейнтом не выражается, потому что требует join.

```sql
-- Справочник обязателен: без FK неизвестное значение resolution проваливает
-- CASE в NULL, и оба CHECK ниже перестают что-либо проверять.
CREATE TABLE resolution_kind (
  resolution           TEXT PRIMARY KEY,
  resolution_authority TEXT NOT NULL REFERENCES resolution_authority(value),
  closes_period        INTEGER NOT NULL
);

CREATE TABLE finding_resolution (
  id                     INTEGER PRIMARY KEY AUTOINCREMENT,
  finding_id             INTEGER NOT NULL REFERENCES finding(id),
  seq                    INTEGER NOT NULL,
  resolution             TEXT    NOT NULL REFERENCES resolution_kind(resolution),
  resolution_authority   TEXT    NOT NULL REFERENCES resolution_authority(value),
  campaign_id            INTEGER NOT NULL REFERENCES review_campaign(id),
  round_no               INTEGER,
  human_answer_id        INTEGER REFERENCES human_answer(id),
  closes_severity_period INTEGER NOT NULL,
  event_id               INTEGER NOT NULL REFERENCES run_event(id),
  created_at             INTEGER NOT NULL,
  UNIQUE (finding_id, seq),
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

**Но всё это работает только вместе с FK на справочник и `ELSE` в `CASE`.** В
первой редакции не было ни того, ни другого, и защита оказалась мнимой:
`CASE 'bogus' WHEN … END` без `ELSE` возвращает NULL, сравнение с NULL даёт
NULL, а NULL в CHECK проходит. То есть строка с произвольным значением
`resolution` вставлялась с любым `authority` и любым флагом периода — включая
`accepted_reason`-подобное закрытие с `closes_severity_period = 1`, ту самую
лазейку, которую констрейнт должен был сделать неисполнимой. Проверено вставкой:
без `ELSE` строка проходит, с `ELSE` отвергается.

Здесь три слоя, и нужны все: FK не даёт написать неизвестное значение, `ELSE`
ловит случай, если FK окажется выключен (`PRAGMA foreign_keys` — свойство
соединения, а не файла), справочник хранит соответствие в одном месте.

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
       -- MIN, а не MAX: период начинается с ПЕРВОГО открывающего события
       -- после последнего закрывающего.
       (SELECT MIN(o.ev) FROM opens o
         WHERE o.finding_id = f.id AND o.ev > lc.ev) AS period_start_event_id
  FROM finding f JOIN last_close lc ON lc.finding_id = f.id;
```

**`MIN`, а не `MAX` — и это ровно то место, где текстовое правило и SQL
разошлись в первой редакции.** Разберём на примере, который ломает `MAX`:

```
event 10  first_seen
event 20  accepted_reason   (closes_severity_period = 0)
event 30  reopening
```

Принятый отказ период не закрывает, значит `last_close = 0`, а открывающих два:
10 и 30. `MAX` вернул бы 30 — и наблюдение из события 10 выпало бы из
накопителя, то есть переоткрытие после принятого отказа обнулило бы серьёзность.
Это ровно та лазейка, ради закрытия которой правило и написано. `MIN` возвращает
10: прежний период продолжается.

Проверка на остальных случаях таблицы границ:

| Последовательность | `MIN` | Правило `decision.md` §6.3 |
|---|---|---|
| `first_seen(10)` | 10 | Период с создания |
| `first_seen(10)`, `verified_fixed(20)`, `reopening(30)` | 30 | Новый период, счёт с нуля |
| `first_seen(10)`, `accepted_reason(20)`, `reopening(30)` | 10 | Прежний период продолжается |
| `first_seen(10)`, `verified(20)`, `reopening(30)`, `accepted(40)`, `reopening(50)` | 30 | Период от переоткрытия, отказ его не сдвигает |
| `first_seen(10)`, `verified_fixed(20)` | NULL | Период закрыт |
| `first_seen critical(10)`, `override→medium(20)`, `recurrence low(30)` | 10 | Период тот же, но `escalation_severity` = `medium`: наблюдения до override отсечены |
| то же плюс `recurrence high(40)` | 10 | `escalation_severity` = `high`: после override накопитель снова растёт |

**Открыт finding или закрыт — это другой вопрос, и на него отвечает другое
представление.** Смешивать их нельзя: `accepted_reason` закрывает замечание, но
намеренно **не** закрывает период накопления. `decision.md` §6.3 называет это
«два разных жизненных цикла у одной записи», и в схеме они обязаны быть двумя
объектами, иначе правило противоречит само себе.

```sql
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
       WHERE r.seq = (SELECT MAX(seq) FROM finding_resolution
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
```

Здесь **любое** закрытие считается закрытием, включая `accepted_reason` и
`policy_closed`, а открывающим считается последнее по времени открытие. Отсюда:
finding после принятого отказа — `closed`, а его период накопления — открыт. Это
не рассогласование, а именно то, что требуется.

Валидация `existing_open` (инвариант 3) читает `finding_status`, вычисление
порога эскалации — `finding_period`. Перепутать их — значит либо разрешить
ссылку на закрытое как на открытое, либо потерять накопитель.

```sql
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
```

**Про отсечку по `event_id` последнего override.** Правило `decision.md` §6.3 —
`max(override, наблюдения после override)`, и слово «после» несущее. Первая
редакция описывала реализацию как «ещё одна ветка `UNION`»: она добавляла
override в пул максимума, но **не отсекала прежние наблюдения**. Результат —
человек понижает `critical → medium`, а старое `critical`-наблюдение остаётся в
`MAX`, и понижение не срабатывает вовсе. Проверено: на последовательности
`critical@10 → override medium@20 → low@30` набросок возвращает `critical`,
правильная формула — `medium`; после `high@40` обе дают `high`.

Это тот же класс ошибки, что `MAX`/`MIN` выше: правило было сформулировано
верно и потеряно при переводе в SQL. Понижение человеком — единственный
разрешённый путь вниз (`decision.md` §6.3), поэтому путь живой, а не
теоретический.

Четыре следствия, которые стоит проверить глазами по таблице границ из
`decision.md` §6.3:

- `accepted_reason` и `policy_closed` не создают закрывающей записи с флагом →
  `period_start_event_id` не меняется → **накопитель живёт**;
- переоткрытие после них даёт `reopening` с `event_id` больше `period_start`, но
  `MIN(ev) WHERE ev > last_close` возвращает то же начало → **прежний период
  продолжается**;
- переоткрытие после `verified_fixed` даёт `reopening` позже закрывающего →
  начало сдвигается → **новый период, счёт с нуля**;
- `reaffirmation` исключён из накопителя намеренно: подтверждение прежнего
  отказа круга не порождает и severity не двигает.

Монотонность вверх (инвариант 9) получается сама: `MAX` не убывает при
добавлении наблюдений. Понижений ровно два, и оба явные: смена периода
(закрывающее событие) и `severity_override` человеком. Автоматического понижения
не существует ни одним путём.

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
`max(override, наблюдения после override)` — реализовано отсечкой по
`event_id` в `finding_severity` выше, а не простым добавлением в пул максимума.

`historical_max` override **не учитывает намеренно**: его смысл — «какая
серьёзность вообще наблюдалась за всю жизнь ID», и решение человека сюда не
относится. Именно поэтому расхождение между `escalation_severity` и
`historical_max` в CLI читается как диагноз: либо систематическое занижение
ревьюерами, либо след ручного понижения — и оба случая стоит увидеть.

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
  attempt_id  INTEGER NOT NULL REFERENCES step_attempt(id),
  created_at  INTEGER NOT NULL,
  UNIQUE (subject_id, revision, provider, model, campaign_id)
);

CREATE INDEX ix_exposure_lookup ON reviewer_exposure (subject_id, revision, provider, model);
CREATE INDEX ix_exposure_subject ON reviewer_exposure (subject_id, provider, model);
```

Ключ — **фактическая пара `provider` + `model`**, а не `profile_id`: у `claude-z`
запрос `opus` возвращает `glm-5.2`, и по имени профиля свежесть не определяется.

**Запись создаётся в момент передачи входа модели, а не при успешном
завершении.** Ревьюер, который получил ревизию и вернул невалидный вывод, её уже
видел: считать его свежим на следующей кампании нельзя, иначе `contract_error`
превращается в способ обойти правило свежести — достаточно один раз ответить
мусором. Поэтому строка пишется в той же транзакции, что и создание попытки, до
`spawn`.

Отсюда следует, что фактическая пара обязана быть **известна заранее**, а не
получена из ответа. Она резолвится один раз при старте прогона (проверкой
профиля) и хранится в `run_profile_resolution`; расхождение между разрешённой и
фактической моделью в ответе — это дрейф либо contract error, а не повод
отложить запись экспозиции.

```sql
CREATE TABLE run_profile_resolution (
  run_id     INTEGER NOT NULL REFERENCES run(id),
  profile_id TEXT    NOT NULL,
  provider   TEXT    NOT NULL,
  model      TEXT    NOT NULL,
  resolved_at INTEGER NOT NULL,
  PRIMARY KEY (run_id, profile_id)
);
```

**Автор ревизии не может быть её ревьюером — и это отдельное правило, а не
следствие свежести.** `reviewer_exposure` фиксирует только тех, кто проверял;
пара provider+model, написавшая ревизию, в неё не попадает, и технически ничто
не мешает назначить ту же модель линией её проверки. А это требование №5 из
исходных требований — «А сделал, Б проверил» — того же класса, что свежесть, и
держаться на аккуратности конфига оно не должно.

Записывается тем же способом, что экспозиция: у каждой авторской попытки есть
`profile_id`, а фактическая пара берётся из `run_profile_resolution`. Проверок
две, в разных местах:

```sql
-- 1. Отбор линий на круг проверки: исключить пару, авторившую эту ревизию.
SELECT r.provider, r.model
  FROM run_profile_resolution r
 WHERE r.run_id = :run_id
   AND (r.provider, r.model) NOT IN (
         SELECT rp.provider, rp.model
           FROM author_revision ar
           JOIN step_attempt a          ON a.id = ar.attempt_id
           JOIN run_profile_resolution rp
                ON rp.run_id = :run_id AND rp.profile_id = a.profile_id
          WHERE ar.id = :preceding_revision_id)
   AND (r.provider, r.model) NOT IN (SELECT provider, model FROM reviewer_exposure
                                      WHERE /* по freshness_scope */);
```

2. **Валидация конфига флоу:** профили, назначенные ролям автора и ревьюера
одной стадии, не должны резолвиться в одну фактическую пару. Это ловится до
запуска прогона и падает как ошибка конфига, а не как contract error на середине
кампании — там уже поздно, кворум набран.

Вторая проверка обязательна именно из-за трёх claude-профилей: `claude`,
`claude-m` и `claude-z` — один бинарь, и если два из них случайно окажутся на
одном бэкенде, «автор и ревьюер» станут одной моделью, а по именам профилей это
будет неотличимо.

**Область свежести — параметр стадии, а не константа.** `decision.md` даёт два
разных требования: §7.3 говорит «система знает, кто уже видел **эту ревизию**», а
§11a про финальную кампанию — «заведомо свежий участник, **не участвовавший в
предыдущих кругах**». Второе строже: на чистом пути без правок ревизия не
менялась, и по правилу «эта ревизия» финальную кампанию нельзя закрыть никем из
участников начальной, а по правилу «эта ревизия» — точнее, при смене ревизии —
можно было бы вернуть прежнего. Разводим явно:

| `freshness_scope` | Ключ проверки | Где применяется |
|---|---|---|
| `revision` | `(subject_id, revision, provider, model)` | Обычные кампании |
| `subject` | `(subject_id, provider, model)` | Финальная кампания, второй кворум |

Из этого следует ограничение, которое лучше знать заранее: **два профиля,
резолвящиеся в одну фактическую модель, для правила свежести — один участник.**
Четыре обязательных профиля дают четыре пары только если четыре бэкенда
действительно разные; допущение `decision.md` §15 стоит именно здесь, и
проверяется оно на шаге T0.3, а не после.

---

## 6. Граф, блокировки, события

### 6.1. Задачи, зависимости и импорт графа

```sql
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
  UNIQUE (import_id, semantic_task_id)
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
```

Инварианты 23 и 25 закрыты схемой: `PRIMARY KEY` даёт запрет дублей рёбер,
`CHECK` — запрет self-edge. Ссылка на `import_id` в каждой задаче делает
переимпорт видимым: задачи прежней ревизии инвалидируются, а не удаляются.

**Про уникальность смыслового ID.** `decision.md` §6.2 требует
`UNIQUE(run_id, semantic_task_id)`, но в паре с переимпортом это противоречие:
инвалидированная задача `T3` прежней ревизии и новая `T3` текущей не могут
сосуществовать, а именно этого требует «инвалидируются, а не удаляются».
Разведено на две гарантии: `UNIQUE(import_id, semantic_task_id)` — внутри одного
импорта ID уникален; партиальный `ux_task_active_semantic` — активная версия
смыслового ID ровно одна. Вместе они дают ровно то, ради чего писался исходный
инвариант, и при этом допускают историю. Уточнение зафиксировано в §14.

**Ациклический граф — единственный инвариант, не выражаемый в SQLite
декларативно.** Проверка делается кодом внутри той же транзакции, что и вставка
рёбер, обходом от каждого нового ребра; при обнаружении цикла возвращается
конкретный путь. Атомарность даёт транзакция: снаружи никогда не видно
промежуточного состояния с циклом. Это ровно то, что требует `decision.md` §6.2
(«проверка цикла атомарна со вставкой ребра»), и никакого окна между основным
циклом и recovery audit не остаётся. Тот же обход гоняется в recovery audit как
дешёвая страховка.

### 6.2. Блокировки

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

### 6.3. Append-only журнал событий

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

### 6.4. Общий монотонный порядок событий

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

`idle` теперь явно записан в перечне `decision.md`, но не является штатным
терминалом: это честное имя для положения «активных веток нет, блокировок нет,
терминала нет». Оно означает ошибку планировщика и должно быть видно, а не
маскироваться под `waiting_human`. `cancelling` столь же производен: запрос на
отмену уже durable, но не все ветки приведены к терминалу. Ровно тот же принцип,
что у `invalid_graph`: переходное или ошибочное положение показывается, а не
замалчивается.

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
  reason        TEXT    NOT NULL REFERENCES question_reason(reason),
                                       -- cap_exhausted_same | cap_exhausted_new
                                       -- | dispute | contract_error | hang
                                       -- | baseline_red | approval_gate | open_question
                                       -- | reopen_human_closed | reconcile_failed
                                       -- | lane_failure | verification_policy
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
  transport     TEXT    NOT NULL REFERENCES transport_kind(transport),
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
```

`PRIMARY KEY (transport, update_id)` — дубль update отвергается вставкой, а не
проверкой. Порядок из `decision.md` §6.5 при этом обязателен: входящий update,
ответ и отметка «обработано» пишутся одной транзакцией.

Таблица входящих названа `telegram_inbox`, потому что идемпотентность по
`update_id` — свойство именно Telegram Bot API; у CLI-транспорта входящего потока
нет, ответ приходит прямой командой. Исходящая же сторона нейтральна: домен
пишет в `notification_outbox`, ничего не зная о транспорте, и `transport = 'cli'`
означает «лежит и ждёт команды `ask`». Это не заглушка — тот же путь, та же
durable-запись, просто другой отправитель.

---

## 8. Артефакты и верификация

```sql
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
```

Протухание апрувов при новой ревизии не требует кода: апрув привязан к
`revision_id`, у новой ревизии апрувов нет. «Апрувы предыдущей протухают»
(`decision.md` §6.7) — это свойство схемы, а не процедура.

```sql
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
| 3 | Ссылка на закрытый ID только в `reaffirmed_closed` / `reopen_closed` | **Код** | Валидация на приёме по `finding_status`: `existing_open` на закрытый ID = contract error |
| 4 | Счётчиков два, независимы; настраивается только `max_author_revisions` | **База** | `campaign_counters` — представление; хранимых счётчиков нет, второй ручки нет |
| 5 | Оба растут только после `succeeded` | **База + код** | `author_revision` — составные FK `(attempt_id, role)` и `(attempt_id, outcome)`; `review_check_count` считает `review_round.result`, который база с попыткой связать не может — см. ниже |
| 6 | Кап проверяется в момент решения «продолжать ли» | **Код** | Единственная точка принятия решения в `review.transition`; проверка `review_check_count <= max_author_revisions + 1` — assert, а не гейт |
| 7 | Личность выдаётся один раз | **База** | `finding.public_id` UNIQUE в прогоне; `first_observation_id` UNIQUE; пересчёта нет в коде |
| 8 | Каждое наблюдение несёт `severity_suggested` либо `unchanged_from`; severity в enum; ссылка назад, без циклов | **База + код** | `CHECK ((a IS NULL) <> (b IS NULL))`, FK на `severity_scale`, триггер обратной ссылки и равенства унаследованной severity; «тот же finding и период» — код на reconciliation |
| 9 | `escalation_severity` монотонна вверх | **База** | Вычисляется `MAX(rank)` по периоду; понизить нечего |
| 10 | Исход ревьюера совместим с disposition | **База** | CHECK в `finding_round` — **с явной проверкой `disposition IS NOT NULL`**, иначе NULL проходит |
| 11 | Решение выносит владелец круга, оно единственное | **База + код** | Одна колонка `reviewer_decision`; принадлежность попытки владельцу — код |
| 12 | Новая кампания не получает прежнюю сессию | **База + код** | `reviewer_exposure` + отбор линий по свободным парам provider+model |
| 13 | У каждого закрытия записан `resolution_authority`; reopen следует ему | **База + код** | FK на `resolution_kind` + CHECK с `ELSE` выводят authority из resolution; маршрутизация reopen — код |
| 14 | Переход и событие — одна транзакция | **Код** | Единственный writer, `store.transaction()` пишет событие вместе с переходом |
| 15 | Все переходы §6.6 атомарны целиком | **Код** | Шесть именованных транзакционных операций, см. `architecture.md` §6 |
| 16 | Ответ записан до снятия блокировки | **Код** | Порядок внутри одной транзакции |
| 17 | После перезапуска нет двойного перехода и двойной работы | **Код** | Recovery audit: `outcome IS NULL` → `interrupted`; новая попытка после подтверждения смерти pgid |
| 18 | Один экземпляр сервиса на каталог | **ОС** | `flock` на файле + lease с heartbeat |
| 19 | Не более одной активной попытки на линию или шаг | **База** | Partial unique index `ux_attempt_active` |
| 20 | Один принятый ответ; `UNIQUE(transport, update_id)` | **База** | `UNIQUE(question_id)` в `human_answer`; PK в `telegram_inbox` |
| 21 | FK между кампанией, кругом, замечанием, наблюдением, попыткой; `UNIQUE(campaign_id, finding_id, round_no)` | **База** | FK и UNIQUE прямо в DDL |
| 22 | Состояние `Run` вычисляется | **База** | `run_state` — представление; колонки состояния нет |
| 23 | Нет self-edge, дублей, циклов; смысловой ID уникален | **База + код** | CHECK и PK; `UNIQUE(import_id, semantic_task_id)` + партиальный индекс на активную версию; цикл — обход внутри той же транзакции |
| 24 | Пустая готовность без блокировки = `invalid_graph` | **Код** | Запрос §9, выполняется планировщиком и recovery audit |
| 25 | Запись в граф только атомарным импортом | **Код** | Единственный метод `task_graph.import_revision()`; прямых INSERT нет |
| 26 | Секрет не появляется в промпте, событии, манифесте, артефакте; в транскрипт — после redaction | **Код** | Redaction до записи, allowlist переменных профиля |
| 27 | РабОрк не создаёт файлов и коммитов в репозитории кода | **Конфиг + код** | Instance profile: artifact repo вне клона; git-сервис не имеет операции записи в клон, кроме checkpoint-коммита ветки задачи |
| 28 | Мутация ревьюера аннулирует результат | **Код** | Сверка tracked/untracked/index до и после; `mutation_violation` |
| 29 | Прореживание меняет только файл заметок | **Код** | Шаг с явным allowlist путей, проверка diff перед коммитом |

**Итог: 8 инвариантов из 29 держит база целиком, 7 — совместно с кодом, 1 —
операционная система (`flock`), 1 — конфигурация, 12 — код.**

Первая редакция заявляла 12 / 5 / 12, и это было завышением дважды. Сначала —
потому что три «гарантированных базой» констрейнта на деле не работали (CHECK с
трёхзначной логикой, `CASE` без `ELSE`, FK без проверки роли и исхода). Потом —
потому что инвариант 5 был засчитан базе целиком, хотя составными FK защищена
только левая половина: счётчик правок. Правая, счётчик проверок, структурно
защищена быть не может, и признать это дешевле, чем городить таблицу ради
симметрии. Числа выше — после починки и после проверки вставкой.

Заодно снимается более сильный тезис первой редакции — «всё, что осталось за
кодом, это только полнота множества или порядок операций». Он неверен: инварианты
3, 26, 28 и 29 — обычные утверждения об отдельных фактах. Точнее так: **за кодом
остаются три класса** —

1. **полнота множества** — «каждое наблюдение классифицировано», «каждый
   открытый ID покрыт» (1, 2, 24);
2. **порядок и атомарность** — «событие в той же транзакции», «ответ до снятия
   блокировки», «новая попытка после подтверждения смерти группы» (6, 14–17, 25);
3. **сравнение с внешним миром или соседней таблицей** — «мутация ревьюера»
   (git-дифф), «секрета нет в транскрипте» (содержимое файла), «ссылка только на
   открытый ID» (join к представлению), «прореживание меняет только один файл»
   (3, 26, 28, 29).

Третий класс — самый неприятный: в нём ошибка не отвергается базой и проявляется
не сразу. Именно на него должны идти приёмочные сценарии, а не на первые два.

---

## 11. Что реализуется транзакцией

Шесть переходов из `decision.md` §6.6 в терминах таблиц.

| Переход | Таблицы в одной транзакции |
|---|---|
| Задан вопрос человеку | `human_question` + `notification_outbox` + `blocker` + `run_event` |
| Получен ответ | `telegram_inbox.handled_at` + `human_answer` + `blocker.cleared_at` + новый `blocker(awaiting_continue)` + `branch.state` + `run_event` |
| Задача выполнена | `step_attempt.outcome` + `task.state='done'` + пересчёт готовности зависимых + `run_event` |
| Круг ревью закрыт | Все `finding_round` круга + `review_round.result` + `review_campaign.state` + `finding_resolution` по закрытым + `run_event` |
| Импорт графа | `task_graph_import` + `task` + `task_dependency` + инвалидация задач прежней ревизии + `run_event` |
| Эскалация | `human_question(snapshot_json)` + `notification_outbox` + `blocker` + `run_event` |

Обратите внимание на второй: **на место снятой блокировки `human_question`
ставится `awaiting_continue`** — в той же транзакции. Иначе между снятием одной
и постановкой другой существует момент, когда ветка выглядит готовой к работе, и
планировщик её подхватит, не дождавшись команды человека.

---

## 12. Значения справочников

```sql
INSERT INTO attempt_outcome(outcome) VALUES
  ('succeeded'),('interrupted'),('hung'),('transient'),('contract_error'),('failed');

INSERT INTO attempt_role(role) VALUES
  ('author'),('reviewer'),('planner'),('reconciler');

INSERT INTO heartbeat_source(source) VALUES ('stdout'),('stderr'),('fs');

INSERT INTO branch_kind(kind) VALUES ('pipeline'),('task');

INSERT INTO round_result(result) VALUES
  ('clean'),('needs_revision'),('escalated');

INSERT INTO review_round_kind(kind) VALUES ('discovery'),('fix_check');

INSERT INTO branch_state(state) VALUES
  ('ready'),('running'),('retry_wait'),('blocked'),('done'),('failed'),('cancelled');

INSERT INTO run_terminal_state(state) VALUES
  ('succeeded'),('failed'),('cancelled');

INSERT INTO campaign_state(state) VALUES
  ('discovery'),('reconciliation'),('fix_cycle'),('closed_clean'),
  ('closed_escalated'),('closed_cancelled');

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

Проектирование схемы обнаружило восемь мест, где решение чего-то не учло, и одну
возможность (пункт 5). Ни одно из
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

Пункты 1–4 и 6–8 **перенесены в `decision.md`** и живут там как принятые
решения, а не как уточнения второго слоя: все они меняют семантику, а не форму.
Здесь они остаются с обоснованием, почему потребовались.

**6. Уникальность смыслового ID задачи — среди активных версий.** §6.2 требует
`UNIQUE(run_id, semantic_task_id)` и одновременно «задачи прежней ревизии
инвалидируются, а не удаляются». Это несовместимо: инвалидированная `T3` и новая
`T3` не могут сосуществовать под таким ключом. Разведено на
`UNIQUE(import_id, semantic_task_id)` плюс партиальный уникальный индекс на
активную версию.

**7. Область свежести ревьюера — параметр стадии.** §7.3 формулирует свежесть
через ревизию («кто уже видел эту ревизию»), а §11a для финальной кампании —
через участие («не участвовавший в предыдущих кругах»). Это разные правила, и на
чистом пути без правок они расходятся: ревизия не менялась, поэтому по первому
правилу финальную кампанию некем закрыть, а по второму — правило как раз и
работает. Введён `freshness_scope` со значениями `revision` и `subject`; для
финальной кампании и второго кворума — `subject`.

**8. Экспозиция фиксируется при передаче входа, а не при успехе.** §7.3 не
уточняет момент, и естественное прочтение — «когда узнали фактическую модель»,
то есть после ответа. Это дыра: ревьюер, вернувший `contract_error`, ревизию уже
видел, но остался бы «свежим». Значит фактическая пара provider+model обязана
резолвиться заранее (`run_profile_resolution`), а расхождение с ответом
трактуется как дрейф, а не как повод отложить запись.

### Что было неверно в первой редакции этого документа

Отдельно, потому что это ошибки дизайна, а не пропуски решения.

| Что | Чем оказалось | Как исправлено |
|---|---|---|
| `finding_period` брал `MAX` открывающего события | Переоткрытие после принятого отказа обнуляло накопитель severity — ровно та лазейка, против которой правило и написано | `MIN`; проверено на пяти последовательностях |
| `severity_override` добавлялся в пул `MAX` без отсечки прежних наблюдений | Понижение человеком не срабатывало: старое `critical`-наблюдение оставалось в максимуме. Тот же класс шва, что `MAX`/`MIN` | Отсечка по `event_id` последнего override; проверено на трёх последовательностях |
| Автор ревизии технически мог стать её ревьюером | `reviewer_exposure` фиксирует только проверявших; про писавшего правило молчало, хотя это прямо «А сделал — Б проверил» | Исключение пары автора при отборе линий + валидация конфига флоу |
| `period_start IS NULL` трактовалось как «finding закрыт» | `accepted_reason` закрывает finding, но не период: одно представление не может отвечать на оба вопроса | Два представления: `finding_status` и `finding_period` |
| CHECK совместимости пары в `finding_round` | При `disposition IS NULL` выражение давало NULL, а NULL в CHECK проходит: решение ревьюера по неотвеченному замечанию вставлялось | Явная проверка `disposition IS NOT NULL` внутри |
| CHECK-и в `finding_resolution` через `CASE` без `ELSE`, без FK на справочник | Неизвестное значение `resolution` давало NULL и обходило обе проверки, включая флаг закрытия периода | Справочник `resolution_kind` + FK + `ELSE` |
| `author_revision.attempt_id` — простой FK | Ссылка на попытку ревьюера или на `failed` не отвергалась; инвариант 5 держал код, а не база | Составные FK по `(id, role)` и `(id, outcome)` + CHECK |
| `severity_effective` при `unchanged_from` ничем не проверялась | Денормализация превращалась в третий путь записи severity | Триггер сравнивает с родительским значением |

Все найдены внешним ревью и воспроизведены исполнением до внесения правок. Два
урока, которые стоит держать при дальнейшей работе:

1. **В SQLite нарушением считается только FALSE.** Любая колонка в сравнении
   должна быть либо `NOT NULL` в объявлении, либо явно проверена внутри самого
   констрейнта; любой `CASE` в CHECK обязан иметь `ELSE`.
2. **Правило, верно сформулированное текстом, ломается при переводе в SQL — и
   это самый частый дефект здесь.** Три ошибки из восьми именно такие: `MAX`
   вместо `MIN`, отсутствие отсечки у override, одно представление вместо двух.
   Ни одну из них не поймал бы тест чистой функции домена — они живут на шве.
   Отсюда задача T1.7b в плане работ: вертикальный срез на настоящей базе.

### Правки дополнительного ревью перед реализацией

- dev-tooling вынесен из частного плана T1.1 в общее решение: пакет живёт в
  `src/metaswarm`, project/dev dependencies фиксируются парой `pyproject.toml` +
  `uv.lock`, а единая локальная проверка запускается через `scripts/check.sh`;
- `disputed` удалён из `round_result`: спор ниже порога хранится как
  `policy_closed`, а итог круга определяется оставшимися открытыми findings;
- закрытые enum-наборы получили реальные справочные таблицы и FK, а tagged/open
  значения явно отделены от enum;
- добавлены отсутствовавшие определения `run_terminal_state` и остальных
  справочников; карта таблиц синхронизирована с DDL;
- `policy_verdict = rejected:<reason>` разложен на типизированный
  `policy_allowed` и отдельный `policy_rejection_reason`;
- связная DDL выполнена целиком, а не только отдельными фрагментами.

Отдельно стоит отметить, что **`run_event.id` получил вторую роль** — общего
монотонного порядка, на котором стоит вычисление периода открытости. §6.3
говорит «период определяется событиями в `run_event`»; конкретно это означает,
что записи-границы обязаны хранить `event_id`, и это учтено в шести таблицах.
