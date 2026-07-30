# Вывод после более широкого поиска

После расширенного поиска я бы **пересмотрел предыдущий рейтинг**: Stoneforge уже не выглядит лучшим кандидатом. Нашлись решения, которые значительно ближе к вашему сценарию.

Причём лидер зависит от того, что именно считать главным:

1. **Почти готовая методология вашего процесса — metaswarm.**
2. **Проще всего запрограммировать точный последовательный flow — VirtusLab Orca** — это другой проект, не StablyAI Orca IDE.
3. **Лучшее долговечное основание с нативным Beads — Gas City.**
4. **Самый близкий готовый продукт с UI, gate и worktree — Forge**, но только если отказаться от Beads как source of truth.

Полностью готового зрелого решения, в котором одновременно есть:

* нативный Beads;
* управляемый fresh context;
* разные CLI-вендоры;
* долговечные внешние human questions;
* произвольные циклы `A → B → A → B → C`;
* ревью документов, планов и кода;
* параллельные финальные review campaigns;
* восстановление после падения,

я не нашёл.

Но вариант **«совсем немного допилить» существует**. Основных пути два:

* **VirtusLab Orca + небольшой Beads/human-gate слой** — быстрее получить точный flow.
* **Gas City + workflow/rubrics из metaswarm** — правильнее для долгой автономной эксплуатации.

---

# 1. Metaswarm: ваш процесс уже почти описан

Из всех найденных проектов **metaswarm ближе всего к вашему сценарию на уровне самой методологии**.

У него уже заявлен и расписан такой SDLC:

```text
Research
→ Plan
→ Design Review Gate
→ Work Unit Decomposition
→ Orchestrated Execution
→ Final Review
→ PR Creation
→ PR Shepherd
→ Closure & Learning
```

Для каждого work unit используется цикл:

```text
IMPLEMENT
→ VALIDATE
→ ADVERSARIAL REVIEW
→ COMMIT
```

При провале ревью управление возвращается исполнителю, затем запускается **свежий reviewer**, максимум три цикла, после чего происходит human escalation. Есть пять параллельных ревьюеров техдизайна, отдельный plan review gate, Beads как source of truth, Codex/Gemini/Claude, cross-model review, Playwright visual review и автоматизация CI/PR. ([GitHub][1])

## Насколько он совпадает с вашим flow

| Ваш этап                       | Metaswarm           |
| ------------------------------ | ------------------- |
| Создание техдизайна            | Есть                |
| Параллельное ревью техдизайна  | Есть, 5 ролей       |
| Несколько циклов до approval   | Есть, до 3          |
| Human escalation               | Есть                |
| Task breakdown                 | Есть                |
| Граф work units                | Нативный Beads      |
| Plan review                    | Есть отдельный gate |
| Реализация каждого work unit   | Есть                |
| Независимая валидация          | Есть                |
| Fresh adversarial reviewer     | Явно требуется      |
| Cross-model review             | Есть                |
| Playwright/визуальная проверка | Есть                |
| Final comprehensive review     | Есть                |
| PR/CI feedback loop            | Есть                |

Design Review Gate выдаёт структурированный результат:

```text
APPROVED | NEEDS_REVISION
blockers
suggestions
questions
```

Пять агентов запускаются параллельно. После трёх неудачных раундов человеку предлагается override, defer, continue revision или cancel.

Реализация work unit тоже почти дословно соответствует вашему пункту 5:

```text
Автор реализовал
→ оркестратор самостоятельно прогнал проверки
→ свежий агент проверил
→ при FAIL автор исправляет
→ снова проверки
→ новый свежий reviewer
```

Отдельно зафиксировано, что новый reviewer **не получает findings предыдущего reviewer**, чтобы избежать anchoring bias.

Cross-model слой не только описан в Markdown: в репозитории присутствуют адаптеры Codex и Gemini, общий invocation wrapper, sandbox/worktree conventions и унифицированные результаты вызовов.

## Что не совпадает

Главная проблема: **metaswarm — скорее набор skills, prompts, rubrics и shell-адаптеров, чем надёжный workflow server**.

Управляющая логика по большей части исполняется главным LLM-оркестратором. Это означает:

