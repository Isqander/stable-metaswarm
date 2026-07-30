# Ресёрчи против кода: верификационный проход

Пять документов в `docs/researches/` расходятся между собой, а часть утверждений
не подтверждается ничем, кроме пересказа README. Ниже — проверка по исходникам тех
claim'ов, от которых **зависит выбор архитектуры**.

Клоны: `refs/<dir>` (см. `refs/manifest.tsv`), пинованные SHA — `refs/pinned.tsv`.
Дата прохода: 2026‑07‑30.

Легенда: ✅ подтверждено кодом · ⚠️ подтверждено частично · ❌ опровергнуто · ❔ не найдено

## Сводка

| # | Claim | Вердикт |
|---|---|---|
| C1 | Gas City: `mol-review-quorum` + `max_attempts = 3` | ✅ |
| C2 | Beads: схема под attempts/revisions/human questions | ✅ богаче, чем описано — см. C7 |
| C3 | VirtusLab Orca: `reviewAndFixLoop` как first-class primitive | ✅ с важной оговоркой |
| C4 | CAO: Telegram-плагины и HITL «из коробки» | ❌ qwen.md неверен, glm.md верен |
| C5 | Microsoft Conductor: context modes + `human_gate` | ✅ зрелее, чем оценил sol.md |
| C6 | Metaswarm: cap‑3, human escalation, no‑anchoring | ✅ но всё это — проза в Markdown |
| **C7** | **Beads: `bd gate --type human` + `bd human` — HITL как примитив** | **новая находка, ни один ресёрч не упоминает** |

---

## C1. Gas City: review-quorum и лимит циклов ✅

**Утверждали** (sol.md §3, fable.md §Слой 1): в core pack есть формула
`mol-review-quorum` с двумя reviewer lanes, retry budget и synthesis step; лимит
циклов декларативен — `max_attempts = 3`.

**Код подтверждает:**

- `refs/gascity/internal/bootstrap/packs/core/formulas/mol-review-quorum.toml` — существует,
  `max_attempts = 3` встречается в нём **трижды** (строки 102, 148, 188).
- Есть вторая релевантная формула: `mol-scoped-work.toml`, тоже с `max_attempts = 3`
  в четырёх шагах.
- Пакет `internal/convergence/` реален (`artifact.go`, `capture.go`, `condition.go`, `acl.go`,
  `childstats.go`).
- `internal/beadmeta/keys.go:125` — `gc.max_attempts` как ключ метаданных бида,
  т. е. счётчик попыток живёт **в beads**, а не в контексте LLM. Это и есть
  детерминированный state, которого не хватает metaswarm (см. C6).

**Уточнение по Telegram** (sol.md: «transport ещё надо прикрутить»): в `internal/extmsg/`
из конкретных транспортов реализован **только `http_adapter.go`** плюс
`adapter_registry.go` с `Register(key, adapter)`. Telegram упоминается лишь как
пример в `engdocs/design/external-messaging-fabric.md:68` и в `internal/config/extmsg.go:21`.
Вывод мягче, чем у sol.md: раз есть generic HTTP-адаптер, Telegram Bot API — это
**конфиг, а не код**. Durable-часть (binding_service, delivery_service, inbound/outbound,
binding_reaper, live_session, transcript_service) реализована.

---

## C2. Beads: схема данных ✅ (существенно богаче описанного)

Четыре типа зависимостей подтверждены (`blocks`, `related`, `parent-child`,
`discovered-from` — `internal/storage/dolt/`, `internal/linear/mapping.go`).

Но `Issue` (`internal/types/types.go:19`) содержит **много больше**, чем упоминает
любой из пяти ресёрчей:

- **Лизинг с TTL и heartbeat**: `LeaseExpiresAt`, `HeartbeatAt`, `LeaseGrantedNode` —
  защита от зависших агентов на уровне store.
- **Оптимистичная конкурентность**: `RowVersion` (equality-only токен) — детект
  конкурентной мутации.
- **`DeferUntil`** — «скрыть из `bd ready` до момента T». Прямо применимо к
  приостановке ветки.
- **`Metadata json.RawMessage`** — валидируемый extension point. Именно сюда ложатся
  `Artifact` / `ReviewOutcome` / `FindingDisposition` из рекомендаций sol.md §«что
  конкретно надо добавить», без форка beads.
- **Типы issue**: помимо bug/feature/task/epic — `decision`, `message`, `molecule`,
  `gate`, `spike`, `story`, `milestone`, `event`.
- **`SourceFormula` / `SourceLocation`** — beads нативно знает про формулы Gas City
  («Formula name where step was defined», «Path: steps[0]»). Связь beads↔gascity
  глубже, чем «интегрирован»: это одна система.
- **Storage classes / wisps** — versioned vs ephemeral контракт репликации,
  `NoHistory`, TTL-компакция.

Итог: рекомендация sol.md про immutable attempts (`design:A1 → review-B:A1 →
revision:A2`) реализуема на существующей схеме без расширения ядра.

