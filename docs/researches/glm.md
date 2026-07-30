Готового решения под все пять требований разом нет, но ландшафт 2025–2026 года подошёл к вашей задаче вплотную. Из свежих (не «запечатлённых в весах») проектов ближе всего три: **AWS CLI Agent Orchestrator (CAO)**, **Microsoft Conductor (YAML-first)** и **Bernstein**. Реалистичный путь — взять Microsoft Conductor как детерминированный оркестрационный слой (в нём из коробки есть YAML-граф, циклы, context-modes для handoff-без-контекста и human-gates), а CLI-агентов разных вендоров подключать через shell-шаги/MCP-серверы; либо взять AWS CAO, где мульти-вендорные CLI — это primary use case, и допилить review-loops + cut-off-файлы. Ниже — что именно я изучил и как это ложится на вашу спецификацию.

## Сводное сравнение по вашим пяти требованиям

| Требование | AWS CAO (cli-agent-orchestrator) | Microsoft Conductor (YAML) | Bernstein | OpenAI Agents SDK + input_filter | Microsoft Agent Framework (AutoGen+SK) Handoff |
|---|---|---|---|---|---|
| **1. Handoff без контекста / cut-off файл** | Частично: supervisor «provides only necessary context», session-based isolation в tmux【turn0fetch1】 | **Да (нативно):** context modes `accumulate` / `last_only` / `explicit` — именно «свежий взгляд» или только нужные зависимости【turn2fetch1】 | Worktree per agent = изоляция по умолчанию; cut-off файл — через handoff-документ【turn0fetch0】 | **Да:** `input_filter` на handoff — можно полностью выкинуть историю【turn4search2】 | Да: «agents do not share the same session instance»【turn4search9】 |
| **2. Параллеливание по графу (beads)** | Да: паттерн `Assign` (асинхронный spawn)【turn0fetch1】 | **Да:** `parallel:` группы с `fail_fast` / `continue_on_error` / `all_or_nothing`, dynamic for-each【turn2fetch1】 | **Да:** параллельные worktree-слоты, детерминированный scheduler【turn0fetch0】 | Через tool-calls параллельно | Concurrent orchestration |
| **3. Мульти-вендорные CLI (не LLM-API)** | **Да (лучшая поддержка):** Kiro, Claude Code, Codex, Antigravity, Hermes, Kimi, GitHub Copilot CLI, OpenCode, Cursor【turn2fetch0】 | **Нет:** только GitHub Copilot и Anthropic как LLM-providers, не CLI-агенты【turn2fetch1】 | **Да (лучшая поддержка):** 40+ CLI-адаптеров (Claude Code, Codex, Gemini CLI, Aider, Cursor, Devin, Q Developer…)【turn8search1】 | Нет (это SDK, не CLI-обёртка) | Нет (SDK) |
| **4. Open questions → уведомление + пауза** | Через plugins (outbound events) + flows; прямого «open question»-примитива нет【turn2fetch0】 | **Да (нативно):** `human_gate` шаг — pause + Rich TUI / web dashboard, маршрутизация по ответу【turn2fetch1】 | `scheduler.pause` + awaiting op (видно в audit-выводе)【turn3fetch1】 | Через handoff к human-агенту | Human-in-the-loop через workflow step |
| **5. Циклы A→B→A, max 3, затем escalation** | Через Send Message между агентами, лимит — свой【turn0fetch1】 | **Да (нативно):** routes с loop-back + `max_iterations` / wall-clock timeout【turn2fetch1】 | Janitor `verify`/`fail`/`retry` (3/3 видно в логах) + cross-model review / tournament【turn3fetch1】 | Свой цикл поверх handoffs | Свой цикл |

Ключевой вывод из таблицы: **ни один проект не закрывает всё пять пунктов из коробки**. CAO и Bernstein сильны на CLI-мульти-вендорности (требование 3), но слабее на формализованных циклах и явных «open questions». Microsoft Conductor идеален для пайплайна с циклами, context-изоляцией и human-gates (требования 1, 4, 5), но работает с LLM-providers, а не с CLI-агентами. Это и определяет выбор стратегии допила.

## Карточки топ-3 кандидатов

### 1. Microsoft Conductor (github.com/microsoft/conductor, MIT, 354★)【turn7fetch0】

YAML-first deterministic CLI: вы описываете агентов, их prompts, models, inputs/outputs и маршрутизацию в одном YAML, routing через Jinja2 — **ноль токенов на оркестрацию**【turn2fetch1】. Это практически готовый «каркас» под ваш флоу «тех-дизайн → декомпозиция → beads → ревью границ → реализация → ручная проверка → общее ревью».

