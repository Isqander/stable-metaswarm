# Схема состояния: SQLite

**Текущий инвентарь после C-21/C-23:** **65 таблиц, 33 явных индекса,
7 представлений и 47 триггеров**. Числа получены механическим подсчётом
нормативных `CREATE`-операторов; 27 закрытых справочников не изменились.
Исторические числа `61/24/6/6` относятся к шестой редакции; после неё вошли
модель effective roster (C-01b) — **+2 таблицы,
+2 индекса, +1 представление, +9 триггеров** — и schema-effects C-01a/C-01c и
C-04: **+1 таблица** (`campaign_transition`), **+4 индекса**, **+8 триггеров**,
плюс новые колонки: `review_campaign.expected_lane_count`,
`reviewer_exposure.profile_id`, `run_id` у слота и назначения и четыре у
попытки — `campaign_id`, `round_id`, `lane_assignment_id`,
`subject_revision`. Составные FK по C-06 **внесены 2026-08-07** (§5.3.1) и имеют собственную
дельту, которую нельзя приписывать группе выше: **+1 таблица**
(`human_question_observation`), денормализованные scope-колонки с парными
`UNIQUE (id, <scope>)`, **+3 явных индекса** для составных FK попытки и
**+5 триггеров** — два на связь наблюдения с личностью и три на членство
вопроса. Соответственно у C-01a/C-01c и C-04 остаётся
`campaign_transition` и восемь триггеров: переходы, начальное состояние и
снимок кампании, границы индекса линии, неизменяемость и запрет удаления у
допуска и у резолюции профиля.
Один lifecycle-заход C-07/C-08 добавил **+9 триггеров** для attempt/round и
трёх evidence-таблиц; adversarial-проход — ещё **+8** для вопроса, ответа и
`finding_round`; защита принятого `severity_override` — ещё **+2**. Остальные
schema-effects того же свода (ограничения
`max_author_revisions >= 1` и `UNIQUE(finding_id, event_id)`) внесены одним
заходом; они не добавляют именованных schema-объектов и текущий inventory не
меняют. Связный прогон DDL после них проверяет не только число объектов, но и
полный набор ограничений. Конструкции effective
roster при этом уже
проверены отдельным прогоном на SQLite 3.45: 52 сценария с машинными
ожиданиями, включая контрпример «одна линия вернула `[]`, вторая не
завершилась», замену исполнителя, повтор замены после падения, запрет
параллельной работы двух поколений слота, зачёт работы вытесненного поколения,
cross-campaign ссылки, формы строки попытки по роли и попытку потратить один
ответ человека дважды. Второй прогон, `campaign-open.sql`, — 54 сценария по
открытию кампании, матрице переходов, неизменяемости снимка и допуску
ревьюера, включая штатный `fix_check` по новой ревизии. Третий прогон,
`scope-keys.sql`, — 79 сценариев на
составные ключи scope из §5.3.1: чужой прогон, чужая кампания, самый тихий
класс «своя кампания, но чужой круг, линия или попытка» и оба источника
решения связи — агент и человек, включая привязку ответа к кругу и к набору
показанных наблюдений. Доказательная сила проверена отдельно:
`scripts/checks/mutations.tsv` держит для ограничения назначенный шаг, и
`mutation-check.py` требует, чтобы после снятия ограничения покраснел именно он.
Четвёртый прогон, `attempt-lifecycle.sql`, — 70 сценариев на C-07/C-08:
active/open начальную форму, неизменяемость каждого input/scope-поля, единственный
terminal-переход попытки и круга и запрет UPDATE/DELETE evidence-строк.
Пятый, `audit-lifecycle.sql`, — 50 сценариев на immutable content вопроса,
append-only ответ, однократный reviewer decision и обходы через `INSERT OR
REPLACE`. Шестой, `schema-constraints.sql`, — 14 сценариев C-21/C-23: минимум
cap, duplicate override, две ортогональные допустимые пары и полный lifecycle
override. Все шесть
воспроизводятся одной командой и падают ненулевым кодом при
любом расхождении ожиданий:
`python3 scripts/checks/run-sql-check.py scripts/checks/<файл>.sql` (файлов
можно передать несколько).
Доказаны 184 мутационных случая из 185 (случай может снимать несколько
перекрывающихся ключей сразу), один — признанный долг; манифест умеет держать и признанный долг
(`todo: <причина>`), который считается недоказанным и виден в итоге отдельным
числом.

**Покрытие манифеста неполное, и это счётная величина, а не оценка.** Режим
`mutation-check.py --coverage scripts/checks/mutations.tsv` перечисляет
ограничения сценарных файлов, у которых нет ни одной строки манифеста: на
2026-08-09 таких **42 из 154**. Дополнительно granular coverage по умолчанию
считает каждый многосоставный trigger: все **55/55** верхнеуровневых ветвей
`WHEN ... OR` и все **27/27** колонок `UPDATE OF` имеют отдельную мутацию и
назначенный шаг. Строка на trigger целиком больше не выдаётся за доказательство
каждого его условия; исключение возможно только явной меткой с объяснением, и
сейчас таких исключений нет. Отдельным списком тот же режим печатает
**мёртвые ключи** — `UNIQUE`, куда входит первичный ключ, и на который никто не
ссылается: отвергнуть строку он не может (PK уже уникален), значит ему нужна не
мутация, а удаление. Найдено и удалено два, сейчас счётчик 0. Разница между «нет строки вовсе» и «строка есть,
но сценарий не изолирован», и «строка есть, но за ней многосоставное условие»
существенна. Механизмы разные: первое ловит structural `--coverage`, второе —
статус `todo:`, третье — обязательные granular rows. Ещё один режим,
`mutation-check.py --schema-sync docs/design/db-schema.md scripts/checks`,
сверяет design со стабами в обе стороны. Все **47/47** нормативных trigger'ов
присутствуют и совпадают дословно после снятия комментариев и пробелов. Для
ограничений таблиц отчёт отдельно показывает **132 нормативных / 107 покрытых /
25 без сценария**, 127 сценарных вхождений и 0 лишних. Поэтому отсутствующий во
всех стабах объект либо `CHECK`/FK/UNIQUE тоже становится видимым. Режим
возвращает ненулевой код, пока эти 25 не получат исполняемые свидетели;
зелёная trigger-parity не выдаётся за полную parity схемы.
Из structural-счёта осознанно исключены два класса:
инлайновые одноколоночные `REFERENCES` в объявлении колонки (тривиальный случай,
плюс FK на закрытые справочники доказывает отдельный манифест T1.3) и
родительские `UNIQUE (id, <scope>)` составных ключей — их снятие не ослабляет
проверку, а ломает схему, и «назначенный шаг» для них ничего не значит.

Все шесть прогонов доказывают **констрейнты и cardinality** — какие строки база
принимает, а какие нет. Атомарность «переход + событие», поведение при падении
между операциями и контракты самих операций ими не проверяются: там нужны
транзакции и код. Владельцы этих тестов разные: транзакционная механика —
T1.2, repository-примитивы (`reserve_reviewer_exposure()`) — T1.3, потребление
готовых attempt и exposure — T1.7b, а внешняя операция
`create_attempt_and_reserve_exposure()` вместе с проверкой легальности
ревизии — **T1.18 в пакете P1-D**.

**Историческая baseline-проверка шестой редакции (2026-08-05).** Тогдашняя
связная DDL была исполнена целиком в SQLite in-memory: 61 таблица, 24 индекса,
6 представлений и 6 триггеров; `PRAGMA foreign_key_check` был чист. Это не
утверждение о текущей редакции: её inventory указан в шапке. Текущая связная
нормативная DDL воспроизводимо исполняется командой
`python3 scripts/checks/run-sql-check.py --design-schema docs/design/db-schema.md`:
inventory `65/33/7/47`, семь views читаются, `foreign_key_check` пуст, точный
readback PRAGMA равен `wal/1/2/5000/0/1`, оба ограничения C-21/C-23 видны в
`sqlite_master`. Отдельными
негативными вставками проверены enum-FK, policy verdict и производные состояния
`idle`/`cancelling`; после четвёртой редакции повторный полный прогон сохранил
inventory `61/24/6/6`, а CHECK-и приняли согласованные пары
`review_campaign.state`/`closed_at` и отверг обе рассогласованные. Ранее
проверенные несущие конструкции также остаются в наборе: составные FK
`author_revision`, партиальные уникальные индексы, XOR наблюдения, триггер
наследования severity и исправленные CHECK.

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
решение. Читать обязательно: там двадцать девять уточнений с обоснованием,
включая одну необязательную возможность.

---

## 1. Соглашения

### 1.1. Режим базы

```sql
PRAGMA journal_mode = WAL;      -- читатели не блокируют единственного писателя
PRAGMA foreign_keys = ON;       -- по умолчанию OFF, включается на каждом соединении
PRAGMA synchronous = FULL;      -- см. ниже
PRAGMA busy_timeout = 5000;
PRAGMA trusted_schema = OFF;
PRAGMA recursive_triggers = ON; -- иначе INSERT OR REPLACE обходит DELETE-trigger
```

`synchronous = FULL`, а не `NORMAL`, хотя WAL с `NORMAL` переживает падение
процесса. Причина одна: ответ человека записывается durable **до** снятия
блокировки (инвариант 16), и терять его при потере питания нельзя — переспросить
некого, а напоминаний в системе нет. Цена нулевая: нагрузка — десятки транзакций
в минуту, не десятки тысяч.

`foreign_keys = ON` выставляется при открытии **каждого** соединения. Это не
свойство файла базы, и забытый PRAGMA превращает половину инвариантов ниже в
декорацию.

`recursive_triggers = ON` — такое же обязательное свойство соединения. При
значении по умолчанию `OFF` SQLite не вызывает DELETE-триггеры для неявного
удаления, которое делает `INSERT OR REPLACE`: завершённую attempt можно заменить
новой active-строкой, а evidence — другим payload. Все trigger'ы схемы только
проверяют и вызывают `RAISE(ABORT)`, сами DML не выполняют, поэтому включение не
создаёт рекурсивных цепочек.

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
  `logical_session.purpose = assignment:<id> | author:stage:<id>`,
  `artifact_approval.approved_by = human | campaign:<id>`);
- открытый реестр версионированных событий `run_event.kind`;
- свободные диагностические детали (`outcome_detail`, `close_reason`,
  `failure_signature`).

Если поле выглядит как «статус плюс причина», оно разделяется на типизированный
факт и detail, а не кодируется строкой вида `rejected:<причина>`.

### 1.5. Что означает «immutable» на практике

SQLite не умеет запрещать UPDATE/DELETE декларативно. Реестр здесь
нормативный — добавление durable-таблицы требует явно поместить её в один из
классов:

- **полностью append-only:** `run_event`, `review_lane`, `lane_waiver`,
  `review_observation`, `finding_observation_link`, `finding_resolution`,
  `severity_override`, `reviewer_exposure`, `run_profile_resolution`,
  `human_question_observation`, `human_answer`; у каждой есть пара
  `BEFORE UPDATE`/`BEFORE DELETE ... RAISE(ABORT)`;
- **управляемый lifecycle:** `step_attempt` рождается active и один раз
  терминализируется; `review_round` — open и один раз закрывается;
  `finding_round` сохраняет input/author-часть и один раз получает reviewer
  decision; у `human_question` неизменяемы scope/reason/presentation/snapshot,
  но меняются `answered_at`/`reask_count`; `lane_assignment` допускает только
  однократную привязку `session_id`;
- **неизменяемый снимок при изменяемом состоянии:** у `review_campaign`
  identity/config snapshot защищён отдельным trigger, а state/closing fields
  меняются по state machine.

Второй слой — **отсутствие обходного кода**: repository даёт только именованные
CAS/lifecycle-операции, но не generic update/delete. `recursive_triggers=ON`
обязателен: иначе любой `BEFORE DELETE` из реестра обходится `INSERT OR
REPLACE`.

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
review-домен     review_subject, review_campaign, campaign_transition,
                 review_lane, lane_assignment, lane_waiver, review_round,
                 author_revision, review_observation, finding,
                 finding_observation_link, finding_round, finding_resolution,
                 severity_override, reviewer_exposure, run_profile_resolution
человек          human_question, human_question_observation, human_answer,
                 notification_outbox,
                 telegram_inbox, telegram_cursor