* нет отдельного детерминированного daemon, владеющего state machine;
* long-running pause на несколько часов или дней не оформлен как полноценная долговечная сущность;
* human escalation обычно означает интерактивный вопрос текущему пользователю;
* Telegram/email reply не привязан к конкретному workflow attempt;
* после падения Beads сохраняет work items, но точная позиция во вложенном цикле может зависеть от способности нового координатора восстановить её;
* в design-review skill исправлять документ в следующих раундах предлагается человеку, тогда как вам обычно нужен возврат к агенту A.

Последний пункт исправляется почти одной заменой orchestration policy:

```text
не «human revises design»
а «dispatch revision to original author session»
```

## Итог по metaswarm

**Как немедленный эксперимент — это самый готовый вариант.**

Поставить, создать одну реальную фичу и пройти:

```text
design → review → decomposition → implementation → final review
```

можно почти без написания инфраструктуры.

Но превращать один metaswarm в unattended production orchestrator означает постепенно написать вокруг него durable runtime. Тогда он перестаёт быть вариантом «почти ничего не допиливать».

---

# 2. VirtusLab Orca: самый простой путь к точному программируемому flow

Это **не** уже разобранный `stablyai/orca`. Новый кандидат — `VirtusLab/orca`, библиотека для программируемых AI development flows на Scala.

Для вас как JVM-разработчика это особенно интересный вариант: flow пишется как типизированный Scala-скрипт и запускается через `scala-cli`.

Минимальный пример проекта уже делает:

```text
Plan
→ задачи плана по одной
→ Claude реализует
→ Codex проверяет
→ исправления возвращаются в ту же Claude-session
→ повторять до чистого ревью
```

Это не пример из пожеланий — такой flow уже лежит в репозитории.

## Особенно сильная сторона — ваш цикл A/B

Встроенный `reviewAndFixLoop`:

* запускает несколько reviewers;
* агрегирует findings;
* передаёт их обратно в исходную `coderSession`;
* принимает от автора как исправления, так и мотивированные `won't-fix`;
* повторяет проверку;
* останавливается при чистом результате, полном наборе `won't-fix` или исчерпании лимита итераций.

Для результатов уже существуют типы:

* `ReviewIssue`;
* `ReviewResult`;
* `FixOutcome(fixed, ignored)`;
* `IgnoredIssue(title, reason)`.

То есть самая сложная специфическая часть вашего сценария — не просто agent messaging, а именно:

```text
A сделал
B нашёл findings
A исправил или обосновал отказ
B проверил повторно
```

— здесь уже является first-class библиотечным primitive.

## Контекст тоже устроен почти как вам нужно

Есть три разных режима:

| Вызов                       | Контекст                       | Переживает рестарт |
| --------------------------- | ------------------------------ | -----------------: |
| `agent.run(...)`            | Fresh one-shot                 |                Нет |
| `agent.chat()`              | Временная многоходовая сессия  |                Нет |
| `agent.session(name, seed)` | Долговечная именованная сессия |                 Да |

Поэтому можно явно выразить:

```text
author:
  durable session, получает design/task plan и findings

reviewer B:
  fresh one-shot, получает только subject + rubric

reviewer C:
  другой fresh one-shot, получает только итоговый subject

continuation:
  durable session, seed = cut-off summary
```

Показательный пример: planner session намеренно отбрасывается через `.value`, после чего создаётся свежая implementer session, получающая только `plan.brief`.

## Resumability

Каждый `stage`:

* имеет структурированный результат;
* коммитит код и progress entry атомарно;
* при повторном запуске пропускается;
* после падения flow продолжается с первого незавершённого stage;
* незакоммиченные изменения провалившегося stage сбрасываются.

Это хорошая и довольно простая модель durability: source of truth — Git commits плюс `.orca/progress-*.json`.

## Разные CLI

Поддерживаются единым API:

* Claude Code;
* Codex;
* OpenCode;
* Pi;
* Gemini.

Можно настроить разные роли глобально:

```properties
planningAgent = claude:opus
codingAgent = codex:gpt-5
reviewAgent = opencode:anthropic/claude-sonnet
```

или закреплять конкретный harness непосредственно в flow.

## Проверки

Есть встроенная работа с:

* format/lint/test командами;
* Git и worktree;
* GitHub PR;
* GitHub checks;
* CI waiting;
* structured model outputs;
* read-only reviewers.

