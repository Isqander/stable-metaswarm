# Оркестраторы CLI‑кодинг‑агентов: ландшафт 2025–2026 и рекомендации под ваш флоу

## TL;DR
- **Готового решения "из коробки" под весь ваш флоу (тех‑дизайн → таск‑граф → таск‑планы → реализация → ревью‑циклы с эскалацией) сегодня не существует.** Ближе всех подходит связка **Gas City (SDK Стива Йегге) + beads**, где уже есть граф‑беды, мульти‑вендорные CLI‑агенты, "судьи у ворот" с лимитом циклов (`max_attempts=3`) и человеческие гейты — но она сырая ("vibe coded", v1.2.1) и потребует конфигурирования формул под ваши стадии.
- **Оптимум по критерию "сложность/надёжность" — не монолит, а слой из трёх компонентов:** durable‑бэкбон (Temporal или DBOS) как машина состояний с паузами на человека → адаптер запуска CLI‑агентов (AgentAPI от Coder / Claude Agent SDK subprocess) → beads как граф задач + HumanLayer/Telegram‑мост для "открытых вопросов". Ревью‑циклы и эскалацию вы описываете как обычный код поверх durable‑степов.
- **Если хотите минимум кода и готовы мириться с ограничениями — берите Gas City и допиливайте формулы;** если нужна предсказуемая надёжность продакшн‑уровня и вы готовы написать тонкий оркестратор — стройте на durable‑движке. Vibe‑Kanban/Conductor/Sculptor годятся как ручной "пульт" для параллельных агентов, но не автоматизируют ваш конвейер с гейтами.

## Key Findings

**1. Рынок разделился на 4 слоя, и ваш флоу требует комбинации из всех четырёх:**
- **Готовые оркестраторы CLI‑агентов**: Gas Town/Gas City (Yegge), Vibe‑Kanban (Bloop AI), Conductor, Sculptor (Imbue), Crystal, amux, Claude Squad, claude‑flow, agentmux, maestro‑orchestrate.
- **Слой графа задач ("beads")**: beads (`bd`) Стива Йегге — именно тот термин, который вы используете; DAG‑зависимости, `bd ready`, git‑backed, мульти‑агентный.
- **Durable‑бэкбоны** (надёжность, паузы на часы/дни, resume): Temporal, Restate, DBOS, Inngest/AgentKit, Hatchet, Trigger.dev, Cloudflare Workflows.
- **HITL/нотификации ("открытые вопросы")**: HumanLayer/CodeLayer, gotoHuman, Claude Code Channels (официальный Telegram/Discord плагин), ccgram, Omnara.

**2. Gas City максимально близок к вашей спецификации концептуально**, потому что Йегге строил его ровно под "фабрику агентов": беды на конвейере, формулы (workflow как граф), судьи у ворот, человеческие гейты, мульти‑вендор. Но это experimental‑инструмент "для Stage 7+ разработчиков".

**3. Ни один готовый продукт не реализует ваши ревью‑циклы с жёстким капом 3 и последующей эскалацией к человеку "из коробки" как декларативную настройку** — кроме примера‑формулы Gas City, где это выражено явно (`max_attempts = 3` + exec‑скрипт review‑gate). Всё остальное придётся собирать.

**4. Мульти‑вендорность CLI‑агентов реально решена в немногих местах**: Gas Town/Gas City (пресеты claude, codex, gemini, opencode, amp, cursor, copilot, pi и др.), Vibe‑Kanban (10+ агентов), AgentAPI (Coder — универсальный HTTP‑адаптер). Большинство "claude‑only" инструментов (Conductor, Crystal, claude‑flow) вам не подходят как база.

## Details

### Слой 1. Готовые оркестраторы CLI‑агентов