**Опровергнуто** (qwen.md): «жёсткая модель жизненного цикла `create → claim → close`».
Модель заметно богаче — см. C7.

---

## C3. VirtusLab Orca: `reviewAndFixLoop` ✅ — но исчерпание лимита ≠ эскалация

Всё, что перечислял sol.md, существует как отдельные файлы в
`refs/orca-virtuslab/flow/src/main/scala/orca/review/`: `ReviewIssue.scala`,
`ReviewResult.scala`, `FixOutcome.scala`, `IgnoredIssue.scala`, `ReviewLoop.scala`,
`Severity.scala`, `ConfidenceGate.scala`, `Reviewers.scala`, `ReviewerSelector.scala`.
Плюс тесты (`ReviewAndFixTest`, `ReviewFixFlowTest`, `FixLoopTest`) и готовые
flow-скрипты (`flows/implement.sc`, `flows/review.sc`, `flows/issue-pr.sc`).

Контракт `FixOutcome` даже строже, чем описан: «промпт требует, чтобы каждый входящий
issue попал ровно в один список; заголовок, не попавший ни в один, всё ещё открыт»,
и при пустом `fixed` (условие останова) issue записывается как ignored с причиной
`"fixer reported no fixes"`, а не молча теряется.

**Оговорка про fork подтверждена** дословно (`tools/src/main/scala/orca/Par.scala:11`):
`stage(...)`, `agent.session(...)` и `session.run(...)` бросают `OrcaFlowException`
при вызове из fork. ADR 0018 подтверждает намеренность.

**Чего ресёрчи не заметили — и это важно.** `stopPolicy` в `ReviewLoop.scala:49`:
при `iteration >= maxIterations` открытые issue **сворачиваются в `ignored`** с
причиной `"max iterations (N) reached"`, после чего flow **продолжается**. Это не
human escalation — это «сдались и поехали дальше». Требование №5 (после трёх циклов
эскалация к человеку) на Orca придётся дописывать, а не конфигурировать.
Дефолт `maxIterations = 10`, не 3, но это обычный параметр.

Ещё деталь: `maxIterations` считает попытки **исправления**, поэтому цикл делает до
`maxIterations + 1` оценок. При переносе кап‑3 это off‑by‑one, на который легко налететь.

---

## C4. CAO: Telegram и HITL ❌ — glm.md прав, qwen.md ошибается

Прямое противоречие между ресёрчами разрешено в пользу glm.md.

- **Telegram в коде отсутствует.** Единственное упоминание во всём репозитории —
  `README.zh-CN.md:44`, где Telegram назван *возможным* получателем outbound-событий.
  Ни в `src/cli_agent_orchestrator/plugins/`, ни где-либо ещё в Python его нет.
- **Плагины — observer-only.** Формулировка README (zh, стр. 406): plugins реагируют
  на server-side события, это «**outbound** расширение». Такой плагин не может
  приостановить ветку и дождаться ответа — он только уведомляет. Тезис qwen.md
  «HITL встроенная, продвинутая… потребуется только конфигурация» не подтверждается.
- Реализация плагинов: `base.py`, `builtin/`, `events.py`, `registry.py` — каркас, не транспорты.
- **Провайдеров 10, но Gemini среди них нет**: `antigravity_cli`, `claude_code`,
  `codex`, `copilot_cli`, `cursor_cli`, `hermes`, `kimi_cli`, `kiro_cli`,
  `opencode_cli` + `mock_cli`. glm.md перечислил их точно; **qwen.md ошибочно
  называет Gemini CLI** среди поддерживаемых CAO.

Практический вывод: CAO ценен как каталог адаптеров запуска CLI (10 провайдеров с
нативной аутентификацией, tmux-изоляция), а не как HITL-слой.

---

## C5. Microsoft Conductor: ✅ зрелее, чем оценил sol.md

`refs/conductor-ms/AGENTS.md` подтверждает всё, что заявлял glm.md:

- **Три режима контекста** реальны (`AGENTS.md:100,153`): `context.py` →
  `WorkflowContext` с `accumulate` / `last_only` / `explicit`. `explicit` («только
  объявленные inputs») — это буквально требование №1 про cut-off файл.
- **`human_gate`** присутствует в реализации и документации (`docs/cli-reference.md`,
  `docs/configuration.md`, `docs/parallel-execution.md`).

Но sol.md §5 недооценил durability. В коде есть:

- **Периодические чекпоинты** (`runtime.checkpoint`): `every_agent` / `every_seconds` /
  `keep_last`, ротация, `conductor checkpoint list`, событие `checkpoint_save_failed`
  при сбое сохранения, resume продолжает вперёд.
- **`terminate` шаги** с `status: success|failed`, `output_template`, понижение
  до `SubworkflowTerminatedError` на границе sub-workflow.
- **Sub-workflows** с отслеживанием глубины (`_subworkflow_depth`).
- Retry-политика поверх `claude-agent-sdk` (который сам API-ошибки не ретраит).