артефакты        artifact_revision, artifact_approval, verification_run
справочники      severity_scale, attempt_outcome, attempt_role,
                 heartbeat_source, branch_kind, branch_state,
                 run_terminal_state, campaign_state, review_round_kind,
                 round_result, finding_round_entry_kind, subject_kind,
                 link_type, disposition,
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
```

`max_author_revisions` лежит **на стадии**, а не только в конфиге флоу, потому
что человек в ответе на исчерпание кругов может выбрать «разрешить ещё одну
правку» (`decision.md` §8) — и это увеличение относится к одной конкретной
стадии, а не к флоу. Значение при создании берётся из конфига; человеческое
решение делает `+1` здесь и пишет событие. Без этой колонки решение человека
пришлось бы хранить где-то сбоку и учитывать при каждой проверке капа — то есть
завести второй источник правды о том же числе.

Минимум равен **1**. Ноль не означает «ревью без права исправить»: первичная
reconciliation при найденных замечаниях обязана запустить первую правку автора,
и при нулевом cap штатный шаг сразу стал бы нарушением счётчиков. Поэтому
ограничение держится на трёх границах: T1.17 отвергает такой конфиг, T1.4 не
принимает `CheckFacts` с `max_author_revisions < 1`, а DDL не позволяет
сохранить повреждённый snapshot стадии. Поздний ответ человека только
увеличивает уже законное значение.

```sql
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
```

#### C-07/C-08: lifecycle до DDL

Инвариант сильнее исходных контрпримеров: **попытка хранит неизменяемый intent
и ровно один терминальный результат; evidence-строки не исчезают; круг хранит
неизменяемый input и ровно одно закрытие.**

| Поле чек-листа | Решение |
|---|---|
| Запрещённые состояния | Терминальный `INSERT` attempt/round; правка input/scope; result до terminal-перехода; повторное завершение; terminal → active; DELETE evidence/attempt/round |
| Источник истины | `step_attempt` для input/result вызова; `review_round` для результата круга; три evidence-таблицы для audit trail |
| Создающая операция и владелец | attempt создаёт runtime T1.18 до spawn; round создаёт review workflow T1.7b; repository-примитивы и CAS принадлежат T1.3 |
| Транзакционная граница | Завершение attempt пишется вместе с его domain-result; закрытие round — с campaign state и событием; триггер не заменяет эту атомарность |
| Формы | attempt: active — все terminal-поля NULL; terminal — `outcome` и `finished_at` заданы. round: open — `result/closed_at` NULL; closed — оба заданы |
| Scope | Все identity/scope/input-координаты обеих строк после INSERT неизменяемы; существующие составные FK проверяют их согласованность |
| Разрешённые UPDATE/DELETE | По одному CAS `NULL → terminal`; DELETE нет. Liveness меняет только отдельную `attempt_liveness` |
| Retry/crash | CAS различает missing и already-terminal; после неясного commit повтор не переписывает факт, recovery читает сохранённый terminal result |
| База / операция / recovery | Триггеры держат форму и lifecycle; repository держит CAS и типизированные ошибки; workflow — атомарность с соседними строками/событием; recovery только диагностирует незавершённое |
| Негативные контрпримеры | `scripts/checks/attempt-lifecycle.sql`: terminal INSERT, каждая группа input/scope, преждевременный result, повтор/откат terminal, DELETE всех пяти durable-классов |
| Синхронизация | T1.2 inventory `65/33/7/47`; T1.3 API, INV/FAIL/AC/V; граф задач T1.3; статус C-07/C-08 в своде |

`AttemptRepository.complete_attempt()` выполняет один CAS:

```sql
UPDATE step_attempt
SET outcome = :outcome,
    outcome_detail = :outcome_detail,
    actual_model = :actual_model,
    output_sha = :output_sha,
    finished_at = :finished_at,
    transcript_path = :transcript_path,
    transcript_digest = :transcript_digest
WHERE id = :attempt_id AND outcome IS NULL;
```

`rowcount = 1` — завершение состоялось. При `rowcount = 0` repository читает
строку по ID: отсутствующая даёт `RepositoryRecordNotFound`, существующая с
непустым `outcome` — `RepositoryAlreadyTerminal(entity='step_attempt', ...)`.
Одинаковый повтор не превращается в silent no-op: repository-примитив не знает,
были ли вместе с первым завершением закоммичены обязательные domain-строки и
событие, поэтому обязан показать повтор вызывающему workflow.

Partial unique index — тот случай, где SQLite делает работу за нас: пока
`outcome IS NULL`, вторая попытка той же роли в той же стадии физически не
вставляется. Инвариант перестаёт быть проверкой в сервисе.

Индекс построен по **слоту**, а не по поколению исполнителя, и это осмысленный
выбор: две активные попытки одного слота — разных поколений — означали бы, что
заменённая линия всё ещё работает параллельно с заменившей. С `lane_id` в ключе
такая пара не вставляется, а составной FK не даёт записать чужое поколение под
чужим слотом.

`round_id` — та связь, без которой гейт участия линий §5.2 невыразим: у попытки
есть `stage_id`, но на одной стадии живут несколько кампаний, а в кампании
несколько кругов. Авторская попытка круга не имеет — правка стоит **между**
кругами, и её место в цепочке задаёт `review_round.preceding_revision_id`.

`campaign_id` — не удобство, а якорь scope. Четыре независимых FK на кампанию,
круг, слот и поколение проверяют каждый свою ссылку по отдельности, и попытка с
кругом кампании A и слотом кампании B проходит их все. Три составных FK
связывают координаты в цепочку `стадия → кампания → круг | слот → поколение`, и
после этого несогласованная строка не вставляется. Проверено вставкой: до
составных ключей проходила, после — отвергается.

Три `CHECK` по роли — того же класса защита, что и `entry_kind` в
`finding_round`: **форма строки положительна, а не выводится из пустых полей.**
Reviewer-попытка без `lane_id` формально не принадлежит ни одному слоту, поэтому
не попадает ни в гейт участия, ни в strict-issued запрос — то есть работа
ревьюера была, а доказать её нечем. Автор и планировщик, наоборот, не должны
получать review-координат вовсе: `lane_id` у авторской попытки означал бы, что
правку писала линия проверки.

**Reconciler — круг без линии, и это ровно то, что говорит контракт**
(`agent-contracts.md` §4.3: «Отдельная роль `reconciler`, не линия», свежая
сессия, профиль из конфига стадии). `lane_id` у такой попытки не просто лишний:
с ним `ux_attempt_active` перестаёт держать единственность, потому что ключ
включает слот — и на одной стадии можно завести столько активных reconciler'ов,
сколько линий. С `lane_id IS NULL` они схлопываются в `COALESCE(lane_id, -1)`,
и вторая активная попытка не вставляется.

`actual_model` заполняется при завершении, потому что до запуска он неизвестен:
у `claude-z` запрос `opus` возвращает `glm-5.2`. Правило свежести ревьюера
считает по фактической паре — см. `reviewer_exposure` в §5.7.

```sql
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
  purpose           TEXT    NOT NULL,       -- assignment:12 | author:stage:7
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
  UNIQUE (id, run_id),                   -- под scope-FK кампании и родителя
  -- Вложенность не выходит за прогон: иначе рекурсия §5.1 смешает findings
  -- разных запусков.
  FOREIGN KEY (parent_subject_id, run_id) REFERENCES review_subject(id, run_id),
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
```

**Почему слот отделён от исполнителя.** Кворум — заявленное число независимых
мнений, и §7.1.1 `architecture.md` прямо разрешает человеку **заменить**
исчерпавшую бюджет линию другим профилем, не понижая кворум. В одной таблице
это невыразимо: `UNIQUE (campaign_id, lane_index)` держит слот занятым, так что
заменяющая строка либо конфликтует по индексу, либо получает следующий индекс —
и тогда roster растёт, а правило «минимальный `lane_index` = владелец» начинает
означать не то, что означало. Разделение снимает обе беды разом: слоты создаются
один раз при открытии кампании, `COUNT(*)` по ним и есть кворум, а замена — это
запись в другой таблице.

**Почему append-only, а не колонка `retired_at`.** Требование «обычные
`UPDATE`/`DELETE` прежнего назначения запрещены» выражается прямо: триггер
неизменяемости, и активное назначение определяется отсутствием преемника, а не
изменяемым флагом. Линейность цепочки держит пара «`UNIQUE (lane_id,
generation)` + триггер `trg_lane_assignment_chain`»: преемник обязан быть
следующим поколением того же слота, а номер поколения в слоте занят
однократно — значит в слоте ровно одно назначение без преемника, то самое,
которое видит `effective_roster`. `UNIQUE (replaces_id)` при такой паре
срабатывать уже не на чем; он оставлен ради индекса под антиджойн
представления и как вторая линия защиты, если триггер когда-нибудь снимут.
Побочная выгода: повторный вызов замены после падения не создаёт третье
поколение, он падает на уникальности, и recovery видит уже применённый
результат.

**Владение — на слот, исполнение — на поколение.** `review_observation.lane_id`,
`finding_round.owner_lane_id` и `finding.first_owner_lane_id` ссылаются на
`review_lane`, то есть на слот; `step_attempt` — на конкретное поколение (и на
слот составным FK, см. §4). Так владение выражает позицию в кворуме, а не
конкретную модель, и не зависит от того, каким поколением слот был исполнен:
слот, заменённый в `discovery` и ставший владельцем findings после
reconciliation, проходит гейт `a.lane_id = fr.owner_lane_id` без единой правки
запроса. Обратное решение — владение на поколении — потребовало бы переписывать
владельца при каждой замене, то есть мутировать `finding_round`.

Важно: из этого разделения **не следует**, что владельца можно менять на ходу.
Кто вправе выносить решения по чужим findings — вопрос контракта, а не формы
ссылки; он разобран ниже вместе с областью операции замены.

**Участие линий — гейт, а не следствие, и только в `discovery`.** Валидный
пустой результат `[]` законен, поэтому «линия отработала и ничего не нашла» и
«линия не отработала» неразличимы по наблюдениям — только по попыткам. Отсюда
**lane-participation gate**; он относится к слепой фазе, где кворум набирают все
линии сразу:

```sql
-- Должен вернуть 0 строк перед result круга discovery и перед spawn
-- reconciler: у каждого слота либо успешная попытка ревьюера в этом круге,
-- сделанная его ТЕКУЩИМ исполнителем, либо durable waiver человека.
-- Итерация идёт по слотам, а не по effective_roster: слот без действующего
-- назначения обязан быть нарушением, а не исчезнуть из выборки.
SELECT l.id AS lane_id
  FROM review_round rr
  JOIN review_lane  l ON l.campaign_id = rr.campaign_id
 WHERE rr.id = :round_id
   AND rr.kind = 'discovery'
   AND NOT EXISTS (
         SELECT 1 FROM lane_waiver w
          WHERE w.campaign_id = rr.campaign_id
            AND w.round_no    = rr.round_no
            AND w.lane_id     = l.id)
   AND NOT EXISTS (
         SELECT 1
           FROM effective_roster er
           JOIN step_attempt a
                ON a.round_id           = rr.id
               AND a.lane_assignment_id = er.assignment_id
               AND a.role               = 'reviewer'
               AND a.outcome            = 'succeeded'
          WHERE er.lane_id = l.id);

-- И отдельно: хотя бы одно мнение. Иначе два последовательных waiver
-- закрывают круг из двух линий вообще без проверки, и он выглядит как clean.
-- Запрос тоже самозащищён по kind: в fix_check линии не обязаны работать
-- вовсе, и без фильтра он давал бы ложное нарушение на штатном круге.
SELECT rr.id AS round_id
  FROM review_round rr
 WHERE rr.id = :round_id
   AND rr.kind = 'discovery'
   AND NOT EXISTS (
         SELECT 1
           FROM effective_roster er
           JOIN step_attempt a
                ON a.round_id           = rr.id
               AND a.lane_assignment_id = er.assignment_id
               AND a.role               = 'reviewer'
               AND a.outcome            = 'succeeded'
          WHERE er.campaign_id = rr.campaign_id);

-- И третье: действующих исполнителей ровно столько, сколько заявлено.
-- Считается effective_roster, а не review_lane: слот, оставшийся без
-- назначения, иначе прошёл бы как полноценная линия кворума.
SELECT rr.campaign_id
  FROM review_round rr
  JOIN review_campaign c ON c.id = rr.campaign_id
 WHERE rr.id = :round_id
   AND rr.kind = 'discovery'
   AND (SELECT COUNT(*) FROM effective_roster er
         WHERE er.campaign_id = c.id) <> c.expected_lane_count;