Что закрывает сразу:
- **Циклы проверки (п.5):** routes с loop-back — `reviewer` может вернуть на `architect`, и так до `max_iterations`. Пример из блока буквально ваш use case: architect → reviewer → (если `approved=false`) обратно к architect【turn1fetch1】.
- **Handoff без контекста (п.1):** три режима `accumulate` / `last_only` / `explicit`. `last_only` — это «только задача для свежего взгляда», `explicit` — «только named dependencies» (аналог cut-off). Разработчики сами отмечают: «being deliberate about what each agent sees turned out to matter more than we expected»【turn2fetch1】.
- **Open questions → pause (п.4):** `human_gate` step — пауза, Rich TUI или web-dashboard, маршрутизация по ответу. Можно прикрутить webhook вместо TUI.
- **Параллельность (п.2):** `parallel:` группы + dynamic for-each.
- **Script steps для билда/деплоя/тестов:** shell-команды как шаг графа, ветвление по exit code — это ваш «билд, деплой, headless browser»【turn2fetch1】.

Где допиливать:
- **CLI-агенты разных вендоров (п.3):** из коробки только Copilot и Anthropic как LLM-providers. Решение — обернуть каждый CLI (Claude Code, Codex, Gemini CLI) в MCP-сервер или shell-step, который дёргает CLI и парсит stdout. Концептуально просто, но это ~50% всей работы по допилу.
- **Cut-off файл как first-class primitive:** workaround — agent пишет результат в `output.file_path`, downstream-агент читает только его (`explicit` режим + named dependency). Паттерн рабочий, но требует дисциплины в YAML.
- **Уведомление в Telegram/email при open question:** human_gate умеет только TUI/dashboard; нужен кастомный plugin или webhook-step.

### 2. AWS CLI Agent Orchestrator / CAO (github.com/awslabs/cli-agent-orchestrator, Apache-2.0, 961★)【turn3fetch0】

Изначально построен под вашу боль: «individual developer CLI tools excel at focused tasks… complex enterprise development projects often require coordination across multiple disciplines»【turn0fetch1】. Архитектура — supervisor agent + worker agents в изолированных tmux-сессиях, общение через локальный MCP-сервер.

Что закрывает сразу:
- **Мульти-вендорные CLI (п.3):** 9+ провайдеров из коробки (Kiro, Claude Code, Codex, Antigravity, Hermes, Kimi, GitHub Copilot CLI, OpenCode, Cursor), CLI остаются full processes со своей аутентификацией【turn2fetch0】. Это требование, ради которого CAO создавался.
- **Handoff без контекста (п.1):** «supervisor provides only necessary context to each worker agent, avoiding context pollution»【turn0fetch1】. Плюс tmux-isolation = у каждого воркера своя сессия.
- **Параллельность (п.2):** паттерн `Assign` — асинхронный spawn【turn0fetch1】.
- **Паттерны координации:** Handoff (синхронно), Assign (асинхронно), Send Message (прямая коммуникация)【turn0fetch1】 — последний закрывает ваш сценарий «А нашёл фандинги → отправил Б на правку».
- **Flows:** cron-style scheduled runs, multi-step pipelines【turn2fetch0】.
- **Plugins (outbound events):** есть plugin-система для outbound events — это точка прикручивания Telegram/email【turn2fetch0】.

Где допиливать:
- **Циклы A→B→A с max 3 + escalation (п.5):** нет first-class primitive «review-loop с лимитом». Реализуется через supervisor-логику: supervisor вызывает worker, затем reviewer, при `found_issues` снова worker, счётчик в supervisor-контексте, после 3 — human escalation. Это ~30% работы.
- **Open questions → пауза ветки (п.4):** прямого примитива нет; решается через plugin-outbound-event + supervisor переводит ветку в paused-состояние. Можно, но это кастомная логика в supervisor-профиле.
- **Cut-off файл:** workaround через Skills (reusable agent guidance) + memory (persistent cross-session)【turn2fetch0】.

### 3. Bernstein (bernstein.run, Apache-2.0, 751★)【turn0fetch0】

Детерминированный Python-scheduler для CLI-агентов: «no model in the coordination loop, so the same plan replays byte-identically»【turn0fetch0】. Четыре стадии: `decompose → spawn → verify → merge`, каждый агент в своём git worktree.

Что закрывает сразу:
- **Мульти-вендорные CLI (п.3):** 40+ адаптеров (Claude Code, Codex, OpenAI Agents SDK v2, Gemini, Cursor, Aider, Cloudflare Agents, GitHub Copilot, Devin, Q Developer…)【turn8search1】. Самая широкая поддержка в категории.
- **Параллельность (п.2):** изоляция через worktree — параллельность by default.
- **Верификация (ближе к п.5):** Janitor-система проверяет tests/lint/types/PII per-diff; в audit-выводе видно `janitor.fail … 3/3 retries` → `scheduler.route … awaiting op`【turn3fetch1】. Это почти ваш «max 3 цикла, затем human escalation», только триггер — тесты, а не LLM-ревьюер.
- **Аудит:** HMAC-chained event log, проверяемый оффлайн — для регламентированных сред это большой плюс【turn0fetch0】.