Команды стека автоматически определяются и сохраняются в `.orca/settings.properties`. Полноценную API/CLI/browser-проверку можно добавить обычным stage.

## Главные пробелы

### Нет Beads

Встроенный `Plan` содержит линейный список задач, но не является распределённым task graph.

Добавить Beads технически нетрудно:

```scala
val ready = bd.ready()
val task = bd.show(id)
bd.claim(id)
bd.close(id)
```

Это может быть маленькая Scala-обёртка над `bd --json`. Но полноценный scheduler, который постоянно выбирает ready beads и запускает отдельные flows, придётся добавить.

### Human question пока интерактивный

Есть `ask_user`, но он предназначен для текущего интерактивного запуска. Более того, interactive session по дизайну не является restart-durable.

Для вашего требования нужен новый primitive:

```scala
awaitExternalAnswer(
  questionId,
  question,
  notify = telegramWebhook
)
```

Он должен:

1. записать вопрос в Beads или отдельную SQLite-таблицу;
2. отправить notification;
3. завершить процесс со статусом `PAUSED`, а не `FAILED`;
4. дождаться внешнего ответа;
5. повторно запустить flow;
6. вернуть ответ как результат незавершённого stage.

### Ограниченный долговечный параллелизм

`Par.mapUnordered` умеет параллельные fresh agent turns, что хорошо для параллельных reviewers. Но `stage`, создание durable session и `session.run` запрещены внутри fork. Один flow в основном принадлежит одной feature branch.

Следовательно:

* параллельные design reviewers — хорошо;
* параллельные initial final reviews — хорошо;
* параллельные независимые implementation flows — нужен внешний Beads scheduler и отдельные Orca flows/worktree.

Поскольку вы сами отметили, что параллельная реализация не критична, это не фатальный недостаток.

## Итог по VirtusLab Orca

**Это самый простой кодовый фундамент для буквального воспроизведения вашего flow**, особенно если первую версию выполнять преимущественно последовательно.

Оценка необходимых добавлений:

* Beads wrapper;
* `ExternalQuestion`;
* pause/resume runner;
* artifact/context manifest;
* несколько custom reviewer sets.

Глубокий fork Orca для этого не нужен: почти всё можно разместить в собственной Scala-библиотеке и одном `.sc`-flow.

---

# 3. Gas City: лучший production-фундамент с нативным Beads

Gas City — это не готовая software-factory методология, а **SDK для её создания**.

Из коробки уже есть:

* Beads как основное хранилище work state;
* формулы, компилируемые в граф Beads;
* independently routable steps;
* fan-out, join, checks, retries и waits;
* разные agent/runtime providers;
* supervisor/controller;
* session recovery;
* durable mail;
* external messaging fabric.

Формула v2 превращается в такой runtime graph:

```text
workflow root Bead
├── agent work Beads
├── check control Beads
├── retry control Beads
├── fan-out/drain Beads
├── gate Beads
└── workflow finalizer
```

Control Beads исполняются самим оркестратором, а обычные work Beads маршрутизируются разным агентам, пулам и провайдерам. Это гораздо надёжнее, чем поручать главному LLM помнить, на какой итерации он находится.

## Beads и распараллеливание

Это единственный из сильных кандидатов, где Beads — не внешний adapter и не импортируемый task list, а центральная часть архитектуры.

Можно естественно выразить:

```text
design
  ↓
review-B-1
  ↓
revision-1
  ↓
review-B-2
  ↓
review-C
  ↓
task-breakdown
  ├── plan-T1
  ├── plan-T2
  └── plan-T3
       ↓
boundary-review
```

При этом ready work действительно виден scheduler и может запускаться независимо.

## Review quorum уже существует

В core pack есть формула `mol-review-quorum`:

* две независимые read-only reviewer lanes;
* отдельные provider, model и target для каждой;
* структурированный verdict;
* findings с severity, file, line/range и evidence;
* retry budget;
* synthesis step;
* durable JSON output.

Это почти готовая основа для вашего финального параллельного ревью.

Ограничение: текущий synthesis всё ещё делает агент, а не детерминированный Go-finalizer. Для вашего случая я бы оставил агенту дедупликацию findings, но разрешение state transitions делал кодом.