```

Все три запроса принимают **только `round_id`**, а кампанию, номер и вид круга
выводят сами: три независимых параметра `(campaign_id, round_no, round_id)`
позволяют проверить один круг, а закрыть другой, а вынесенный наружу фильтр по
`kind` рано или поздно забудут в одном из двух мест. Поэтому на круге
`fix_check` все три пусты по построению, и вызывающему не нужно помнить, к
какому виду круга гейт применим.

**Почему запросы устроены именно так.** Считать успешную попытку «по слоту»
мало: после замены исполнителя работу мог сдать не тот, кто сейчас назначен, —
вытесненное поколение. Поэтому в зачёт идёт попытка **текущего** назначения из
`effective_roster`. Но и итерировать по самому представлению нельзя: слот, у
которого действующего назначения нет, из него просто выпадет, и нарушение
станет невидимым — гейт «не найдёт» строку, о которой должен был сообщить.
Отсюда разделение ролей: **итерация — по `review_lane`, зачёт — через
`effective_roster`**. Третий запрос по той же причине считает действующих
исполнителей, а не слоты: иначе слот без назначения проходит как полноценная
линия кворума.

Дыра, из-за которой это переписано, стоит того, чтобы назвать её прямо. Пока
`lane_assignment.profile_id` не был связан с `run_profile_resolution`,
назначение с неизвестным профилем принималось базой и **исчезало** из
`effective_roster` — а вместе с ним из всех трёх запросов, которые ходили по
представлению. Два слота, у одного успешное мнение, у второго такое
назначение — и все три гейта пусты при кворуме из одной фактической линии.
Теперь профиль назначения проверяется составным FK через `run_id` слота, так что
такое назначение просто не вставляется, а запросы устроены так, что даже
пустой `effective_roster` даёт нарушение, а не тишину.

Контрпример, ради которого гейт написан: первая линия вернула `[]` и завершилась
успехом, вторая не завершилась вовсе. Три прежних гейта пусты — unlinked
observations нет, findings нет, `issued`-строк нет, — и `apply_reconciliation()`
записал бы результат круга по мнению одной линии. С этим запросом слот второй
линии возвращается строкой, и результат не пишется.

**Почему в `fix_check` этого гейта нет.** Там работают не все линии, а владельцы
открытых findings (`architecture.md` §7.1, шаг 4): линия, не нашедшая ничего в
discovery, во втором круге законно не участвует. Требовать от неё попытку —
значит либо гонять её впустую, либо выписывать ложный waiver. Участие тех, кто
действительно обязан ответить, доказывает **finding-coverage gate** ниже: он
связывает каждую строку `issued` с успешной попыткой ревьюера **своего** слота,
и владелец, не вынесший решения, круг закрыть не даёт.

Названия стоит держать раздельно, потому что гейты отвечают на разные вопросы:
**lane-participation** — «все ли линии кворума высказались» (только discovery);
**finding-coverage** — «каждый ли открытый finding представлен и отвечен»
(оба вида круга).

**Деградация кворума — строка, а не флаг на кампании.** Человек разрешает её на
конкретный круг: линия может не отработать в круге 2 и вернуться в круге 3.
Поэтому `lane_waiver` ключуется `(campaign_id, round_no, lane_id)` и несёт
ссылку на ответ человека — при разборе «почему это ревью ничего не нашло» видно
не только число фактически отработавших линий, но и чьим решением оно понижено.
Понизить кворум **до нуля** нельзя: второй запрос выше требует хотя бы одного
успешного мнения, а для отказа от проверки у человека есть отдельный вариант —
остановить ветку.

**Замена и waiver — не самостоятельные транзакции.** Обе появляются только как
следствие ответа человека на вопрос `lane_failure`, поэтому пишутся **в той же
транзакции, что и приём ответа** (§11, переход «Получен ответ»):
`human_answer` + снятие `human_question` + `awaiting_continue` + строка
`lane_assignment` либо `lane_waiver` + событие. Отдельный commit создавал бы
окно, в котором ответ уже принят и блокировка снята, а выбранного человеком
действия в базе ещё нет — и после `continue` система продолжила бы с прежним
составом линий.

- `replace_lane_assignment(lane_id, profile_id, human_answer_id)` — новое
  поколение. Предусловия: кампания не закрыта; круг — `discovery` (см. ниже); у
  слота нет активной попытки (`outcome IS NULL`); слот ещё не отработал текущий
  круг успешной попыткой ревьюера; новый профиль проходит те же проверки, что и
  при наборе линий, — свежесть по `reviewer_exposure` и запрет пары автора
  ревизии.
- `waive_lane_for_round(campaign_id, round_no, lane_id, human_answer_id)` —
  строка `lane_waiver`. Предусловия: ответ человека на `lane_failure` с
  вариантом «продолжить деградированным кворумом» и круг `discovery`.

Waiver тоже ограничен `discovery`, и по той же причине, что и замена: в
`fix_check` он ничего не решает. Пропустить там можно только владельца открытых
findings, а его строки `issued` всё равно требуют решения — finding-coverage
gate не пройдёт. Записанный waiver стал бы durable no-op: человек ответил,
система что-то сохранила, ветка всё равно встала. Значит и набор вариантов в
самом вопросе `lane_failure` зависит от вида круга: в `discovery` их три, в
`fix_check` при действующем Q57-A остаётся один — остановить ветку.

Обе — repository-операции внутри чужой транзакции, без собственных границ
коммита. Повтор при доставке того же ответа останавливают `UNIQUE (question_id)`
в `human_answer` и `UNIQUE (human_answer_id)` в обеих таблицах: один ответ
человека порождает ровно одну замену или ровно один waiver. Одной уникальности
`(lane_id, generation)` для этого мало — после рестарта операция прочитала бы
активное поколение 2 и совершенно законно создала третье.

**Замена разрешена только в `discovery`.** Схема поколений её ничем не
ограничивает, но `decision.md` §6.3 объясняет владение именно памятью: владелец
«внутри кампании продолжает свою логическую сессию и помнит свои findings, чего
требует правило памяти ревьюера», а «отдельный consolidation-ревьюер был бы
свежим агентом без этой памяти». Заменить владельца в `fix_check` — значит
отдать решения по чужим findings свежему агенту, то есть тронуть контракт
разрешения findings, а он не двигается без явного решения владельца продукта.
Поэтому: до `discovery_completed` замена возможна (findings ещё не существует, и
владельцем слот стать не успел), в `fix_check` — запрещена предусловием
операции. Если владелец исчерпал бюджет в `fix_check`, остаётся одна остановка
ветки: деградация findings с владельца не снимает, круг всё равно не закроется,
и потому waiver там тоже запрещён. Это узкое место вынесено вопросом **Q57** в
[open-questions.md](../requirements/open-questions.md).

**Почему машина состояний кампании живёт в базе, а не только в T1.4.** Ревью
P1-A нашло разрыв (C-01a): переход `discovery → reconciliation` был назван, но
не имел исполняющей операции, и у реализации оставалось два пути — получить
`InvalidCampaignTransition` либо сделать прямой `UPDATE` в обход единственной
state machine. Второй путь молча делает состояние `reconciliation`
недостижимым, а recovery перестаёт отличать «ждём вторую линию» от «ждём
reconciliation». Справочник переходов закрывает именно его: `discovery →
fix_cycle` физически не вставляется, а у терминальных состояний исходящих строк
нет, и это ровно `INV-11` плана T1.4, снятый с кода на базу.

Домен при этом никуда не девается: база проверяет **допустимость пары**, а
какое событие соответствует переходу (`discovery_completed`,
`reconciliation_clean`, `human_finalized`) решает T1.4 — в паре `fix_cycle →
fix_cycle` их вообще три.

Граница гарантии названа честно: триггер проверяет допустимость по содержимому
`campaign_transition`, поэтому гарантия ровно настолько прочна, насколько
неизменен справочник. Он migration-owned — правило §13, п. 3, — и добавление
строки `(discovery, fix_cycle)` открыло бы ровно тот прыжок, ради запрета
которого таблица заведена. Атомарность самого перехода вместе с событием
справочником не проверяется: это по-прежнему `store.transaction()` и
инвариант 14.

Цена названа честно: фикстура «кампания сразу в `fix_cycle`» больше не
создаётся одной вставкой, builders обязаны пройти легальный путь. Для тестов
это и нужно — путь, которым состояние достигается в проде, а не тот, которым
удобно его подделать.

**Открытие кампании — одна транзакция, и её незавершённость видна запросом.**
`architecture.md` §7.1 требует создать вместе предмет, кампанию, snapshot
порога, слоты с первыми назначениями, круг 1 и событие. Атомарность держит
`store.transaction()`.

**`expected_lane_count` — то, без чего незавершённость недоказуема.** Пока
заявленный размер кворума нигде не записан, «сколько линий должно быть» знает
только конфиг, а база видит лишь то, что успело записаться. Кампания на две
линии, оборвавшаяся после первой, выглядит полностью открытой: слот есть,
исполнитель есть, круг есть. Дальше её принимает и гейт участия линий — он ходит
по существующим слотам, — и одно мнение засчитывается как полный кворум. Это тот
же класс ошибки, ради которого писался C-01b, только этажом раньше.

Поэтому размер кворума снимается на кампанию тем же снимком, что порог и
версия политики, а плотность индексов держит триггер: `lane_index` обязан
лежать в `0..expected_lane_count-1`. «Слоты 0 и 2 при кворуме 2» не
вставляются, недобор ловится запросом ниже.

```sql
-- Незавершённое открытие: кампания без круга 1, с числом слотов, не равным
-- заявленному кворуму, либо со слотом без ДЕЙСТВУЮЩЕГО исполнителя.
-- Должен вернуть 0 строк.
SELECT c.id
  FROM review_campaign c
 WHERE c.closed_at IS NULL
   AND (NOT EXISTS (SELECT 1 FROM review_round r
                     WHERE r.campaign_id = c.id
                       AND r.round_no = 1 AND r.kind = 'discovery')
        OR (SELECT COUNT(*) FROM review_lane l
             WHERE l.campaign_id = c.id) <> c.expected_lane_count
        OR EXISTS (SELECT 1 FROM review_lane l
                    WHERE l.campaign_id = c.id
                      AND NOT EXISTS (SELECT 1 FROM effective_roster er
                                       WHERE er.lane_id = l.id)));
```

Последнее условие — не паранойя: слот и его первый исполнитель пишутся разными
INSERT'ами, и кампания со слотами без назначений выглядит открытой, а
`effective_roster` покажет её короче, чем она заявлена.

**Повтор открытия: база отвергает дубль, идемпотентность делает операция.**
`UNIQUE (stage_id, ordinal)` не даёт создать вторую кампанию того же порядкового
номера на стадии — но это **обнаружение дубля**, а не идемпотентность: повторный
вызов после потерянного ответа получит ошибку уникальности, а не прежний
результат. Поэтому операция открытия обязана вести себя как
`reserve_reviewer_exposure()` в §5.7: при конфликте прочитать существующую
кампанию и сверить её aggregate — предмет, снимок порога и версии политики,
`expected_lane_count`, состав слотов с их исполнителями, круг 1. Совпало —
вернуть существующую кампанию и не писать второе событие; разошлось — invariant
error, потому что тем же `(stage_id, ordinal)` пытаются открыть другую кампанию.

`severity_threshold` и `policy_version` копируются на кампанию при её создании,
а не читаются из конфига в момент решения. Причина в §6.3 `decision.md`: в
событие эскалации пишется snapshot, и вопрос «почему это ушло человеку»
отвечается без пересчёта. Если порог живёт только в конфиге, а конфиг между
остановкой и `continue` поменялся, снапшот врёт.

**Открытый human gate не закрывает кампанию.** Когда проверка даёт
`round_result = 'escalated'`, но человек ещё не ответил,
`review_campaign.state` остаётся `fix_cycle`, а `closed_at` — `NULL`; ожидание
держат `human_question` и blocker ветки. `closed_escalated` выставляется только
после окончательного ответа «принять как есть» или «остановить ветку». Ответ
«разрешить ещё одну правку» увеличивает `stage_execution.max_author_revisions`
и продолжает ту же кампанию с накопленными счётчиками.

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

Порядок внутри транзакции при этом обязателен: сначала CAS
`complete_attempt(..., outcome='succeeded')`, потом `INSERT INTO
author_revision`. Обратный порядок отвергнет FK — что и требуется.

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
-- Круг нельзя закрыть, пока каждая строка issued не прошла тот же
-- author_revision, который проверяет этот fix_check, и решение владельца
-- не вынесено успешной reviewer-попыткой той же стадии.
SELECT fr.finding_id
  FROM finding_round fr
  JOIN review_round rr
    ON rr.campaign_id = fr.campaign_id AND rr.round_no = fr.round_no
  JOIN review_campaign c ON c.id = fr.campaign_id
  LEFT JOIN author_revision ar
    ON ar.id = rr.preceding_revision_id AND ar.campaign_id = fr.campaign_id
  LEFT JOIN step_attempt d ON d.id = fr.author_attempt_id
  LEFT JOIN step_attempt a ON a.id = fr.reviewer_attempt_id
 WHERE fr.campaign_id = :campaign_id AND fr.round_no = :round_no
   AND fr.entry_kind = 'issued'
   AND (rr.kind <> 'fix_check'
        OR ar.id IS NULL
        OR fr.disposition IS NULL
        OR fr.author_attempt_id IS NULL
        OR fr.author_attempt_id <> ar.attempt_id
        OR d.id IS NULL
        OR d.role <> 'author'
        OR d.outcome <> 'succeeded'
        OR d.stage_id <> c.stage_id
        OR fr.reviewer_decision IS NULL
        OR a.id IS NULL
        OR a.role <> 'reviewer'
        OR a.outcome <> 'succeeded'
        OR a.stage_id <> c.stage_id
        OR a.round_id <> rr.id
        OR a.lane_id <> fr.owner_lane_id);
```

Пустой результат — необходимое условие для записи `review_round.result`. Тот же
запрос гоняется в recovery audit: если сервис упал между записью решений и
закрытием круга, круг останется открытым, и это правильно. Строки `post_check`
в этот запрос намеренно не входят: identity создана или переоткрыта уже после
текущей проверки, автор её ещё не видел. У такой строки все поля
author/reviewer пусты по CHECK ниже; в следующем `fix_check` открытый finding
получает отдельную строку `issued`.

Признак положительный, а не выводится из NULL. Иначе незавершённый `issued`,
ради которого существует гейт, выглядел бы точно как законный `post_check` и
молча обходил проверку. Для primary discovery строки, созданные reconciliation,
также имеют `post_check`: до первой авторской правки иных строк быть не может.

Строгий запрос выше не обнаружит finding, для которого строку текущего круга
вообще забыли создать. Поэтому перед **любым** `review_round.result` выполняется
отдельный **finding-coverage gate**. Finding считается вошедшим в кампанию,
если на него уже есть link от observation этой кампании; каждый такой открытый
finding обязан иметь в текущем круге ровно одну строку `issued` либо
`post_check`:

```sql
-- Должен вернуть 0 строк перед закрытием review_round.
SELECT fs.finding_id
  FROM finding_status fs
 WHERE fs.status = 'open'
   AND EXISTS (
         SELECT 1
           FROM finding_observation_link l
           JOIN review_observation o ON o.id = l.observation_id
          WHERE l.finding_id = fs.finding_id
            AND o.campaign_id = :campaign_id
       )
   AND NOT EXISTS (
         SELECT 1
           FROM finding_round fr
          WHERE fr.campaign_id = :campaign_id
            AND fr.round_no = :round_no
            AND fr.finding_id = fs.finding_id
       );
```

При создании `fix_check` exact open set и все его `issued`-строки пишутся одной
транзакцией. Позднее reconciliation может законно добавить `post_check`, поэтому
гейт закрытия проверяет наличие строки участия, а её форму и полноту ответа —
отдельный strict-issued запрос. В discovery тот же гейт ловит link без
`post_check`. Оба запроса принадлежат T1.3 repositories и повторяются recovery
audit; рассуждение «mapper должен был создать строку» не заменяет проверку
полноты множества.

База гарантирует не только парность `review_round.result`/`closed_at`, но и весь
lifecycle: круг вставляется открытым, input не меняется, пара закрытия
выставляется одним UPDATE ровно один раз, DELETE запрещён. Repository закрывает
круг CAS по `result IS NULL` и при нулевом `rowcount` различает
`RepositoryRecordNotFound` и `RepositoryAlreadyTerminal`, как для attempt.

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
```

`seq` выделяет `FindingRepository` как следующий номер внутри campaign в той
же writer-транзакции, а `public_id` сразу получает окончательную форму
`O-<campaign_id>-<seq>`. `UNIQUE(campaign_id, seq)` и единственный writer
делают пару однозначной; временного observation ID нет.

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
открытости» — возникают только у follow-up observation внутри
`review.decisions.v1.decisions[*]`: у blind discovery и `new_observations`
`unchanged_from` запрещён, там обязательна собственная `severity_suggested`.
Target follow-up observation уже задан внешним `finding_id`, поэтому T1.6
проверяет finding и период до вставки, а T1.7b пишет observation и
`recurrence` в одной транзакции с решением. Нарушение даёт `contract_error` и
не оставляет observation без link. Такой direct path допустим только при
`reviewer_decision IN ('still_present', 'insists')`: `verified_fixed` и
`accepted_reason` закрывают finding и не могут одновременно нести его
`recurrence`. Это выбор B в Q45 и необходимая проверка согласованности решения.

**Где `unchanged_from` вообще возможен.** В фазе `blind_discovery` ревьюер не
видит ledger и не знает ID прежних наблюдений — сослаться ему не на что, и любое
наблюдение слепой фазы обязано нести собственную `severity_suggested`.
`unchanged_from` появляется только там, где ревьюер видит открытые замечания: в
кругах проверки исправления, которые ведёт владелец finding'а. Валидатор ответа
знает фазу и требует разного — см. `agent-contracts.md` §3.

### 5.3.1. Scope: где его держит база, а где код

Ревью P1-A (C-06) потребовало по каждой связи явного выбора: составной FK,
удаление дублирующей колонки либо честная переклассификация в «База + код» с
перечнем запросов. Выбор сделан так.

**Держит база — там, где scope-колонка в таблице уже есть.** Составными
ключами замкнуты: стадия ↔ прогон её ветки; попытка ↔ прогон своей стадии;
наблюдение ↔ круг, слот, предмет, попытка и ревизия своей кампании; личность ↔
первая кампания, её круг, слот, наблюдение и ревизия; `finding_round` ↔ круг,
слот-владелец и попытка ревьюера своей кампании; закрытие ↔ круг своей кампании;
авторская правка ↔ стадия своей кампании; допуск ревьюера ↔ шесть ключей §5.7.
Общий приём один: денормализованная scope-колонка плюс `UNIQUE (id, <scope>)` в
родителе — тот же, что уже применён к `author_revision`.

**Остаётся за кодом — там, где ключ потребовал бы новых колонок ради связи,
которая и так проверяется запросом.** Таких мест четыре, и все четыре с
перечисленными проверками:

| Связь | Почему не FK | Чем держится |
|---|---|---|
| `finding_observation_link`: finding того же прогона | `campaign_id` и `round_id` в таблице уже есть и закрывают наблюдение, круг, роль и исход решившей попытки; `run_id` пришлось бы тащить ради одной оставшейся связи | Recovery-запрос ниже |
| `finding_round.author_attempt_id` ↔ стадия кампании | У автора нет review-координат (CHECK §4), общий якорь — стадия, а `stage_id` в `finding_round` не хранится | Strict-issued гейт §5.2 сравнивает `d.stage_id = c.stage_id` явно |
| `author_revision.artifact_revision_id` ↔ `logical_path` предмета | `logical_path` — свойство артефакта, а не ревизии кампании; ключ потребовал бы протащить путь в правку | Предусловие `create_attempt_and_reserve_exposure()` в T1.18, AC записан в графе |
| Цикл в дереве `review_subject` | Многозвенный цикл констрейнтом не выражается | Проверка при вставке в той же транзакции (§5.1) |

```sql
-- Recovery audit: связь между наблюдением и личностью разных прогонов.
-- Должен вернуть 0 строк.
SELECT l.observation_id
  FROM finding_observation_link l
  JOIN review_observation o ON o.id = l.observation_id
  JOIN review_campaign    c ON c.id = o.campaign_id
  JOIN finding            f ON f.id = l.finding_id
 WHERE f.run_id <> c.run_id;
```

**Что закрыто у `finding_observation_link` базой, а не соглашением.** Прежняя
формулировка «валидация на приёме» покрывала слишком многое, и вот что из неё
переехало в схему: связь ставит только **успешная** попытка (составной FK на
`outcome` плюс CHECK), только роли `reviewer` или `reconciler`, только попытка
**того же круга**, что и наблюдение, а ревьюер вправе связать лишь **повтор**,
лишь **своё собственное** наблюдение и лишь в `fix_check`. Последние два
сравнивают строку с соседней таблицей, поэтому триггер; всё остальное — ключи и
CHECK.

**У решения два законных источника, и это часть контракта, а не деталь.**
Кроме агента связь ставит **человек** — в двух названных решением Q49 случаях:
он запасной reconciler при `reconcile_failed` (`architecture.md` §7.1.2, ответ
мапится в те же четыре исхода) и он же решает судьбу наблюдения при
`reopen_human_closed`, где link намеренно не создаётся до ответа. Первая
редакция этих ключей человека не предусматривала — и тем самым делала оба
принятых пути незаписываемыми. Подставить в такую строку последнюю
провалившуюся попытку нельзя (`outcome <> 'succeeded'`), а синтезировать
фиктивную успешную — ложь в аудите ровно там, где аудит важнее всего.

Поэтому источников два и ровно один: `decided_by_attempt_id` (с ролью и
исходом) **либо** `decided_by_human_answer_id`, что держит `CHECK` с `<>`.

**Ответа «по этой кампании» недостаточно, и это отдельный урок.** Кампания
живёт несколько кругов: ответ на `reconcile_failed` из круга 1 формально
подошёл бы наблюдению круга 2, которого человек никогда не видел, — и
исторический ответ классифицировал бы новое наблюдение. Поэтому у
review-вопроса появился `round_id` (обязательный для обеих причин по CHECK), а
состав того, что человек видел, вынесен в `human_question_observation` —
нормализованное членство вместо разбора `snapshot_json` кодом. Триггер требует
три вещи: вопрос той же кампании **и того же круга**, наблюдение **входит в
набор вопроса**, а если у строки членства зафиксирован target (случай
`reopen_human_closed`), то и `finding_id` связи обязан ему совпасть. Одного
круга мало: reopen-запрос покрывает лишь часть наблюдений круга, и T1.5
требует полного покрытия именно pending-набора.

Форма target задана **обязательной**, а не опциональной:
`CHECK ((reason = 'reopen_human_closed') = (finding_id IS NOT NULL))`. Без
такого равенства оставался обход — записать reopen-членство без цели, и
проверка «та ли это личность» промолчала бы, потому что сравнивать не с чем.
У `reconcile_failed`, наоборот, цели нет по смыслу: человек там заменяет
reconciler и классифицирует наблюдение сам. Сама строка членства при этом
проверяет свой scope ключами — вопрос и наблюдение обязаны быть из одной
кампании и одного круга, а target из того же прогона: audit-факт «человек это
видел» должен быть истинным сам по себе, а не после того, как его отбракует
создание связи.

Кто именно решил — читается из того, какая колонка заполнена; отдельного
`authority` не нужно, как и в `finding_resolution`, где authority выводится из
вида закрытия.

Владельцы разведены по фазам, и путать их нельзя. Agent-вариант связи пишет
reconciliation (T1.5/T1.7b). **Членство `human_question_observation` пишет тот,
кто задаёт вопрос** — human-gate транзакция T1.7b: `human_question`, все строки
членства, outbox, blocker, состояние ветки и событие кладутся вместе. Состав
приходит
из домена, а не от formatter'а (порт, который реализует T1.16, — чистая функция,
она не читает базу, возвращает только представление и членство не выбирает), и у
двух причин он разный:

- `reconcile_failed` — **ровно raw observations того payload**, который
  построил `T1.5.build_reconcile_failed_question()` после исчерпания бюджета в
  T1.18. Не «все наблюдения круга»: направленные follow-up уже связаны
  механически и в reconciliation-вход не входили, а показать их человеку
  значило бы дать право переклассифицировать уже решённое;
- `reopen_human_closed` — точный pending-набор `AwaitHumanReopen`, каждая
  строка с target `finding_id`.

T1.16 при обработке ответа членство только **читает**.

Почему не наоборот: записывать членство при обработке ответа значило бы, что
система задним числом объявляет, будто человек видел наблюдение. Отсюда и
негативный AC у T1.16 — ответ прошлого круга к наблюдению текущего обязан
отвергаться, — и fault-injection тест у T1.7b: вопрос и полный набор либо
записаны вместе, либо отсутствуют вместе.

**Что именно держит заморозку набора — разделено честно.** База запрещает
дописывать членство **после появления ответа** (триггер выше). Промежуток между
коммитом вопроса и ответом закрывает не она, а операция: T1.7b пишет набор
целиком в одной транзакции с вопросом, а repository не предоставляет
отдельного позднего `INSERT` в эту таблицу. Соответственно и проверок две:
интеграционная на отсутствие частичного набора после fault injection — у T1.7b,
негативная на позднюю дозапись — на уровне базы.

Разница между двумя колонками таблицы важна: **утверждение, закрытое ключом,
нельзя нарушить и потому незачем проверять; утверждение, оставленное коду,
обязано иметь названный запрос** — иначе это не выбор, а забывчивость.

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
```

Севериты на личности нет — намеренно (`decision.md` §6.3). `first_round_id`
хранится, потому что причина остановки цикла требует различить «автор трижды не
смог починить одно и то же» и «ревьюер нашёл новое на четвёртой проверке», а это
и есть круг первого появления.

`event_id` — ссылка на запись в общем журнале. Она несёт **порядок** и нужна для
вычисления периода открытости, см. §5.6.

**Выдача пары internal/public ID.** `FindingRepository` внутри уже открытого
`BEGIN IMMEDIATE` вычисляет следующий номер AUTOINCREMENT как
`COALESCE((SELECT seq FROM sqlite_sequence WHERE name = 'finding'), 0) + 1` и
одним INSERT задаёт явный `id = N`, `public_id = 'F-' || N`.
Явная вставка максимального INTEGER PRIMARY KEY обновляет sequence самой
SQLite. Единственный writer исключает гонку между чтением и INSERT; rollback
откатывает и строку, и sequence, а удаление уже committed finding не позволяет
переиспользовать номер. Временная строка вроде `pending-*` не создаётся и
публичную границу не пересекает.

```sql
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
```

`observation_id` как первичный ключ даёт «одна связь на наблюдение» без единой
строчки кода — вторая связь физически не вставляется. Половина инварианта 2
получена констрейнтом.

**Вторая половина — «наблюдение не потеряно» — констрейнтом не выражается**, это
утверждение о полноте, а не об отдельной строке. Полный гейт выполняется кодом
непосредственно перед записью `review_round.result`, после механических links
из решений и всех необходимых reconciliation:

```sql
-- Должен вернуть 0 строк перед закрытием review_round.
SELECT o.id, o.public_id
  FROM review_observation o
  LEFT JOIN finding_observation_link l ON l.observation_id = o.id
 WHERE o.round_id = :round_id AND l.observation_id IS NULL;
```

Тот же запрос гоняется в recovery audit, но результат зависит от состояния
круга. В открытом круге строки без link — законное незавершённое
reconciliation: recovery возобновляет его. Если `review_round.result` уже
записан, хотя запрос непуст, это нарушение инварианта. Запрос намеренно не
сужается до входа T1.5: он страхует оба пути и остаётся глобальным гейтом круга.

**Четыре типа связи.** Четыре исхода reconciliation отображаются в них
биективно; direct follow-up path Q45 использует тот же `recurrence`, потому что
target уже известен:

| Классификация / источник | `link_type` | Что делает с кругом |
|---|---|---|
| `new` | `first_seen` | Входит в текущий круг, создаётся новый ID |
| `existing_open(id)` либо follow-up observation при `still_present`/`insists` по известному ID | `recurrence` | Входит в текущий круг; во втором случае target задаёт внешний `finding_id` без reconciliation |
| `reaffirmed_closed(id)` | `reaffirmation` | Круга не порождает; допустим только после `accepted_reason` |
| `reopen_closed(id, reason)` | `reopening` | Переводит закрытое в текущий круг |