Оценка sol.md «durability активного сложного flow слабее Gas City» скорее всего
всё ещё верна (нет beads, нет worktree lifecycle), но «слабее» — не «отсутствует».
Как **язык описания** пайплайна Conductor стоит скопировать.

---

## C6. Metaswarm: ✅ всё есть — и всё это проза в Markdown

Подтверждено дословно:

- **5 параллельных ревьюеров** (`skills/design-review-gate/SKILL.md`): PM, Architect,
  Designer, Security, CTO; «All five must approve». README добавляет: «3-iteration
  cap before human escalation».
- **Кап 3 и эскалация**: `agents/issue-orchestrator.md:508` — «ANY REJECTED? → Iterate
  (max 3x) → Escalate to human»; `:606` — «After 3 failures, escalate to human with
  full failure history»; `guides/agent-coordination.md:223` — таблица «3 retries per
  gate, then escalate to human»; `skills/orchestrated-execution/SKILL.md:389,397`.
- **Fresh reviewer против anchoring bias** — самая проработанная часть:
  `guides/agent-coordination.md:190` («next reviewer MUST be a completely new `Task()`
  instance with zero memory of prior reviews»), `:209` («Never a teammate. Never
  resumed. Never given context about what previous reviewers found»),
  `agents/code-review-agent.md:383` («Fresh Reviewer Rule»),
  `rubrics/adversarial-review-rubric.md:183` — ревьюеру предписано **самому
  эскалировать**, если он обнаружил у себя знание предыдущего ревью.

**Ключевое ограничение подтверждено и оно жёстче, чем у sol.md.** Состав репозитория:
158 `.md`, 21 `.sh`, 13 `.toml`, 4 `.ts`. Внутри `skills/design-review-gate/SKILL.md`
параллельный запуск ревьюеров записан как **TypeScript-псевдокод
`await Promise.all([Task({...}), ...])` внутри Markdown-файла** — это инструкция для
LLM, а не исполняемый код. Счётчик «Attempt 2 of 3» — тоже строка в промпте
(`skills/orchestrated-execution/SKILL.md:399,412`). То есть state machine целиком
находится в контексте главного LLM-оркестратора Claude Code.

- **Адаптеры внешних вендоров: ровно два** — `skills/external-tools/adapters/codex.sh`
  и `gemini.sh` (+ `_common.sh`). Не «мульти-вендорность», а Claude Code как рантайм
  плюс два шелл-вызова.

Вывод для этого проекта: из metaswarm переносятся **промпты, рубрики, роли, фазовая
структура и review-JSON-контракты** — это его настоящая ценность, и она значительна
(`rubrics/`, `agents/`, `guides/`). Заменяться должен именно исполнитель state machine.
Имя `stable-metaswarm` описывает задачу верно.

---

## C7. Новая находка: beads уже содержит HITL-примитив

**Ни один из пяти ресёрчей этого не упоминает, а qwen.md утверждает обратное**
(«Beads сам по себе не реализует механизм человека в цикле в автоматическом режиме…
человек должен вручную заметить это»).

В `Issue` есть группа **Gate Fields (async coordination primitives)**:

```go
AwaitType string        // Условие: gh:run, gh:pr, timer, human, mail
AwaitID   string        // Идентификатор условия
Timeout   time.Duration // Максимальное ожидание до эскалации
Waiters   []string      // Mail-адреса для уведомления при открытии гейта
```

И полноценный CLI поверх них:

- `bd gate create` — «Create a gate that **blocks an issue**», флаг
  `--type` со значениями `human, timer, gh:run, gh:pr`, **дефолт `human`**
  (`cmd/bd/gate.go:1042`).
- `bd gate add-waiter <gate-id> <waiter>` — подписка на открытие гейта.
- `bd gate check` — «Evaluate gates and close resolved ones» (демонизируемый tick).
- `bd gate resolve <gate-id>` — ручное закрытие.
- `bd human list | respond <id> | dismiss <id> | stats` — рабочий процесс по бидам
  с лейблом `human`; `human respond` добавляет комментарий и закрывает бид
  (`cmd/bd/human.go`).

Плюс `IssueType = "gate"` как отдельный тип и `DeferUntil` для отложенного возврата
в `bd ready`.

**Что это меняет.** Требование №4 (открытый вопрос → уведомление → приостановка
именно этой ветки, независимые ветки продолжают работу) — это `bd gate create
--type human` на блокирующем биде, `Waiters` как канал уведомления, `Timeout` как
эскалация по времени, `bd gate check` как tick. Durable по построению, потому что
состояние в git/Dolt. Отдельный durable-бэкбон (Temporal/DBOS/ControlFlow), который
рекомендуют fable.md и gemini.md **именно под это требование**, оказывается
избыточным: beads уже владеет и графом, и ожиданиями, и уведомлениями.

Проверить на следующем шаге: как `bd gate check` узнаёт о разрешении `human`-гейта
из внешнего канала (webhook / polling / только `bd human respond`), и что именно
происходит по `Timeout` — эскалация или просто закрытие.