## Human questions

Gas City имеет два необходимых слоя.

### Durable waits

Сессии и work items могут ожидать Bead или gate, а supervisor занимается wake/restart.

### External messaging

External messaging fabric имеет статус Implemented и вводит:

* provider-neutral external conversations;
* durable conversation-to-session bindings;
* durable reply routes;
* transport adapters;
* поддержку архитектуры для Telegram, Slack, Discord и email.

При этом core намеренно не поставляет все transport adapters; реализованы общий слой и отдельные bridge/PoC-интеграции.

То есть Telegram-канал ещё надо прикрутить, но самая неприятная часть — correlation, durable binding и return route — уже спроектирована.

## Fresh/continuation handoff

`gc handoff`:

* пишет долговечное handoff mail;
* останавливает controller-managed session;
* supervisor поднимает новую session;
* новый процесс получает handoff context.

Это хороший continuation/cut-off режим.

Для настоящего fresh review нужна отдельная context policy, чтобы новая session получала не handoff mail, а только task/artifact manifest.

## Чего не хватает

Gas City пока не содержит именно вашей последовательности стадий:

```text
design
→ design review loops
→ task graph
→ per-task plans
→ boundary review
→ implementation loops
→ verification
→ final review campaigns
```

Её надо оформить как custom pack из формул, prompt templates и скриптов.

Но это существенно меньшая работа, чем добавлять в Stoneforge или StablyAI Orca:

* Beads;
* durable DAG;
* control nodes;
* wait lifecycle;
* supervisor;
* provider routing;
* external messaging fabric.

В Gas City всё это уже есть.

---

# 4. Forge: самый близкий готовый продукт, если Beads не обязателен

`ForgeAILab/forge` — локальный self-hosted control plane в одном бинарнике.

Он уже поддерживает:

* worktree на каждую задачу;
* Claude Code, Codex, Cursor, Gemini, OpenCode и generic shell;
* CI gate;
* reviewer role;
* human approval;
* audit log;
* REST, MCP, CLI и web UI;
* локальную SQLite.

Его workflow engine строится вокруг проектных state definitions, ролей, gate states, hooks и retry budgets. Gate может повторно отправлять задачу coder после rejection; human approval является отдельным переходом.

Это позволяет описать:

```text
designing
→ design_review_b
→ design_revision
→ design_review_c
→ task_breakdown
→ task_plan_review
→ implementation
→ code_review
→ verification
→ final_review
→ done
```

## Почему Forge выглядит проще

Вам не надо писать:

* server;
* UI;
* agent registration;
* worktree lifecycle;
* CI runner;
* review screen;
* audit log;
* task board.

Добавляется только custom workflow и несколько новых artifact/review conventions.

## Но есть две серьёзные оговорки

### Нет Beads

Forge имеет собственные Tasks/Subtasks/SQLite.

Поддерживать одновременно:

```text
Beads task state
+
Forge task state
```

не стоит. Получится два scheduler, две модели ownership и проблемы синхронизации.

Forge подходит, только если:

* заменить Beads внутренним task graph Forge;
* либо использовать Beads как одноразовый import/export, а не второй source of truth.

### Workflow engine ещё переходный

Forge находится в public beta `0.1.x`. В официальной архитектуре прямо указано, что data-driven `WorkflowEngine` пока является параллельным путём, а часть операций продолжает использовать legacy `TaskStatus` transitions. Для production-critical flow это существенный maturity risk.

Кроме того, recovery remote execution сейчас в некоторых случаях переводит работу в manual recovery, а не автоматически продолжает с последнего checkpoint.

---

# 5. Остальные сильные кандидаты

## Bernstein

Bernstein интересен как детерминированный Python engine:

* YAML DAG;
* `fresh_context`;
* retry loops;
* cross-model review;
* много CLI-adapters;
* worktree;
* durable ledger и replay;
* structured reviewer verdicts.

Но в текущем workflow runner прямо отсутствует human approval gate. Также разные возможности распределены между workflow manifests и plan YAML: в одном месте сильнее control flow, в другом — per-step provider routing. Нет Beads.

Это хороший запасной вариант, но VirtusLab Orca сейчас проще для вашего review-heavy процесса.

## Tutti