`decision.md` §6.3 перечисляет три типа связи при четырёх исходах — это
пропуск, а не решение: без отдельного `reopening` переоткрытие неотличимо от
повтора, и период открытости вычислить нечем. Уточнение зафиксировано в §14.

### 5.5. Круг по замечанию и закрытие

```sql
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

**Про составной ключ `(campaign_id, round_no)` — это C-06a, и он важнее, чем
кажется.** `round_no` не глобален: он уникален только внутри кампании. Без
ключа строка `finding_round` могла ссылаться на номер круга, существующий лишь
в другой кампании, — и такая строка не просто существовала, а **выпадала** из
strict-issued гейта, потому что он построен на `INNER JOIN review_round`.
Ошибка, ради поимки которой гейт написан, делала гейт слепым к самой себе.
Теперь orphan не вставляется, и слепота INNER JOIN перестаёт быть проблемой:
диагностировать нечего.

Инвариант 21 (`UNIQUE(campaign_id, finding_id, round_no)`) держится напрямую.
Инвариант 11 («решение выносит владелец круга, и оно единственное») — тем, что
`reviewer_decision` одна колонка одной строки: второго решения записать некуда.
Код при этом обязан проверить, что `reviewer_attempt_id` принадлежит линии
`owner_lane_id` — это констрейнтом не выражается, потому что требует join.

`owner_lane_id` ссылается на **слот**, а не на исполнителя (§5.2). Слот,
заменённый в `discovery` до появления findings, становится владельцем на общих
основаниях, и гейт `a.lane_id = fr.owner_lane_id` выполняется без единой правки.
Замена уже действующего владельца в `fix_check` при этом запрещена — не формой
ссылки, а предусловием операции: см. §5.2.

`entry_kind = issued` создаётся для точного roster, который автор получил до
`fix_check`; его `author_attempt_id` обязан совпасть с попыткой
`review_round.preceding_revision_id`, что проверяет гейт §5.2. Reconciliation
создаёт `post_check` только если target ещё не представлен в текущем круге.
Владелец такой строки — минимальный `lane_index` среди observations, впервые
введших target в этот круг; существующую строку `issued` и её owner менять
нельзя.

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
  run_id                 INTEGER NOT NULL REFERENCES run(id),
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
```

Одна пара `finding_id + event_id` имеет не более одного override. Это именно
составная граница: одно человеческое событие может изменить несколько findings,
а один finding — получить новый override в более позднем событии. Без неё два
override одной пары одновременно становились «последними» и раздваивали строку
в `finding_last_override`, а затем и в `finding_severity`. Строка — принятое
аудируемое решение человека, поэтому она append-only: иначе `DELETE` либо
`INSERT OR REPLACE` обходят `UNIQUE`, задним числом меняют последний override и
тем самым порог эскалации. Запрет скрытого DELETE при REPLACE опирается на
обязательный `recursive_triggers=ON` (§1.1).

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
```

Ключ — **фактическая пара `provider` + `model`**, а не `profile_id`: у `claude-z`
запрос `opus` возвращает `glm-5.2`, и по имени профиля свежесть не определяется.

**`revision` — та, которую попытка реально получила, а не та, с которой кампания
открылась.** Разница появляется на первом же `fix_check`: линия проверяет
`author_revision.output_sha`, то есть другое состояние предмета, и запись
исходной ревизии сделала бы ledger свежести ложным — модель видела новое, а в
базе стоит старое. Поэтому `step_attempt` несёт `subject_revision` (что ушло
агенту), а строка допуска связана с ним составным ключом
`(first_attempt_id, revision)`. Прежний FK на `review_subject(id, revision)`
снят: он запрещал ровно тот случай, ради которого существует `fix_check`.
**Что именно считается ревизией — зависит от `review_subject.kind`:**

| kind | Ревизия в `discovery` | Ревизия в `fix_check` |
|---|---|---|
| `code`, `task`, `stage` | `review_subject.revision` (git SHA) | `author_revision.output_sha` |
| `artifact` | `content_digest` текущей `artifact_revision` предмета | `content_digest` ревизии из `author_revision.artifact_revision_id` |

Для артефактов берётся именно `content_digest`, а не `artifact_revision.id`:
допуск должен опираться на воспроизводимый идентификатор содержимого, который
одинаково читается в CLI, в событии и между прогонами, а внутренний ключ этим
свойством не обладает. Без этой строки таблицы реализация артефактного
`fix_check` выбрала бы представление ревизии наугад, и два места разошлись бы
молча.

**Легальность ревизии — предусловие операции, и владелец у неё T1.18.** База
проверяет согласованность двух строк (допуск ссылается на ту ревизию, которую
попытка получила), но не то, что само значение — законная ревизия этой кампании;
триггером это выражалось бы рекурсией по её авторским правкам, и цена не стоит
выигрыша. Проверку выполняет `create_attempt_and_reserve_exposure()` в T1.18
(пакет P1-D) вместе с отбором и spawn: там же, где известно, какую ревизию
агенту передают. T1.3 даёт примитивы — запись попытки с `subject_revision` и
`reserve_reviewer_exposure()`, — но собственного правила не содержит. Передача
владельца не снимает требования: у T1.18 обязаны появиться названная операция,
предусловие «ревизия принадлежит предмету кампании либо одной из её авторских
правок» и негативный AC на произвольное значение. Требование записано в описание
T1.18 в графе задач (`task-plans.md`), а не только здесь, — иначе оно потерялось
бы к моменту написания плана.

Для артефактов у этого предусловия есть вторая половина, которую DDL тоже не
закрывает: `author_revision.artifact_revision_id` ссылается на
`artifact_revision(id)` без привязки к прогону и логическому пути, поэтому
формально можно взять digest ревизии чужого артефакта или чужого прогона.
Проверка принадлежит той же операции, а сам разрыв — из семейства C-06b.

Отсюда же следует, что замена исполнителя слота (§5.2) требует своей строки
экспозиции: ключ включает `campaign_id`, а пара у нового поколения другая, так
что конфликта с прежней строкой нет. Тот же отбор — свежесть плюс запрет пары
автора ревизии — новое назначение проходит заново, а не наследует от
заменённого.

**Запись создаётся в момент передачи входа модели, а не при успешном
завершении.** Ревьюер, который получил ревизию и вернул невалидный вывод, её уже
видел: считать его свежим на следующей кампании нельзя, иначе `contract_error`
превращается в способ обойти правило свежести — достаточно один раз ответить
мусором. Поэтому строка пишется в той же транзакции, что и создание попытки, до
`spawn`.

**Строка — факт допуска, а не журнал попыток** (решение Q50 A). Различие не
терминологическое: ревью P1-A показало, что при журнальном чтении `UNIQUE`
ломается на двух штатных путях. Первый — reconciler: `agent-contracts.md` §4.3
по умолчанию даёт ему профиль линии с наименьшим индексом, то есть ту же пару
provider+model на той же ревизии и в той же кампании. Второй — retry после
`contract_error`, где смысл правила как раз требует, чтобы строка осталась
одна. С membership-семантикой оба пути штатные: строка одна,
`first_attempt_id` указывает на **первую** передачу, а кто ещё работал под этой
парой — видно в `step_attempt`.

Отсюда две операции, а не одна, и это разделение важно не только для
терминологии:

- **`create_attempt_and_reserve_exposure(...)`** — внешняя транзакционная
  операция шага «до spawn». Проверяет допуск (свежесть пары и запрет пары
  автора ревизии), создаёт `step_attempt`, затем вызывает вторую;
- **`reserve_reviewer_exposure(attempt_id)`** — repository-метод внутри той же
  транзакции: вставляет membership либо, при конфликте, **читает и сверяет**
  существующую строку.

Порядок задан внешним ключом `first_attempt_id → step_attempt(id)` и обратным
быть не может: membership не на что ссылаться.

**Внутренний метод принимает только идентификатор попытки.** Кампанию, предмет,
ревизию, профиль и фактическую пару он выводит сам — из `step_attempt` и
`run_profile_resolution`. Это не удобство сигнатуры: пять независимых
параметров вызывающий может передать несогласованными, и тогда допуск
записывается на пару, которой попытка не соответствует. Именно эту дыру
закрывают два последних ключа в DDL выше, а узкая сигнатура делает её
невозможной ещё до базы.

Сверка — не «проглотить конфликт»: безусловный `INSERT OR IGNORE` запрещён,
потому что маскирует ошибку вызывающего. Конфликт по ключу вообще возможен
только когда ревизия та же — а это два законных случая и один незаконный.

**Законно.** Новая попытка принадлежит той же кампании и **действующему**
назначению того же слота (`lane_assignment_id` совпадает с `assignment_id`
слота в `effective_roster`) либо это `reconciler` той же кампании и той же
ревизии. Сюда попадают оба штатных пути:

- *retry в том же круге* — предыдущая попытка того же логического шага
  завершилась `contract_error`, `transient`, `hung` или `interrupted`. Последний
  не забыт: прерванная перезапуском попытка повторяется с чистого листа и
  бюджета не тратит (`architecture.md` §8). «Предыдущая» здесь — строго
  **последняя по `id`** среди попыток ключа
  `(campaign_id, round_id, lane_assignment_id, role)`; иначе после цепочки
  `contract_error → failed` реализация посмотрит на старый retryable-исход и
  разрешит лишний запуск;
- *следующий круг той же кампании* — когда автор ничего не исправил
  (`rejected`/`wont_fix` по всем findings), новой авторской ревизии нет, и
  `fix_check` идёт по той же. Требовать здесь совпадения `round_id` было бы
  прямым нарушением Q50 и правила памяти линии: внутри кампании линия
  продолжает свою сессию из круга в круг.

**Незаконно.** Вторая линия с той же фактической парой, попытка вытесненного
поколения слота, повтор поверх уже успешной работы **этого же круга**. При
отказе откатывается вся транзакция, включая только что созданную попытку: иначе
в базе останется attempt без допуска, и recovery увидит осиротевшую запись.

Отдельной ссылки `retry_of_id` в схеме нет: координаты попытки плюс правило
«последняя по `id`» различают продолжение и новую работу. Если цепочки повторов
внутри одного назначения понадобится разбирать точнее, ссылка появится — но
тогда как осознанное расширение, а не как заплатка к неопределённому «предыдущая
попытка».

Из membership-семантики следует и формулировка инварианта в T1.3: не «вторая
запись отвергается», а **«дубля не появляется»**. `UNIQUE` не запрещает
планировщику назначить уже видевшую пару — он лишь не даёт создать вторую
строку допуска; свежесть проверяется при назначении линии или кампании, а не
перед каждым запуском.

**Что membership не закрывает.** Строка появляется только вместе с первой
попыткой, поэтому между открытием кампании (линии уже отобраны) и `spawn`
допуск нигде не зафиксирован. Пока кампании одной стадии открываются
последовательно по `ordinal`, это безопасно: второй кворум отбирается уже после
того, как первый отспавнил линии. Появится параллельное открытие — допуск
придётся резервировать раньше попытки, то есть делать `first_attempt_id`
дозаписываемым. Пишу это здесь, чтобы ограничение было видно до, а не после.

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
Для branch-scoped вопроса одной строки blocker недостаточно: в той же
транзакции `branch_id` и `question_id` обязательны, а `branch.state`
переводится в `blocked`. Nullable `branch_id` сохраняется для вопросов уровня
Run, но review-вопрос всегда branch-scoped.

Recovery проверяет связь в обе стороны до запуска планировщика:

```sql
-- Открытый branch-scoped human gate не может оставить ветку рабочей.
SELECT bl.id, bl.branch_id
  FROM blocker bl
  JOIN branch b ON b.id = bl.branch_id
 WHERE bl.cleared_at IS NULL
   AND bl.kind IN ('human_question', 'awaiting_continue')
   AND b.state <> 'blocked';

-- Заблокированная ветка не может остаться без durable-причины.
SELECT b.id
  FROM branch b
 WHERE b.state = 'blocked'
   AND NOT EXISTS (
     SELECT 1 FROM blocker bl
      WHERE bl.branch_id = b.id AND bl.cleared_at IS NULL
   );

-- Открытый branch-scoped вопрос обязан иметь matching blocker.
SELECT q.id, q.branch_id
  FROM human_question q
  LEFT JOIN blocker bl
    ON bl.question_id = q.id
   AND bl.branch_id = q.branch_id
   AND bl.kind = 'human_question'
   AND bl.cleared_at IS NULL
 WHERE q.branch_id IS NOT NULL AND q.answered_at IS NULL
   AND bl.id IS NULL;
```

Первый и третий случаи восстанавливаются атомарно из durable-вопроса до
readiness; второй никогда не лечится переводом в `ready` по догадке.

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

Инвариант 22: `Run` вычисляется из состояний веток; blocker и физическое
`branch.state` обязаны быть синхронизированы транзакцией и recovery-аудитом.

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
```

`QuestionRepository` выделяет `human_question.id` и `public_id = Q-<id>` тем же
приёмом, что finding: явный следующий AUTOINCREMENT ID внутри active
transaction и один INSERT окончательной строки. Rollback не оставляет ни
вопроса, ни имени.

`reason` — не украшение. Он отвечает на требование `decision.md` §7.1: причина
остановки живёт в состоянии, а не выводится из счётчика, и `cap_exhausted_same`
против `cap_exhausted_new` — это два разных вопроса человеку с разными
вариантами.