**Gas Town / Gas City / beads (Steve Yegge, gastownhall)** — самый амбициозный и самый близкий к вашему видению.
- **Gas Town** (`gt`, github.com/gastownhall/gastown): Go, MIT, ~17.3k звёзд, 1.6k форков (июль 2026), релиз январь 2026. Роли: Mayor (координатор), Polecats (эфемерные воркеры в git‑worktree), Crew (постоянные воркеры), Refinery (merge‑queue + верификационные гейты, Bors‑style bisecting), Witness (per‑rig мониторинг зависших агентов), Deacon (патруль‑демон, рестарт), Dogs (обслуживание). Всё состояние — в beads (git‑backed). Мульти‑вендор: встроенные пресеты `claude, gemini, codex, kiro, cursor, auggie, amp, opencode, copilot, pi, omp`; провайдер задаётся в `settings/config.json`, оверрайд `gt sling <bead> <rig> --agent cursor`.
- **Gas City** (`gc`, github.com/gastownhall/gascity): "orchestration‑builder SDK", извлечённый из Gas Town. Go 95.9%, MIT, latest release v1.2.1 (1 июня 2026), ~897 звёзд, 299 форков, 4093 коммита. Шесть примитивов: Agent, Bead, Formula, Rig, Pack, Event. Оркестратор **не хардкодит роли** — всё это конфигурация в Pack. Формула запускается как граф: декомпозирует работу на беды, фанит готовые параллельно, держит шаги до закрытия зависимостей, ретраит падения, гонит граф к завершению вне вашей сессии.
- **Судьи у ворот и лимит циклов**: сайт gascity.com — "Beads on the belt, formulas driving the factory floor, judges at the gates". Chris Sells (CEO Gas City, Inc.) в посте "Announcing Gas City 1.0!" (sellsbrothers.com, 28 апреля 2026) публикует реальную формулу `code-review-loop`: один coder‑агент + ТРИ ревьюера (Codex, Claude, Gemini) + синтезатор. В TOML: `[steps.check] max_attempts = 3` и exec‑шаг `checks/review-gate.sh` (решает, продолжать ли цикл). Вердикт синтезатора: `approved` если нет блокирующих/major находок, иначе `iterate`. Пакет `internal/convergence` = "bounded iterative refinement loops".
- **Человеческая эскалация**: Gas City — "human gates" как первоклассная фича формул; режимы `interaction_mode` (interactive/autonomous/headless), `review_mode` (report‑only/machine handoff/interactive). Gas Town — `gt escalate` создаёт беды с severity CRITICAL(P0)/HIGH(P1)/MEDIUM(P2), маршрутизирует через Deacon→Mayor→Overseer; в релизах есть коммит "implement escalation notification channels (email, slack, sms, log)".
- **Открытые вопросы / пауза**: примитив `mail` (тип беды); `gt gate`/park — "park your work on a gate. When the gate closes, you'll receive wake mail", после чего можно выйти и `gt resume`. Дашборд Gas City — "pending‑interaction response controls" (человек отвечает на блокирующий вопрос агента из UI).
- **Мульти‑вендор дословно** (Sells, 1.0): "Gas City has a built-in 'factory worker' protocol to standardize access to your favorite CLI coding agents, including codex, claude, and gemini, of course, but also amp, opencode, pi, etc. ... change your coder agent ... without changing your formulas." Провайдер задаётся декларативно (`provider = "codex"`, `option_defaults = { model = "gpt-5.5" }`).
- **Вердикт**: концептуально это ровно "фабрика с гейтами и бедами", как у вас. Минусы: сырость (Йегге: проект "100% vibe coded", высокий burn API, жалобы на "murderous rampaging Deacon" и авто‑merge падающих тестов), требует экспертизы Stage 7+, Telegram явно не подтверждён (email/Slack/SMS есть), Qwen в пресетах не найден. Надёжность — "дайл, который вы крутите" (больше раундов ревью/judge = надёжнее), но продакшн‑гарантий durable‑движка нет.