Tutti имеет:

* `tutti.toml`;
* разные CLI;
* worktree;
* DAG workflow steps;
* typed artifacts;
* human approval;
* review/CI/merge gates;
* durable run ledger.

Также у него описан настоящий PR feedback cycle:

```text
review
→ apply feedback
→ push
→ re-review
→ required checks
→ merge
```

Но сложные document-review loops и Beads integration придётся дописывать. Проект пока меньше и менее обкатан, чем лидеры.

## Microsoft Conductor

Conductor хорош как workflow DSL:

* YAML;
* parallel/fan-out;
* sub-workflows;
* conditional routing;
* loops;
* max iterations;
* human gates;
* web dashboard.

Однако он не является полноценным coding-agent execution plane:

* нет native Beads;
* нет системного worktree lifecycle;
* нет task/PR ownership модели;
* ограниченный набор provider backends;
* durability активного сложного flow слабее Gas City.

Его имеет смысл использовать только как embedded workflow engine внутри собственной системы.

## Stoneforge

Stoneforge по-прежнему хороший durable control plane:

* event-sourced Tasks/Plans/Documents;
* dependency graph;
* workflows/playbooks;
* worktrees;
* recovery;
* provider sessions;
* merge steward.

Но после нового поиска видно, что ему придётся добавлять больше всего именно в вашей ключевой области:

* отдельный Reviewer;
* ReviewOutcome;
* fresh reviewer contract;
* generic author/reviewer cycles;
* multiple independent review campaigns;
* durable HumanQuestion glue.

Stoneforge V2 собирается двигаться именно туда, но текущая версия ещё не там.

## StablyAI Orca

StablyAI Orca остаётся превосходной operator IDE:

* любые CLI;
* worktree;
* SSH;
* terminal UI;
* browser/computer use;
* mobile notifications.

Но как основной workflow control plane он уступает всем четырём лидерам: встроенная decomposition отсутствует, DAG базовый, review engine отсутствует, а restart-durable coordinator остаётся открытой проблемой.

---

# Сравнительная таблица

Легенда:

* **🟢** — уже хорошо реализовано;
* **🟡** — есть основа, нужен glue или custom workflow;
* **🔴** — практически надо строить.

| Проект             | Fresh/cut-off |                    Beads / DAG |               Разные CLI |             Durable human question |                     A ↔ B цикл | B → C / parallel reviews |   Verification |         Restart |
| ------------------ | ------------: | -----------------------------: | -----------------------: | ---------------------------------: | -----------------------------: | -----------------------: | -------------: | --------------: |
| **Metaswarm**      |            🟢 |                🟢 native Beads |   🟢 Claude/Codex/Gemini |                    🟡 интерактивно |                             🟢 |                       🟢 |             🟢 |              🟡 |
| **VirtusLab Orca** |            🟢 |    🟡 свой Plan, Beads adapter |           🟢 5 harnesses | 🟡 `ask_user`, не durable external |                     🟢 встроен |       🟢 программируется |             🟢 | 🟢 между stages |
| **Gas City**       |            🟡 |                🟢 native Beads |                       🟢 |        🟢 primitives, 🟡 transport |               🟡 нужна formula |        🟢 quorum/fan-out |             🟢 |              🟢 |
| **Forge**          |            🟡 |             🟡 собственный DAG |                       🟢 |   🟢 approval/UI, 🟡 внешний канал |                  🟢 gate retry |                       🟡 |             🟢 |              🟡 |
| **Bernstein**      |            🟢 |             🟡 собственный DAG |                       🟢 |                                 🔴 |                             🟢 |                       🟢 |             🟢 |              🟢 |
| **Tutti**          |            🟡 |             🟡 собственный DAG |                       🟢 |                                 🟡 |                     🟢 PR-loop |                       🟡 |             🟢 |              🟡 |
| **Stoneforge**     |            🟡 |      🟢 собственный rich graph |            🟡 3 provider |                                 🟡 | 🟡 test/fix, не generic review |                       🟡 |             🟢 |              🟢 |
| **Conductor**      |            🟡 | 🟢 workflow DAG, не task graph |          🟡 provider SDK |                                 🟢 |                             🟢 |                       🟢 |             🟡 |              🟡 |
| **StablyAI Orca**  |            🟢 |                 🟡 простой DAG | 🟢 практически любой CLI |                   🟡 decision gate |                             🔴 |               🟡 вручную | 🟢 browser/CLI |              🟡 |