Для агрегированного `reason = 'dispute'` по Q46 вопрос относится к кампании:
`campaign_id` заполнен, одиночный `finding_id` равен `NULL`, а полный
упорядоченный список findings с severity/threshold/policy version хранится в
`snapshot_json`. Это намеренная денормализованная audit-копия решения, а не
единственный источник findings: исходные observations, links и resolutions
остаются нормализованы. Отдельная junction-таблица question→finding для v1 не
нужна, потому что ответ применяется к campaign/open set целиком.

Обратная сторона того же правила: для `dispute` и `cap_exhausted_*`
`chosen_option` либо `interpreted_json` принятого ответа кодируют ровно одно
кампанийное действие. Смешанная per-finding раскладка не вставляется в
`human_answer` и не снимает blocker; она увеличивает `reask_count` и порождает
переспрос через outbox. Существующей схемы достаточно, потому что
`human_answer` хранит только уже принятый итог интерпретации.

При этих review-причинах открытая строка `human_question` вместе с blocker'ом
является состоянием ожидания. Она намеренно не дублируется значением
`review_campaign.state`: кампания остаётся в `fix_cycle`, чтобы разрешённая
человеком дополнительная правка продолжила тот же цикл, а не создала кампанию с
обнулёнными счётчиками.

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
CREATE INDEX ix_attempt_by_round    ON step_attempt (round_id, lane_assignment_id, role);
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

`ix_attempt_by_round` — под lane-participation gate §5.2: он выполняется перед
каждым `review_round.result` круга `discovery` и перед spawn reconciler. Ключ
идёт по `lane_assignment_id`, а не по слоту, потому что в зачёт входит работа
именно действующего исполнителя.

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

Все 30 инвариантов `decision.md` §13. Колонка «Чем» — что именно не даст
нарушить.

| # | Инвариант | Держит | Чем |
|---|---|---|---|
| 1 | Каждый открытый finding кампании представлен в текущем круге; каждый `issued` получил disposition автора и решение владельца; `post_check` переносит работу в следующий круг | **База + код** | `entry_kind` — enum FK и CHECK формы строки; roster-запрос §5.2 ловит отсутствующую строку участия, strict-issued запрос связывает `issued` с `preceding_revision_id`, успешными author/reviewer attempts и owner; `post_check` исключён только из второго гейта |
| 2 | Каждое наблюдение получает ровно одну связь: через reconciliation либо детерминированную `recurrence` известного target только при `still_present`/`insists` | **База + код** | `finding_observation_link.observation_id` PK — не даст двух связей; допустимость решения и полнота — код до транзакции и глобальный запрос перед `review_round.result` |
| 3 | `existing_open` — только открытый ID; `reaffirmed_closed` — только закрытый через `accepted_reason`; `reopen_closed` — любой закрытый ID | **Код** | Валидация на приёме по `finding_status`: status и `last_resolution` проверяются до записи |
| 4 | Счётчиков два, независимы; настраивается только `max_author_revisions` | **База** | `campaign_counters` — представление; хранимых счётчиков нет, второй ручки нет |
| 5 | Оба растут только после `succeeded` | **База + код** | `author_revision` — составные FK `(attempt_id, role)` и `(attempt_id, outcome)`; перед `review_round.result` запрос §5.2 связывает каждую `issued`-строку с той же author revision и успешным решением owner |
| 6 | Кап проверяется в момент решения «продолжать ли» | **Код** | Единственная точка принятия решения в `review.transition`; проверка `review_check_count <= max_author_revisions + 1` — assert, а не гейт |
| 7 | Личность выдаётся один раз | **База** | `finding.public_id` UNIQUE в прогоне; `first_observation_id` UNIQUE; пересчёта нет в коде |
| 8 | Каждое наблюдение несёт `severity_suggested` либо `unchanged_from`; severity в enum; ссылка назад, без циклов | **База + код** | `CHECK ((a IS NULL) <> (b IS NULL))`, FK на `severity_scale`, триггер обратной ссылки и равенства унаследованной severity; «тот же finding и период» — T1.6 до direct link либо после reconciliation |
| 9 | `escalation_severity` монотонна вверх | **База** | Вычисляется `MAX(rank)` по периоду; понизить нечего; `UNIQUE(finding_id, event_id)` не даёт двум override одновременно стать последними и раздвоить view, а immutable/no-delete trigger'ы вместе с `recursive_triggers=ON` не дают удалить либо подменить принятое решение |
| 10 | Исход ревьюера совместим с disposition | **База** | CHECK в `finding_round` — **с явной проверкой `disposition IS NOT NULL`**, иначе NULL проходит |
| 11 | Решение выносит владелец круга, оно единственное | **База + код** | Одна колонка `reviewer_decision`; запрос §5.2 проверяет роль, stage, outcome и `lane_id = owner_lane_id`; у `post_check` решения быть не может по CHECK |
| 12 | Новая кампания не получает прежнюю сессию | **База + код** | `reviewer_exposure` как факт допуска (`UNIQUE` не даёт появиться дублю) + отбор линий по свободным парам provider+model; замена исполнителя слота проходит тот же отбор |
| 13 | У каждого закрытия записан `resolution_authority`; reopen следует ему | **База + код** | FK на `resolution_kind` + CHECK с `ELSE` выводят authority из resolution; маршрутизация reopen — код |
| 14 | Переход и событие — одна транзакция | **Код** | Единственный writer, `store.transaction()` пишет событие вместе с переходом |
| 15 | Все переходы §6.6 атомарны целиком | **Код** | Восемь именованных транзакционных операций (§11); branch-scoped вопрос включает blocker с обеими ссылками и `branch.state='blocked'`. Допустимость самих переходов кампании при этом держит **база**: справочник `campaign_transition` + триггер |
| 16 | Ответ записан до снятия блокировки | **Код** | Ответ, clear `human_question` и новый `awaiting_continue` пишутся одной TX; ветка остаётся `blocked` |
| 17 | После перезапуска нет двойного перехода и двойной работы | **База + код** | База: terminal attempt/round записываются однократно, active attempt ограничена partial UNIQUE; код: recovery подтверждает смерть pgid, CAS-терминализирует и только затем создаёт новую попытку |
| 18 | Один экземпляр сервиса на каталог | **ОС** | `flock` на файле + lease с heartbeat |
| 19 | Не более одной активной попытки на линию или шаг | **База** | Partial unique index `ux_attempt_active`; ключ — слот, поэтому заменённое и заменившее поколения не могут работать параллельно |
| 20 | Один принятый ответ; `UNIQUE(transport, update_id)` | **База** | `UNIQUE(question_id)` в append-only `human_answer`; PK в `telegram_inbox`; `recursive_triggers=ON` не даёт `INSERT OR REPLACE` удалить прежний ответ |
| 21 | FK между кампанией, кругом, замечанием, наблюдением, попыткой; `UNIQUE(campaign_id, finding_id, round_no)` | **База** | Составные FK §5.3.1 — связь с чужой кампанией, прогоном или стадией не вставляется; `entry_kind` FK и UNIQUE прямо в DDL |
| 22 | Состояние `Run` вычисляется; branch blocker согласован с физическим состоянием ветки | **База + код** | `run_state` — представление; атомарная запись пары и три recovery-запроса §6.2 |
| 23 | Нет self-edge, дублей, циклов; смысловой ID уникален | **База + код** | CHECK и PK; `UNIQUE(import_id, semantic_task_id)` + партиальный индекс на активную версию; цикл — обход внутри той же транзакции |
| 24 | Пустая готовность без блокировки = `invalid_graph` | **Код** | Запрос §9, выполняется планировщиком и recovery audit |
| 25 | Запись в граф только атомарным импортом | **Код** | Единственный метод `task_graph.import_revision()`; прямых INSERT нет |
| 26 | Секрет не появляется в промпте, событии, манифесте, артефакте; в транскрипт — после redaction | **Код** | Redaction до записи, allowlist переменных профиля |
| 27 | РабОрк не создаёт файлов и коммитов в репозитории кода | **Конфиг + код** | Instance profile: artifact repo вне клона; git-сервис не имеет операции записи в клон, кроме checkpoint-коммита ветки задачи |
| 28 | Мутация ревьюера аннулирует результат | **Код** | Сверка tracked/untracked/index до и после; `mutation_violation` |
| 29 | Прореживание меняет только файл заметок | **Код** | Шаг с явным allowlist путей, проверка diff перед коммитом |
| 30 | `review_campaign.closed_at` заполнен только у терминальной кампании и обязателен для неё | **База** | CHECK связывает три `closed_*` значения с `closed_at IS NOT NULL` в обе стороны |

**Итог: 8 инвариантов из 30 держит база целиком, 10 — совместно с кодом, 1 —
операционная система (`flock`), 1 — конфигурация, 10 — код.**

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

1. **полнота множества** — «каждое наблюдение получило одну связь», «каждый
   открытый ID покрыт», «каждый слот кворума отработал или отпущен waiver'ом»
   (1, 2, 24 и оба гейта §5.2);
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

Шесть переходов из `decision.md` §6.6 в терминах таблиц плюс два, которых там
не было: открытие кампании и завершение слепой фазы. Оба назвало ревью P1-A
(C-01a и C-01c) — они существовали в тексте `architecture.md` §7.1, но не имели
ни операции, ни границы транзакции, и потому расползались по вызовам.

| Переход | Таблицы в одной транзакции |
|---|---|
| Задан branch-scoped вопрос человеку | `human_question` + все строки `human_question_observation` для `reconcile_failed`/`reopen_human_closed` + `notification_outbox` + `blocker(branch_id, question_id)` + `branch.state='blocked'` + `run_event`. Членство именно здесь, а не «позже допишем»: после `human_answer` триггер строку уже не примет, и вопрос без набора означал бы решение по показанному, чего никто не видел |
| Получен ответ | `telegram_inbox.handled_at` + `human_answer` + `blocker(human_question).cleared_at` + новый `blocker(awaiting_continue, branch_id)` + сохранённый `branch.state='blocked'` + review-следствие (`stage_execution.max_author_revisions += 1` при дополнительной правке; `review_campaign.state='closed_escalated'` при окончательном ответе; `lane_assignment` нового поколения либо `lane_waiver` при ответе на `lane_failure`) + `run_event` |
| Задача выполнена | CAS `complete_attempt()` + `task.state='done'` + пересчёт готовности зависимых + `run_event` |
| Круг ревью закрыт | Все `finding_round` круга + CAS `close_round()` + `finding_resolution` по закрытым + состояние кампании (`closed_clean` при успехе; прежний `fix_cycle` при `escalated` до ответа человека) + `run_event` |
| Кампания открыта | `review_subject` (или существующий) + `review_campaign(state='discovery')` со snapshot порога + слоты `review_lane` + первые `lane_assignment` + `review_round(1, discovery)` + `run_event` |
| Слепая фаза завершена | `review_campaign.state='reconciliation'` + `run_event(discovery_completed)`; коммит **до** spawn reconciler, иначе после падения recovery всё ещё видит `discovery` |
| Импорт графа | `task_graph_import` + `task` + `task_dependency` + инвалидация задач прежней ревизии + `run_event` |
| Эскалация | CAS `close_round(result='escalated')` + `human_question(snapshot_json)` + `notification_outbox` + `blocker(branch_id, question_id)` + `branch.state='blocked'` + сохранённый `review_campaign.state='fix_cycle'` + `run_event` |

Отдельная атомарная операция внутри открытого `fix_check` не добавляет нового
перехода машины состояний, но тоже неделима: follow-up `review_observation`
из `decisions[*]` + direct link `recurrence` + `finding_round.reviewer_decision`
и соответствующее resolution/human-следствие + `run_event`. Невалидный
`unchanged_from` либо follow-up при закрывающем `verified_fixed`/
`accepted_reason` отклоняет ответ до операции. `new_observations` могут остаться
непривязанными только пока отдельный reconciliation ещё не завершён; записать
`review_round.result` в таком состоянии запрещает глобальный гейт §5.4.

Состав effective roster (§5.2) отдельных переходов не добавляет: замена линии и
понижение кворума — это **review-следствие перехода «Получен ответ»**, и в его
транзакцию входит либо строка `lane_assignment` следующего поколения (со
ссылками на заменённое назначение и на `human_answer`), либо строка
`lane_waiver(campaign_id, round_no, lane_id)`. Своей границы коммита у них нет:
иначе между принятым ответом и применённым действием существует момент, когда
блокировка уже снята, а состав линий ещё прежний, — и `continue` уводит кампанию
дальше с ним.

Ещё одна операция границы транзакции не образует, но порядок внутри неё
обязателен: перед каждым `spawn` пишутся `step_attempt` и строка допуска
`reviewer_exposure` — именно в этом порядке, потому что `first_attempt_id`
ссылается на попытку (§5.7). Отдельная транзакция здесь и нужна: попытка должна
быть durable до запуска процесса, иначе после падения некому сопоставить
осиротевший процесс с записью.

Повтор доставки того же ответа отсекается на входе (`UNIQUE (question_id)` в
`human_answer`, PK в `telegram_inbox`). Внутри review-домена один ответ даёт
ровно одно действие, и это две разные проверки: `UNIQUE (human_answer_id)` в
каждой таблице запрещает повтор того же вида, а встречные триггеры
`trg_*_answer_xor` — комбинацию «тем же ответом и заменили линию, и понизили
кворум». Раздельных уникальностей для этого мало: каждая видит только свою
таблицу.