**Vibe‑Kanban (Bloop AI, github.com/BloopAI/vibe-kanban)** — Rust‑бэкенд + React, Apache‑2.0, web‑UI канбан для параллельных CLI‑агентов. Поддерживает 10+ агентов (Claude Code, Codex, Gemini CLI, Copilot, Amp, Cursor, OpenCode, Droid, CCR, Qwen Code). Каждая задача — свой git‑worktree/бранч; ревью диффов, setup/cleanup скрипты, PR. **Важно: компания Bloop объявила о закрытии 10 апреля 2026** (Louis Knight-Webb: "the vast majority are free users and we couldn't find a business model that we could get excited about"); проект продолжается как community‑maintained под Apache‑2.0, перешёл на fully‑local архитектуру, ~26.7K звёзд к июлю 2026. Зависимостей‑DAG и жёстких ревью‑циклов с капом нет; есть открытый feature request на интеграцию с beads (issue #1394). Годится как ручной пульт, не как автомат вашего конвейера.

**Sculptor (Imbue, github.com/imbue-ai/sculptor)** — desktop, MIT, каждый агент в отдельном Docker‑контейнере (не worktree), синк в локальный репо; Claude Code first + поддержка Codex, экспериментально Pi. Есть "CI Babysitter" (авто‑диспатч агентов на починку падающих пайплайнов), skills для spec/TDD. Только macOS(Apple Silicon)/Linux, desktop. Хорош для безопасной параллельной работы, но не для декларативного пайплайна.

**Conductor** — macOS desktop, параллельные Claude Code в изолированных workspace, ревью кода. Claude‑only. **Crystal** — параллельные Codex/Claude Code сессии в git‑worktree. **amux** (amux.io) — open‑source control plane на tmux, self‑healing watchdog, 1:1 каналы с @mentions, атомарный захват задач через SQLite CAS. **maestro‑orchestrate** — набор промптов/скиллов (39 "специалистов") поверх Gemini CLI/Claude Code/Codex/Qwen, 4‑фазный workflow с ревью, но это не durable‑движок.

### Слой 2. Граф задач — beads (`bd`)

Именно ваш термин. Distributed git‑backed граф‑issue‑tracker для агентов, Go, теперь на Dolt (versioned SQL, cell‑level merge, native sync). Создан Йегге, релиз 13 октября 2025 ("Introducing Beads: A Coding Agent Memory System"); за первые шесть дней собрал ~1000 звёзд и ~50 форков (Йегге: "I vibe-coded this whole project ... all in six days"), к 2026 — 24k+ звёзд. Ключевое:
- 4 типа зависимостей: `blocks`, `related`, `parent-child`, **`discovered-from`** (агент сам заводит issue при обнаружении новой работы, с трассировкой к родителю).
- `bd ready` — выдаёт задачи без открытых блокеров (готовые к параллельному запуску) — это ровно ваша "распараллеливание по графу".
- JSON‑first (`--json` у каждой команды), hash‑based IDs (без merge‑коллизий), semantic compaction (сжатие старых закрытых задач для экономии контекста), `bd remember` (персистентная память), тип `message` с тредами и mail‑делегированием.
- Ограничение: issue не могут ссылаться на issue в другом проекте (каждая БД изолирована). Активная разработка (1.x).

beads — идеальный "control/data plane" для ваших таск‑планов вне контекстного окна агентов, и он уже интегрирован в Gas Town/Gas City.

### Слой 3. Durable‑бэкбоны (надёжность, паузы, resume)

Ключ к вашему требованию "остановка до ответа человека на часы/дни" + restart‑safety. Durable execution журналирует каждый шаг и возобновляет ровно с места падения; HITL‑паузы маппятся на suspend/resume‑примитивы, workflow может ждать дни без потребления ресурсов.
- **Temporal** — самый зрелый; workflow/activity: workflow — детерминированный план, activity — LLM/tool‑вызовы (включая spawn CLI‑агента). `workflow.wait_condition()` / signals дают бесплатную паузу на человека, переживающую рестарты воркеров. Минус: требование детерминизма workflow‑кода, versioning, отдельный сервер — оверхед моделирования.
- **DBOS** — durable execution только на Postgres, без отдельного сервера; проще всего вписать, если у вас уже есть Postgres. Уступает Temporal по throughput/экосистеме.
- **Restate** — легче Temporal, лучше для serverless/edge, journaled `ctx.run()`, exactly‑once без idempotency‑ключей в коде.
- **Inngest / AgentKit** — event‑driven, `waitForEvent()` для "агент спрашивает человека"; AgentKit (TypeScript) даёт Networks/Router/State, есть пример SWE‑bench и coding‑agent с code‑based роутером. Serverless‑first, durable степы.
- **Hatchet, Trigger.dev, Cloudflare Workflows, AWS Lambda Durable Functions** — прочие варианты; выбор зависит от стека.

Для вашего сценария durable‑движок — это то, что даёт **надёжность**, которой нет у "vibe coded" оркестраторов: гейты, ретраи, каппинг циклов, паузы и эскалация становятся обычными детерминированными степами.

### Слой 4. HITL / "открытые вопросы" / нотификации

- **HumanLayer** — API/SDK для human‑in‑the‑loop: `@require_approval(channel="slack")`, гранулярный роутинг к людям/командам, эскалации, таймауты, обучение на прошлых решениях, вебхуки. Каналы: Slack, email, а в Premium — Teams, SMS, RCS. Авторы "12‑factor agents" — прямая теоретическая основа вашего флоу; дословно из репозитория: "Factor 7: Contact humans with tool calls: Treat humans as high-latency tools. Use ask_human or request_approval as structured tool outputs that trigger a pause in the loop. Factor 8: Own your control flow: You should own the while loop." (основатель Dex Horthy проанализировал 100 000 сессий, выделив "dumb zone" — средние 40–60% большого контекстного окна, где деградирует recall). Их же **CodeLayer** — open‑source "post‑IDE IDE" (Go‑демон `hld` + Tauri/React + CLI): параллельные Claude Code сессии, approval‑гейты (агент ждёт human confirmation перед bash/write_file), forking сессий, context engineering. Claude Code‑ориентирован.
- **Claude Code Channels** (официальный плагин Anthropic, code.claude.com/docs/en/channels) — push событий в сессию из Telegram/Discord/iMessage/вебхуков; permission relay для удалённого approve/deny. Ограничения: сессия должна быть жива (нет always‑on демона), при permission‑prompt сессия висит пока не ответите; для unattended — `--dangerously-skip-permissions`.
- **ccgram, claude‑code‑telegram (RichardAtCT), Omnara** — сторонние Telegram/мобильные мосты: нотификации, ответы на вопросы кнопками, remote approval. Omnara — mobile‑first (iOS/Android, голос), "пульт" поверх Claude Code/Codex.
- Итог: канал нотификаций — легко подключаемая часть (как вы и написали, "всегда можно прикрутить канал"). Проблема не в канале, а в durable‑паузе ветки до ответа — её надёжно даёт только durable‑бэкбон или mail/gate у Gas Town.

### Дополнительно: инфраструктура изоляции и запуска
- **AgentAPI (Coder, github.com/coder/agentapi)** — HTTP‑API поверх широкого набора агентов: по README "Control Claude Code, AmazonQ, Opencode, Goose, Aider, Gemini, GitHub Copilot, Sourcegraph Amp, Codex, Auggie, and Cursor CLI with an HTTP API." Цель дословно: "make AgentAPI a universal adapter to control any coding agent, so a developer using AgentAPI can switch between agents without changing their code." SSE `/events`, `/message`, `/status`; сервер по умолчанию на порту 3284, последняя версия 0.12.2. Это лучший готовый строительный блок для гетерогенного запуска CLI‑агентов.
- **container‑use (Dagger, github.com/dagger/container-use)** — MCP‑сервер + CLI (`cu`): каждый агент в своём контейнере + git‑бранче, параллельно, git‑based review (`cu merge`). Универсально (любой MCP‑агент).
- **Claude Agent SDK** — spawns Claude Code CLI как subprocess (stdin/stdout JSON‑lines), subagents для fan‑out, hooks, permission‑коллбэки. Но: **lock‑in на Claude‑модели**, durable‑execution надо строить сверху самому.
- **git worktrees** — базовая техника изоляции параллельной реализации (у вас "распараллеливание не критично", но worktrees — дешёвый способ).

### Соответствие требованиям (матрица)

| Требование | Gas City+beads | Durable (Temporal/DBOS)+beads+AgentAPI+HumanLayer | Vibe‑Kanban | Sculptor/Conductor |
|---|---|---|---|---|
| Гетерогенные CLI‑агенты | ✅ пресеты | ✅ через AgentAPI | ✅ 10+ | ⚠️ Claude‑first |
| Fresh‑context handoff + cut‑off файл | ✅ беды/seance, но cut‑off свой | ✅ пишете сами (артефакты в git/beads) | ⚠️ вручную | ⚠️ вручную |
| Граф задач (beads) + параллель | ✅ нативно | ✅ beads + `bd ready` | ❌ нет DAG | ❌ |
| Открытые вопросы → нотификация + пауза ветки | ⚠️ mail/gate/escalate (email/slack/sms), Telegram нет | ✅ durable signal + HumanLayer/Telegram | ❌ | ❌ |
| Ревью‑циклы A↔B, кап 3, эскалация; затем C | ⚠️ формула `max_attempts=3` + judge, но C‑каскад свой | ✅ пишете как код (полный контроль) | ❌ | ❌ |
| Ревью‑инициированные циклы (reviewer→fix) | ⚠️ конфигурируемо | ✅ код | ❌ | ❌ |
| Пайплайн: тех‑дизайн→таски→планы→cross‑review→реализация→E2E→финальное ревью | ⚠️ формулы под каждую стадию | ✅ степы под каждую стадию | ❌ | ❌ |
| Durability/resume на дни | ⚠️ beads‑backed, но не журнал durable‑движка | ✅ сильнейшая сторона | ❌ | ❌ |
| Зрелость/прод‑готовность | ⚠️ experimental "vibe coded" | ✅ Temporal зрелый | ⚠️ company shut down | ⚠️ beta |
| Сложность внедрения | средняя (конфиг формул) | высокая (пишете оркестратор) | низкая | низкая |

## Recommendations

Ранжирую три архитектуры по критерию "сложность внедрения vs надёжность".

### Вариант A (минимум допиливания, быстрый старт, надёжность средняя) — Gas City + beads, конфигурируем формулы
- **Что делаете**: ставите `gc` + `bd`; описываете ваш конвейер как набор **формул**: `tech-design` → `task-breakdown` → `per-task-plan` → `cross-review` → `implement` → `e2e-verify` → `final-review`. Каждая стадия с ревью — формула с `max_attempts = 3` и exec‑скриптом review‑gate (у вас уже есть готовый пример от Sells). Мульти‑вендор — через пресеты провайдеров (claude/codex/gemini/opencode). Эскалация — `gt escalate`/human gates; нотификации — email/Slack (Telegram прикручиваете через вебхук/мост).
- **Что допилить**: (1) каскад "после B — тот же цикл с C" (пишете как вторую судейскую формулу); (2) reviewer‑initiated циклы (формула, стартующая с судьи, диспатчащая fix‑беду через `discovered-from`); (3) Telegram‑канал для открытых вопросов; (4) cut‑off/handoff‑файл (соглашение об артефакте в git). Оценка усилий: **низкая‑средняя** (конфиг + shell‑скрипты гейтов).
- **Риск**: сырость Gas City (v1.2.x, "vibe coded"), возможные регрессии, высокий API‑burn. Подходит, если вы Stage 7+ и готовы жить на bleeding edge.

### Вариант B (оптимум сложность/надёжность — РЕКОМЕНДУЮ) — тонкий оркестратор на durable‑движке + beads + AgentAPI + HITL
- **Стек**: **Temporal** (или **DBOS**, если хотите только Postgres без сервера) как машина состояний вашего пайплайна; **beads** как граф таск‑планов и память; **AgentAPI (Coder)** как единый способ запускать любой CLI‑агент (Claude Code/Codex/Gemini/opencode) subprocess'ом и читать статус/события; **HumanLayer** (+ Telegram/email мост) как слой "открытых вопросов" и эскалации; **git worktrees / container‑use** для изоляции параллельной реализации.
- **Почему оптимум**: ваши самые жёсткие требования — durable‑пауза ветки на дни до ответа человека, жёсткий кап 3 цикла с эскалацией, независимые reviewer‑инициированные циклы — это ровно то, что durable‑движки дают надёжно и детерминированно (signals/wait_condition, ретраи, resume после краха). Ревью‑петли и каскад A→B→C вы пишете как обычный код (полный контроль, тестируемо), а не боретесь с чужими абстракциями. beads снимает граф‑задачи и fresh‑context, AgentAPI снимает мульти‑вендорность.
- **Что писать самому**: сам оркестратор (workflow‑определения стадий, review‑loop с counter≤3→escalate, диспетчер по `bd ready`). Оценка усилий: **средняя‑высокая**, но результат — предсказуемый и сопровождаемый.
- **Порог, меняющий решение**: если Gas City к моменту старта стабилизируется (v2.x, снятие "vibe coded" ярлыка, Telegram и Qwen в пресетах) — Вариант B по трудозатратам сравняется с A, и тогда берите A.

### Вариант C (максимум готового, минимум автоматизации) — Vibe‑Kanban/Sculptor как ручной пульт
- Подходит, только если вы согласны сами быть "оркестратором": вручную раскидывать таски по агентам, смотреть диффы, ревьюить. Ваши ревью‑циклы с капом и эскалацией НЕ автоматизируются. Рекомендую лишь как временный «пилот» пока строите A или B, либо для быстрой оценки качества конкретных CLI‑агентов.

**Итоговая рекомендация**: начните с **Варианта B** для надёжного ядра (durable + beads + AgentAPI), но **прототипируйте на Gas City (Вариант A)**, чтобы за день‑два проверить сам флоу "фабрики с гейтами" на реальной задаче — Gas City уже даёт ~80% вашей семантики в конфиге, и это лучший способ понять, какие формулы/гейты вам реально нужны, прежде чем кодить их на Temporal.

## Caveats
- **Gas City/Gas Town — быстро меняющийся, экспериментальный проект.** Версии, звёзды, пресеты приведены на конец июля 2026; за недели всё может измениться. Метрики продуктивности (74 PR/день, 65% merge за 24ч) — самоотчёт вендора, независимо не подтверждены. Telegram как канал и Qwen как провайдер на момент проверки не подтверждены. Йегге прямо предупреждает: "If you're not at least Stage 7 ... you will not be able to use Gas Town. You aren't ready yet."
- **Vibe‑Kanban**: компания Bloop закрылась (10 апреля 2026); проект жив как community‑maintained под Apache‑2.0, но темп развития под вопросом.
- **Claude Agent SDK** удобен, но заперт на Claude‑модели и не даёт durable‑execution сам по себе — против вашего требования гетерогенности.
- **Ralph‑петля** (Geoffrey Huntley, впервые описана в мае 2025, "In its purest form, Ralph is a Bash loop") — это техника, не продукт; автор прямо сомневается, что из неё выйдет хороший продукт: "There's been a few attempts to turn it into products, but I don't think that will work ... you really want to babysit this thing." Полезна как паттерн fresh‑context‑итераций внутри одной стадии, не как оркестратор.
- **"Классические" Temporal/LangGraph/CrewAI/Airflow** приведены только как бэкбон‑бейзлайн (Temporal реально силён для durable‑ядра). LangGraph/CrewAI/AutoGen/Microsoft Agent Framework ориентированы на LLM‑через‑API, а не на spawn внешних CLI‑агентов — под ваше требование №3 они не подходят как готовое решение.
- Ни один durable‑движок не чинит галлюцинации/runaway‑петли/eval‑drift сам по себе — ваши гейты и кап циклов остаются вашей ответственностью.
- Все цифры звёзд/версий/дат следует перепроверить на момент внедрения.

### Ссылки на проекты
- Gas Town: github.com/gastownhall/gastown · Gas City: github.com/gastownhall/gascity (docs.gascity.com, gascity.com) · beads: github.com/gastownhall/beads
- Vibe‑Kanban: github.com/BloopAI/vibe-kanban (vibekanban.com) · Sculptor: github.com/imbue-ai/sculptor (imbue.com/sculptor) · container‑use: github.com/dagger/container-use
- AgentAPI: github.com/coder/agentapi · amux: amux.io · maestro‑orchestrate: github.com/josstei/maestro-orchestrate
- HumanLayer / 12‑factor agents: github.com/humanlayer/12-factor-agents · CodeLayer: github.com/humanlayer/humanlayer (humanlayer.dev) · Claude Code Channels: code.claude.com/docs/en/channels · ccgram: github.com/jsayubi/ccgram · claude‑code‑telegram: github.com/RichardAtCT/claude-code-telegram
- Temporal: temporal.io · DBOS: dbos.dev · Restate: restate.dev · Inngest AgentKit: agentkit.inngest.com
- Ralph Wiggum: ralph-wiggum.ai · awesome‑cli‑coding‑agents: github.com/bradAGI/awesome-cli-coding-agents