---

# Где меньше всего допиливать

Оценки ниже — моя инженерная оценка при условиях:

* один репозиторий;
* CLI-first;
* один сильный разработчик с AI-агентами;
* Telegram через простой webhook/bot;
* без polished workflow editor;
* параллельная реализация не обязательна в первом MVP.

| Вариант                                    | До работающего MVP | До надёжной версии | Главный риск                                   |
| ------------------------------------------ | -----------------: | -----------------: | ---------------------------------------------- |
| **Metaswarm как есть**                     |        **1–3 дня** |         4–8 недель | Главный LLM остаётся workflow engine           |
| **VirtusLab Orca + Beads + external gate** |     **1–3 недели** |         4–7 недель | Нет глобального durable scheduler              |
| **Gas City + metaswarm-like pack**         |     **2–4 недели** |         5–8 недель | Нужно освоить Formula v2 и собрать domain pack |
| **Forge custom workflow** без Beads        |     **1–3 недели** |         4–7 недель | Public beta, workflow engine ещё переходный    |
| Forge + двусторонний Beads sync            |         3–5 недель |        6–10 недель | Два task stores                                |
| Bernstein                                  |         3–6 недель |        6–10 недель | Human gate и интеграция control surfaces       |
| Tutti                                      |         3–6 недель |        6–10 недель | Молодой проект, часть adapters ещё развивается |
| Stoneforge                                 |         4–7 недель |        8–14 недель | Generic review campaign надо проектировать     |
| StablyAI Orca                              |        6–12 недель |        3–5 месяцев | Придётся построить control plane поверх IDE    |

---

# Моя рекомендуемая архитектура

## Production-вариант: Gas City + процесс metaswarm

```text
┌─────────────────────────────────────┐
│ Gas City Controller                 │
│                                     │
│ Beads source of truth               │
│ DAG / readiness                     │
│ session scheduling                  │
│ waits / retries / recovery          │
│ external message correlation        │
└──────────────────┬──────────────────┘
                   │ Formula v2
┌──────────────────▼──────────────────┐
│ Your SDLC Pack                      │
│                                     │
│ design-author                       │
│ artifact-review-loop                │
│ task-breakdown                      │
│ task-plan-loop                      │
│ boundary-review                     │
│ implementation-loop                 │
│ verification-loop                   │
│ final-review-campaign               │
└──────────────────┬──────────────────┘
                   │ prompts / rubrics
┌──────────────────▼──────────────────┐
│ Metaswarm methodology               │
│                                     │
│ design review roles                 │
│ plan review rubrics                 │
│ adversarial review                  │
│ verification rules                  │
│ Playwright review                   │
└─────────────────────────────────────┘
```

Здесь metaswarm **не является вторым scheduler**. Из него берутся:

* prompts;
* rubrics;
* roles;
* phase structure;
* cross-model policies;
* review JSON contracts.

Владение состоянием остаётся только у Gas City/Beads.

## Что конкретно надо добавить

### 1. Artifact contract

```json
{
  "artifact_type": "design|breakdown|task_plan|review|verification",
  "subject_id": "bd-123",
  "revision": 3,
  "producer": "claude-architect",
  "content_ref": "docs/orchestration/design-v3.md",
  "content_hash": "sha256:...",
  "created_at": "..."
}
```

### 2. Context policy

```text
TASK_ONLY
SELECTED_ARTIFACTS
CUT_OFF
RESUME_SESSION
```

### 3. Review outcome

```json
{
  "subject_revision": 3,
  "reviewer": "codex-reviewer-b",
  "verdict": "approved|changes_requested|blocked",
  "findings": [
    {
      "id": "F-17",
      "severity": "major",
      "description": "...",
      "file": "...",
      "line": 42
    }
  ]
}
```

### 4. Finding disposition

```json
{
  "finding_id": "F-17",
  "decision": "fixed|rejected|wont_fix",
  "reason": "...",
  "revision_after_fix": 4
}
```

### 5. Human question