Обратите внимание на переход «Получен ответ»: **на место снятой блокировки
`human_question` ставится `awaiting_continue`** — в той же транзакции. Иначе
между снятием одной и постановкой другой существует момент, когда ветка
выглядит готовой к работе, и планировщик её подхватит, не дождавшись команды
человека.

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
   CHECK'и и FK. Сильнее всего это относится к `campaign_transition`: триггер
   переходов проверяет допустимость **по её содержимому**, поэтому добавленная
   строка `(discovery, fix_cycle)` открывает ровно тот прыжок, ради запрета
   которого таблица и заведена. Repository API мутации справочников не
   предоставляет, а изменение набора переходов — это смена семантики по
   правилу 4 ниже, то есть major-версия ядра и явное решение человека, а не
   правка на ходу. Startup-проверка сверяет набор строк с эталоном ядра.
4. Миграция, меняющая семантику существующих записей (а не только форму),
   поднимает major-версию ядра — а значит попадает в drift check и требует
   явного решения человека (`decision.md` §4).

Первая миграция создаёт схему целиком. Дробить историю ради красоты до первого
рабочего прогона незачем.

---

## 14. Уточнения к `decision.md`

Проектирование и последующее ревью таск-планов дали двадцать девять уточнений:
двадцать восемь мест, где решение чего-то не учло, и одна возможность (пункт 5).
Ни одно из принятых
решений не отменяется — но без этих уточнений часть из них нереализуема.

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

Пункты 1–4 и 6–13 **перенесены в `decision.md`** и живут там как принятые
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

**9. Human gate — не терминал кампании.** Диаграмма первой редакции переводила
кампанию в `closed_escalated` сразу при исчерпании капа или споре выше порога,
но §8 разрешает человеку добавить одну правку и продолжить цикл. Новая кампания
не сохраняет счётчики старой, а обратный переход из состояния `closed_*` ломает
сам смысл терминала. Поэтому до ответа человека кампания остаётся в
`fix_cycle`, текущий круг закрывается как `escalated`, а остановку выражают
`human_question` и blocker ветки. `closed_escalated` появляется только после
окончательного ответа «принять» или «остановить»; дополнительная правка
увеличивает лимит стадии и продолжает ту же кампанию.

Это решение явно подтверждено владельцем 2026-08-04 после обсуждения
противоречия: разрешение дополнительной правки должно продолжать ту же кампанию
с прежними счётчиками, а не открывать новую и не возвращать терминал к жизни.

**10. Терминальность кампании должна совпадать с `closed_at`.** После уточнения
пункта 9 состояние `fix_cycle` с заполненным `closed_at` означало бы
одновременно «цикл можно продолжить» и «кампания закрыта»; терминальное
`closed_*` с `closed_at = NULL` ломало бы запросы незавершённых кампаний в
обратную сторону. В `review_campaign` добавлен симметричный CHECK: `closed_at`
заполнен тогда и только тогда, когда `state` входит в три терминальных
состояния.

**11. Новый finding на последнем `fix_check` требует нового observation в
контракте решений.** Явный ответ владельца Q13a требует различать «автор не
починил прежнее» и «ревьюер нашёл новое на последней проверке», не сбрасывая
общий cap. Первая версия `review.decisions.v1` позволяла отвечать только по уже
выданным `finding_id`, а T1.5 вызывался лишь после первичного discovery. Поэтому
на любом вызове `decide_after_check()` все findings имели `first_round_id`
первого круга и `cap_exhausted_new` был недостижим.

Контракт `review.decisions.v1` получил обязательный массив
`new_observations`. После валидации решений новые observations текущего
`fix_check` проходят тот же reconciliation T1.5 на post-decision ledger;
только он выдаёт им новую личность или связывает с существующей. Затем
собирается open set и вызывается T1.4. Новая личность получает
`first_round_id` текущего `review_round`, поэтому
различение причин становится исполнимым. Схема уже содержала всё необходимое:
`review_observation.round_id`, `finding.first_round_id` и два значения
`question_reason`; DDL менять не пришлось. Это не новое решение, а приведение
дизайна к ранее зафиксированному ответу Q13a.

**12. `reaffirmed_closed` допустим только после `accepted_reason`.**
`decision.md` разрешал этот исход только для принятого аргументированного отказа,
а первая редакция `agent-contracts.md` — для любого закрытого ID. Широкое
правило позволяло модели тихо потерять регресс после `verified_fixed`, обойти
закрытие по политике или не вернуть человеку finding, который прежде закрыл он.

После явного обсуждения владелец выбрал строгую границу Q44. Другая проблема
или observation с неуверенной связью получает `new`; если reconciler распознал
тот же finding, `reaffirmed_closed` разрешён только при
`last_resolution = accepted_reason`. После `verified_fixed`, `policy_closed`
или `human_decision` используется `reopen_closed`; human authority снова
открывает вопрос человеку. Его ответ `keep_closed` может создать связь
`reaffirmation`, но это явное подтверждение человека, а не тихий исход агента.
Схему менять не потребовалось: `finding_status.last_resolution` и
`last_authority` уже дают валидатору оба факта.

**13. Follow-up observation известного finding связывает рантайм, а не
reconciler.** После добавления `new_observations` первая редакция оставила две
несовместимые формулировки: T1.5 получал только новые observations без target,
но любая связь якобы могла появиться только на reconciliation. Поэтому
observation внутри `decisions[*]` с внешним `finding_id` и законным
`unchanged_from` мог остаться без link и заблокировать закрытие круга.

В Q45 владелец выбрал вариант B. Внешний `finding_id` уже является
детерминированным target: T1.6 проверяет, что `unchanged_from` относится к тому
же finding и текущему периоду, после чего T1.7b атомарно пишет observation,
`finding_observation_link(link_type='recurrence')` и решение. Только blind
discovery и `new_observations` с неизвестной личностью проходят T1.5. Полный
запрос observations без link остаётся глобальным гейтом перед
`review_round.result`; в открытом круге recovery трактует такие строки как
незавершённый reconciliation, а не как готовый повреждённый результат. DDL
менять не потребовалось: link уже хранит target, тип, attempt и event.

**14. Direct follow-up совместим только с решением, оставляющим finding
открытым.** Первая редакция варианта B разрешала вложенный observation при любом
`reviewer_decision`. Поэтому база принимала противоречивое состояние:
`verified_fixed` или `accepted_reason` закрывал finding, а `recurrence` в той же
транзакции утверждала, что проблема снова наблюдается; completeness gate этого
не замечал, а `escalation_severity` закрытого периода становилась `NULL`.

Контракт уточнён без новой развилки: follow-up observation допустим только при
`still_present` и `insists`, уже определённых как решения, оставляющие finding
открытым. При `verified_fixed`/`accepted_reason` ответ отклоняется как
`contract_error`; отдельная проблема должна идти через `new_observations`.
Проверку формы выполняет T1.7 до записи, T1.7b доказывает атомарность и отказ на
реальной базе. DDL не менялась: это межстрочный инвариант входного payload,
который SQLite не выражает, пока observation ещё не вставлен.

**15. Один dispute-вопрос хранит все одновременно эскалируемые findings.**
Первая граница T1.4 принимала не более одного `EscalatingDispute`, хотя один
check может содержать несколько `insists` не ниже порога. Детерминированный
выбор одного скрывал бы остальные причины, а ответ «принять как есть» всё равно
распространяется на кампанию целиком.

По явному решению владельца Q46 T1.6 возвращает полный упорядоченный список,
T1.4 переносит его в один `AskHuman`, а T1.7b пишет один campaign-level
`human_question`: `finding_id = NULL`, все элементы — в `snapshot_json`.
Отдельная таблица не добавлена; исходные связи остаются нормализованными, а
snapshot нужен для воспроизводимого объяснения уже принятого решения.

**16. Ответ на review-вопрос задаёт одно действие для всей кампании.** Q46
сделал вопрос агрегированным, но первая формулировка контракта оставила
свободному тексту неограниченную форму. Поэтому понятный человеку смешанный
ответ вроде «один finding принять, другой вернуть в работу» мог быть частично
интерпретирован, хотя persisted state умеет выразить только одно действие на
кампанию.

Новой развилки и DDL нет: это необходимая обратная граница уже выбранного Q46.
Для `dispute` и `cap_exhausted_*` интерпретатор возвращает ровно одно из трёх
кампанийных действий. Смешанный/per-finding результат считается неоднозначным,
не создаёт `human_answer`, не снимает blocker и уходит на ограниченный
переспрос; после лимита принимается только ключ варианта.

**17. Общая граница восьми ответов агента должна быть строгой и конечной.** До
плана T1.7 у контракта не было численного лимита payload, правила duplicate
JSON keys/unknown fields/coercion, а `handoff.cutoff.v1` назывался схемой без
точной JSON-формы. Кроме того, сводка §11 потеряла две из пяти уже утверждённых
проверок verification policy (`path` и `target`), а пустой verification plan
давал ложный зелёный результат. Последующее ревью нашло ещё три двусмысленности
той же границы: `new` reconciliation-группа могла не дать title или передать
title существующему ID, enum `expect` не был перечислен, а запрет non-finite
чисел не учитывал переполнение литерала вроде `1e999`. Реализатор либо придумал
бы несовместимые правила, либо принял двусмысленный результат.

Зафиксировано без изменения DDL: payload между маркерами — один строгий
JSON-object до `1 MiB`, schema registry содержит ровно восемь ответов агента,
модели запрещают extra/coercion, а verification plan требует непустые
`steps`/`rationale` и единственный `expect=exit_zero`. Любое non-finite число
отклоняется до модели. `new`-группа требует непустой title, а исходы известного
ID title запрещают. Cut-off получил структурные поля, а SHA-256 считает сервис
по canonical payload после валидации. Все внешние факты
(существование пути/SHA, scoped ledger, policy allow/deny lists) приходят
T1.7 immutable snapshot-контекстом; владелец домена или эффекта повторно
проверяет несущий инвариант перед записью/исполнением.

**18. Finding, вошедший после проверки, не обязан иметь фиктивный ответ автора
в текущем круге.** После Q13a reconciliation `new_observations` создаёт новую
identity внутри уже выполняющегося `fix_check`. Первая версия гейта §5.2
проверяла все `finding_round` одинаково и требовала от такой строки disposition
и reviewer decision, которых физически ещё не могло быть. Тот же дефект
возникал в primary discovery. Выводить исключение по NULL опасно: незавершённый
выданный finding выглядит точно так же и обходит гейт.

По явному решению владельца Q47 введён закрытый `entry_kind`: `issued` для
roster, который автор видел до текущей проверки, и `post_check` для identity,
созданной или переоткрытой только по её результату. CHECK делает все поля
author/reviewer у `post_check` пустыми; строгий гейт проверяет только `issued` и
связывает её author attempt с `review_round.preceding_revision_id`. В следующем
`fix_check` открытый finding получает новую строку `issued`. Если target уже
представлен в текущем круге, reconciliation не создаёт дубль. Изменение
потребовало нового enum-справочника и увеличило inventory до `61/24/6/6`.

**19. Durable human gate обязан физически остановить branch.** Первая версия
атомарной операции создавала `human_question` и blocker, но не переводила
`branch.state` в `blocked`. Представление `run_state` читает состояния веток,
поэтому Run продолжал выглядеть `running`, а планировщик имел право взять
следующий шаг — вопреки уже записанному правилу «ветка стоит».

По явному решению владельца Q48 любой branch-scoped вопрос одной транзакцией
пишет blocker с `branch_id`/`question_id`, переводит ветку в `blocked` и пишет
событие. Ответ заменяет `human_question` на `awaiting_continue`, не меняя
`blocked`; только `continue` возвращает `ready`. Recovery проверяет пару в обе
стороны и никогда не разблокирует ветку по отсутствию строки наугад. Это не
останавливает соседние ветки: при наличии любой активной Run остаётся
`running`, иначе вычисляется `waiting_human`.

**20. Короткие review-core ID выделяются вместе с их монотонными
компонентами.** Схема требовала формы `F-17`, `O-4-12` и `Q-3`, а T1.5
намеренно возвращает identity intent без ID, но граница allocator до T1.7b не
была определена. Два естественных обхода плохи:
ID из текста превращает identity в content hash, а INSERT временного
`public_id` с последующим commit делает промежуточное имя наблюдаемым.

T1.7b использует свойство единственного writer. Для finding/question repository
внутри `BEGIN IMMEDIATE` берёт `COALESCE(current sqlite_sequence, 0) + 1`, одним INSERT
записывает `id=N` и `public_id=F-N`/`Q-N`. Observation получает следующий
`seq` кампании и сразу `O-<campaign_id>-<seq>`. Rollback ничего не публикует;
committed/удалённый INTEGER ID не переиспользуется благодаря AUTOINCREMENT.
Это локальная persistence-механика, а не новый способ сопоставлять findings.

**21. Строгий гейт существующих `issued`-строк не доказывает полноту roster.**
Первая версия Q47 проверяла только строки, которые уже есть в
`finding_round`. Если application layer забывал включить один открытый finding,
запрос получал пустое множество нарушений, круг закрывался, а finding молча
переезжал дальше без disposition и решения owner.

Добавлен отдельный положительный гейт §5.2: каждый открытый finding, уже
связанный с observation этой кампании, обязан иметь строку участия в текущем
круге. При создании `fix_check` это exact `issued` roster; после проверки новая
или reopened identity может иметь `post_check`. Гейт выполняется и для
discovery, и для fix-check непосредственно перед result, а также в recovery.
T1.3 владеет обоими read-запросами и чтением current-round participants.