Где допиливать:
- **LLM-ревьюер как участник цикла (п.5):** Bernstein верифицирует через deterministic checks (lint/types/tests), а не через «агент Б проверил агента А». Есть `cross-model review` и `tournament` (best-of-n через LLM-judge)【turn3fetch1】, но это не совсем цикл «А сделал → Б проверил → А поправил». Нужно вводить отдельный review-stage в manager-план.
- **Open questions → pause + notify:** `scheduler.pause` есть, но уведомление в Telegram — через opt-in telemetry в свой OTel/Datadog/S3【turn3fetch1】, не «прикрутить канал за 5 минут».
- **Handoff без контекста / cut-off:** worktree = изоляция, но явного cut-off-файла нет; pattern — handoff-документ.
- **Граф beads с ревью границ скопом:** Bernstein про parallel execution, а не про многофазный SDD-пайплайн. Ваш флоу «тех-дизайн → декомпозиция → beads → ревью всех → реализация» — это скорее поверх Bernstein, чем внутри.

## Гибридная архитектура (где допил минимальный)

Два жизнеспособных варианта, в порядке надёжность/сложность:

**Вариант A — «Conductor как мозг, CLI-агенты как исполнители» (рекомендуемый).** Microsoft Conductor уже реализует 4 из 5 ваших требований на уровне примитивов. Единственный крупный пробел — CLI-мульти-вендорность. Решение: каждый CLI-агент оборачивается в MCP-сервер (или shell-step), который принимает task-описание + cut-off-файл, запускает CLI (Claude Code, Codex, Gemini CLI) в headless-режиме, пишет результат в файл и возвращает exit-code + путь. В YAML Conductor-а это просто `script` step или MCP-tool-call. Ваши циклы проверки (п.5), human-gates для open-questions (п.4), parallel-groups для beads (п.2) и context-modes для handoff-без-контекста (п.1) — всё нативно. Допил ~30%: написание CLI-обёрток + webhook-plugin для Telegram.

**Вариант B — «CAO + supervisor-профиль с review-loops».** Берёте AWS CAO как есть (CLI-мульти-вендорность и tmux-isolation уже готовы), дописываете supervisor-профиль, в котором реализованы: счётчик циклов A→B→A с max 3, переход в `paused-on-open-question` при обнаружении FAQ, outbound-plugin дёргает Telegram-бота. Допил ~40%, но supervisor-логика — это Python в репозитории CAO, его реально форкнуть и дописать. Плюс CAO уже поддерживает Claude Code sub-agents и Q Developer Custom Agents【turn0fetch1】, что близко к вашей модели.

**Вариант C — «Bernstein + внешний SDD-слой».** Если для вас критичны audit/deterministic-replay (например, enterprise-клиенты с комплаенсом), Bernstein даёт это из коробки. Но ваш флоу с многофазным ревью и open-questions — это надстройка над Bernstein, допил ~50%.

## Допил, который везде нужен

Независимо от выбора базы, четыре вещи придётся реализовать вручную:

1. **Cut-off файл как first-class primitive.** Паттерн: агент А завершает работу, пишет `./handoffs/{task_id}.md` (summary + артефакты + что не сделано), downstream-агент Б стартует с этим файлом как единственным контекстом. В Conductor — через `explicit` context mode + named dependency на `output.file_path`. В CAO — через Skills + Memory. В Bernstein — через handoff-документ в worktree.
2. **Open-question-детектор.** Агент возвращает структурированный output с полем `open_questions: []`; если массив непустой — оркестратор ставит ветку на pause и дёргает webhook. Это ~100 строк Python поверх любого из трёх.
3. **Webhook → Telegram/email.** Conductor и CAO имеют plugin/event-системы; Bernstein — opt-in telemetry. Минимально — `requests.post` в bot API.
4. **Финальное «ревью скопом разными агентами».** Параллельный fan-out N ревьюеров, каждый со своим context-mode `last_only` (чтобы не видеть выводы других ревьюеров), сбор findings, маршрутизация правок. В Conductor — `parallel:` группа + агрегация; в CAO — N `Assign` + supervisor-агрегация; в Bernstein — tournament/best-of-n.

## Итоговая рекомендация по сложности/надёжности

- **Минимальный допил, максимальная надёжность:** Microsoft Conductor + CLI-обёртки через MCP. YAML-граф = ваш пайплайн буквально читается как спецификация, циклы/human-gates/context-modes — native, детерминизм даёт воспроизводимость. Риск — молодой проект (226 коммитов, 354★)【turn7fetch0】, но MIT и активный.
- **Если CLI-мульти-вендорность критичнее формализации циклов:** AWS CAO. 9+ CLI-providers из коробки, supervisor-pattern, plugins для outbound events. Допил — review-loops + open-question-pause в supervisor-профиле.
- **Если нужен audit-grade trail и air-gap:** Bernstein. Но будьте готовы, что SDD-флоу строится поверх, а не внутри.
- **SDK-подход (OpenAI Agents SDK + input_filter):** стоит рассмотреть, если готовы сами писать CLI-обёртки и весь граф. `input_filter` на handoff даёт чистый «fresh context», но вы берёте на себя всё — scheduler, parallel, pause, escalation. Это самый гибкий, но и самый трудоёмкий путь.

Combinator-вывод: **Conductor как оркестратор + CAO-подобные CLI-обёртки** даёт вам лучшее из обоих миров — детерминированный YAML-граф с нативными циклами и context-изоляцией, и мульти-вендорные CLI как исполнители. Это ~30% допила против ~50% у «чистого» SDK-подхода и ~40% у «CAO + review-loops».