```json
{
  "question_id": "Q-12",
  "subject_id": "bd-123",
  "question": "...",
  "options": [],
  "blocks": ["bd-123.4"],
  "status": "pending",
  "external_conversation": "telegram:chat/thread",
  "answer": null
}
```

### 6. Immutable attempts

Не делать циклические Beads dependencies. Каждый раунд — отдельный attempt:

```text
design:A1
→ review-B:A1
→ revision:A2
→ review-B:A2
→ review-C:A1
```

Так сохраняются:

* аудит;
* конкретная revision;
* fresh context;
* возможность сравнивать reviewers;
* защита от stale approval.

Большую часть этого можно сделать custom Gas City pack без глубокого fork core.

---

# Более простой вариант для первого прототипа

Для первой рабочей реализации я бы серьёзно рассмотрел **VirtusLab Orca**.

Причины:

* ваш основной workflow почти линейный;
* параллельная реализация не критична;
* `reviewAndFixLoop` уже существует;
* fresh и durable sessions явно разделены;
* cross-vendor roles есть;
* stages resumable;
* JVM/Scala для вас будет значительно проще Go-инфраструктуры Gas City.

Структура одного Scala flow получится примерно такой:

```text
Stage: Design
Stage: Review design by B + fix loop
Stage: Review design by C + fix loop

Stage: Task breakdown
Stage: Review breakdown by B + fix loop
Stage: Review breakdown by C + fix loop

For each Beads task:
    Stage: Create task plan
    Stage: Review plan by B + fix loop

Stage: Boundary review all plans + fix loops

For each ready implementation task:
    Stage: Implement
    Stage: Review by B + fix loop
    Stage: Decide verification
    Stage: Run verification
    Stage: Repair + re-review + re-verify

Stage: Parallel final reviews
For each campaign with findings:
    Stage: Repair
    Stage: Re-review
```

Потребуются только два инфраструктурных дополнения:

```text
bd-scala adapter
external-question/pause-resume adapter
```

Это реалистичнее, чем переделывать Stoneforge или StablyAI Orca.

---

# Окончательный выбор

## Мой основной выбор для долгой эксплуатации

**Gas City + собственный Formula pack, основанный на metaswarm.**

Это единственный найденный путь, где одновременно естественно соединяются:

* Beads;
* долговечный граф;
* независимая блокировка одной ветки;
* recovery;
* разные агенты;
* параллельные reviewer lanes;
* внешние conversation bindings;
* возможность реализовать точный state machine без главного LLM в роли scheduler.

## Мой выбор для самого быстрого прототипа

**VirtusLab Orca + Beads wrapper.**

По объёму прикладного кода это, вероятно, самый короткий путь к буквальному `A → B → A → B → C`, а не просто к «несколько агентов работают над задачами».

## Самый быстрый вариант вообще

**Metaswarm**, чтобы за несколько дней проверить саму методологию на одной реальной фиче.

Но его я бы не делал окончательным control plane без добавления durable scheduler.

## Forge

Выбрал бы Forge только при готовности заменить Beads внутренними Tasks Forge. Делать live sync между ними не стоит.

---

# Проверка кандидата на практике

Перед окончательным выбором система должна пройти один vertical-slice тест:

1. Агент A создаёт техдизайн.
2. Fresh B дважды отклоняет его.
3. A исправляет один finding и мотивированно отклоняет другой.
4. B подтверждает disposition.
5. Fresh C одобряет документ.
6. Создаются две Beads-задачи, одна зависит от другой.
7. В первой возникает HumanQuestion.
8. Ветка блокируется, но независимая задача продолжает работу.
9. Процесс оркестратора принудительно завершается.
10. Ответ приходит через внешний канал.
11. После рестарта выполняется ровно одно продолжение.
12. Implementation проходит build/API test.
13. Два параллельных final reviewers создают независимые findings.
14. Каждый finding проходит собственный repair/re-review cycle.
15. Повторный запуск не дублирует задачи, вопросы, attempts и commits.

**Gas City имеет лучшие шансы пройти весь этот тест без фундаментальной переделки. VirtusLab Orca быстрее реализует почти все шаги, но пункты 8–11 потребуют отдельного durable scheduling слоя.**

[1]: https://github.com/dsifry/metaswarm "https://github.com/dsifry/metaswarm"