**22. Human question нельзя готовить до доменного решения.** Если
`finalize_fix_check()` принимал заранее построенный envelope, вызывающий должен
был угадать `dispute`/`cap_exhausted_same`/`cap_exhausted_new`, то есть повторить
`decide_after_check()` снаружи. Тест с заранее известным cap это маскировал, но
production-вызов корректно построить было нельзя.

T1.7b владеет чистым синхронным `ReviewDecisionGateFormatter`-портом и вызывает
его только после единственного `AskHuman` внутри writer-транзакции — передавая
не сам `AskHuman`, а обёртку `AfterCheckGate` из закрытого union
`ReviewDecisionGateRequest` (два других варианта — пути Q49). T1.16 реализует
production-форматирование и владеет общим union по всем одиннадцати
детерминированным причинам Q51.

Formatter получает готовое решение и контекст, не читает БД, не делает I/O и не
выбирает reason. Возвращает он **только presentation** — текст, формулировки
вариантов при канонических ключах и транспортно-нейтральное тело сообщения.
`reason`, scope, `snapshot_json`, строки `human_question_observation` и
`notification_outbox.transport` пишутся из самого запроса и конфигурации: в
P1-A транспорт всегда `cli`, потому что отправителя `telegram` ещё нет, а
ветка к моменту вопроса уже `blocked` — durable вопрос без доступного
отправителя означал бы застрявший прогон.

Поэтому проверка перед атомарной записью question/outbox/blocker/state/event
сузилась до пригодности presentation — непустой текст и ровно канонический
набор ключей ответа: разойтись с решением по причине, scope, snapshot или
маршруту формулировщик уже не может, он их не производит.

**23. Линия — это слот кворума, а исполнитель у слота сменный.** §7.1.1
`architecture.md` разрешает человеку заменить исчерпавшую бюджет линию другим
профилем, не понижая кворум, но модель данных держала профиль прямо в
`review_lane` под `UNIQUE (campaign_id, lane_index)`. Заменить было нечем:
`UPDATE` стёр бы исполнителя, чьи попытки и наблюдения уже в аудите, а вставка
новой строки либо конфликтовала по индексу, либо увеличивала roster и ломала
правило «минимальный `lane_index` = владелец».

Введено разделение (§5.2): `review_lane` — неизменяемый слот,
`lane_assignment` — append-only поколения исполнителей, активное определяется
отсутствием преемника.
Владение (`owner_lane_id`, `review_observation.lane_id`,
`finding.first_owner_lane_id`) осталось на слоте, исполнение
(`step_attempt.lane_assignment_id`) — на поколении, слот продублирован в попытке
составным FK. Отсюда же следствия, которых в решении не было: `step_attempt`
получил `round_id` и `campaign_id` (иначе участие линий в круге доказать нечем —
на стадии живут несколько кампаний, и независимые FK пропускают попытку с кругом
одной кампании и слотом другой), а деградация кворума стала durable-строкой
`lane_waiver(campaign_id, round_no, lane_id, human_answer_id)` вместо факта,
упомянутого только в тексте вопроса человеку.

Три границы этого уточнения названы явно, потому что каждая касается уже
принятых правил. **Первая:** гейт участия линий относится только к `discovery` —
в `fix_check` отвечают владельцы открытых findings, а не весь кворум, и
требование «все слоты» там закрыло бы штатный круг. **Вторая:** кворум нельзя
понизить до нуля, хотя формально waiver можно выписать каждой линии; для отказа
от проверки есть остановка ветки. **Третья:** замена исполнителя разрешена
только до `discovery_completed` — смена владельца в `fix_check` отдала бы
решения по findings свежему агенту, а §6.3 `decision.md` обосновывает владение
именно памятью прежнего. Это контракт разрешения findings, поэтому вынесено
вопросом Q57, а не решено проектированием.

**24. Машина состояний кампании и две её операции.** §6.6 `decision.md`
перечисляет переходы, но открытие кампании и завершение слепой фазы в список не
попали, а сама допустимость переходов держалась только тем, что весь код ходит
через один домен. Ревью P1-A (C-01a, C-01c) показало цену: у перехода
`discovery → reconciliation` не было исполняющей операции, и прямой `UPDATE` в
обход домена делал состояние `reconciliation` недостижимым, а recovery терял
разницу между «ждём вторую линию» и «ждём reconciliation».

Добавлены (§5.2 и §11): справочник `campaign_transition` с триггером — база
отвергает `discovery → fix_cycle` и любой исход из терминального состояния;
триггер начального состояния — кампания рождается только в `discovery`; две
именованные транзакции — «Кампания открыта» и «Слепая фаза завершена», причём
вторая обязана быть закоммичена **до** spawn reconciler.

Отдельно понадобился **заявленный размер кворума**: пока его нет в базе,
незавершённость открытия недоказуема. Кампания, настроенная на две линии и
оборвавшаяся после первой, выглядит полностью открытой — и гейт участия линий
принимает одно мнение за полный кворум, потому что ходит по существующим слотам.
Поэтому `review_campaign.expected_lane_count` снимается тем же снимком, что
порог и версия политики, плотность индексов держит триггер
`trg_lane_index_bounds`, а recovery audit и гейт участия сверяют число слотов с
заявленным.

Снимок при этом обязан быть неизменяемым, иначе он не снимок: `UPDATE
review_campaign SET expected_lane_count = 1` подгоняет заявленный кворум под
фактический roster, и оба гейта полноты замолкают. Поэтому identity и снимок
кампании защищены триггером `trg_campaign_snapshot_immutable`; меняться могут
только состояние и поля закрытия.

Здесь же зафиксирована граница: `UNIQUE (stage_id, ordinal)` даёт **обнаружение
дубля**, а не идемпотентность. Повторный вызов после потерянного ответа получит
ошибку уникальности, поэтому операция открытия обязана прочитать существующую
кампанию, сверить её aggregate и вернуть — либо поднять invariant error при
расхождении.

**25. `reviewer_exposure` — членство, а не журнал.** Прежняя формулировка
требовала строку на каждую передачу входа и одновременно держала
`UNIQUE (subject_id, revision, provider, model, campaign_id)`. Вместе они
ломаются на двух штатных путях (C-04): reconciler по умолчанию наследует
профиль линии с наименьшим индексом, а retry после `contract_error` повторяет
ту же пару. Решение Q50 A: строка означает допуск пары к версии предмета в
кампании, первая передача записана в `first_attempt_id` (переименовано из
`attempt_id`), журнал попыток остаётся в `step_attempt`.

Составных FK в итоге шесть, и последние два важнее остальных. Первые четыре
связывают строку допуска с предметом кампании, попыткой той же кампании и
прогона и — главное — с **фактически переданной ревизией**: прежний ключ на
`review_subject(id, revision)` разрешал только исходную ревизию предмета и тем
самым запрещал штатный `fix_check`, где линия проверяет `output_sha` правки
автора. Но они не мешали записать допуск на пару
`anthropic/opus`, ссылаясь на попытку профиля, который резолвится в
`openai/gpt`, — а свежесть считается именно по паре, и такая строка сделала бы
одну модель ложно свежей, а другую ложно использованной. Поэтому в exposure
появился `profile_id` первой попытки, а пара берётся из
`run_profile_resolution` через составной ключ: цепочка `допуск → профиль
попытки → резолвинг прогона` замкнута базой. С другого конца та же цепочка
замкнута ключом `(lane_assignment_id, lane_id, profile_id)` в `step_attempt`:
исполнить чужое назначение своим профилем нельзя, а `provider`/`model` из
`lane_assignment` убраны — пара выводится из `run_profile_resolution`, второй
копии правды нет. Операции разделены: внешняя
`create_attempt_and_reserve_exposure()` и repository-метод
`reserve_reviewer_exposure(attempt_id)`, который выводит остальное сам, — и
передать несогласованные поля больше нельзя. Строки самого допуска стали
append-only (два триггера): удалить или поправить их значило бы вернуть уже
видевшую модель в пул свежих. Безусловный `INSERT OR IGNORE` запрещён; при
конфликте существующая строка сверяется, а допустимы ровно два продолжения:
попытка **действующего** назначения того же слота в той же кампании — сюда
попадают и retry в круге, и следующий круг по той же ревизии, когда автор ничего
не исправил, — и reconciler той же кампании и ревизии. Требовать совпадения
`round_id` нельзя: это запрещало бы штатный `fix_check` и противоречило Q50.

**26. Scope держался на независимых FK, а значит не держался.** Каждая ссылка
проверялась по отдельности, поэтому `finding_round` могла указывать на
`round_no`, существующий только в другой кампании, наблюдение — на круг одной
кампании и линию другой, стадия — на ветку чужого прогона. Хуже всего первое:
такая строка **выпадала** из strict-issued гейта, построенного на INNER JOIN, —
то есть ошибка, ради поимки которой гейт написан, делала его слепым.

Введены составные ключи по всему review-домену (§5.3.1) и по паре
`branch → stage_execution → step_attempt`; общий приём — денормализованная
scope-колонка плюс `UNIQUE (id, <scope>)` в родителе. Четыре связи осознанно
оставлены коду, и у каждой назван запрос или предусловие: наблюдение ↔ личность
разных прогонов, авторская попытка в `finding_round` через стадию,
`logical_path` артефактной ревизии, цикл в дереве предметов. Разница
зафиксирована прямо: закрытое ключом не нуждается в проверке, оставленное коду
обязано иметь названный запрос.

**27. Формулировщик вопроса не выбирает маршрут и не сочиняет команды.** Когда
envelope свели к presentation, в нём по инерции остались `transport` и разметка.
Это разные вещи: текст — представление, транспорт — маршрутизация внешнего
эффекта. Formatter, законно вернувший `telegram` в P1-A, создавал бы durable
вопрос без существующего отправителя, причём ветка к этому моменту уже
`blocked`, — то есть застрявший прогон вместо остановки на человеке.

`notification_outbox.transport` и `target_ref` пишет транзакция: в P1-A всегда
`cli`, позже маршрут выбирает конфигурация T1.15/T1.16. `reply_markup` тоже не
принадлежит formatter'у — форма разметки зависит от транспорта, поэтому поздние
отправители строят её при отправке из durable `human_question.options_json`, а
в P1-A колонка остаётся `NULL`.

Тем же рассуждением закрыты и ключи вариантов. Q51 называет их командами
перехода состояния, значит сочинять их некому, кроме домена: канонический набор
для `dispute`/`cap_exhausted_*` — ровно `A`/`B`/`C` из таблицы §10
`agent-contracts.md` — несёт сам запрос, за formatter'ом остаются формулировки, а
несовпадение набора отвергается до записи. У двух причин Q49 фиксированного
набора нет: там ответ классифицирует наблюдения, и `options_json = NULL`
законен.

**28. DELETE-trigger без `recursive_triggers` не защищает от REPLACE.** SQLite
при `INSERT OR REPLACE` неявно удаляет конфликтующую строку, но DELETE-trigger
на этом пути при значении PRAGMA по умолчанию не вызывается. Поэтому
append-only таблица формально запрещала прямой DELETE и одновременно позволяла
заменить весь audit-факт: terminal attempt снова становилась active, observation
получала другой payload, а `UNIQUE(question_id)` у принятого ответа обходился
удалением старой строки.

`recursive_triggers=ON` стал шестым обязательным connection PRAGMA с readback в
T1.2. Рядом закрыт соседний audit-шов: scope/reason/presentation/snapshot
`human_question` неизменяемы, `human_answer` append-only, а `finding_round`
сохраняет input/author-поля и получает reviewer decision ровно один раз. Это не
новая бизнес-семантика, а доведение уже принятых правил «решение по тому, что
показали» и «один принятый ответ» до физической гарантии.

Доказательство тоже не opt-in: `mutation-check.py --coverage` требует отдельный
свидетель каждой ветви многосоставного `WHEN` и каждой колонки многоколоночного
`UPDATE OF`, а `--schema-sync` сверяет со стабами полный набор и тела
нормативных trigger'ов и table-level `CHECK`/FK/`UNIQUE`. Для каждой полностью
append-only таблицы §1.5 обязательны оба пути — прямой DELETE и конфликтующий
REPLACE.

**29. UNIQUE ограничивает новые строки, но не делает принятое решение
неизменяемым.** C-23 запретил второй `severity_override` одной пары
finding/event, однако прямой DELETE и конфликтующий `INSERT OR REPLACE`
удаляли прежнюю строку. Это меняло `finding_last_override`, а следом и порог
эскалации, задним числом. Поскольку строка ссылается на `human_answer_id` и
фиксирует решение человека, она относится к тому же audit-классу, что
`human_answer`: два append-only trigger'а плюс обязательный
`recursive_triggers=ON`. Наличие user-facing producer'а C-22 на эту физическую
гарантию не влияет.

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
| `severity_override` не был уникален по `(finding_id, event_id)` | Два override одного finding в одном событии оба становились последними и раздваивали `finding_severity` | Составной `UNIQUE`; разные findings одного события и разные события одного finding разрешены |
| `severity_override` после добавления `UNIQUE` оставался удаляемым | DELETE либо REPLACE обходил гарантию единственного принятого решения и менял последний override задним числом | Append-only UPDATE/DELETE trigger'ы; отдельно проверены прямой DELETE, REPLACE и сохранность строки |

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
