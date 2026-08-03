# Контракты вендорских CLI

Статус: M0 завершён 2026-08-03. Наблюдения сняты на Ubuntu 24.04 VPS,
Linux 6.8.0. Это не WSL2; допущение recovery о чтении
`METASWARM_ATTEMPT_ID` из `/proc/*/environ` здесь не проверялось.

Сырые ответы лежат в `../../fixtures/vendor-cli/`. Это один smoke-прогон на
указанных версиях, поэтому версии входят в drift check, а real-vendor smoke
tests остаются opt-in.

## 1. Результат T0.3: фактические модели

Запрос `opus` проверен явно через `--model opus`. Для Claude Code одиночный
JSON-ответ версии 2.1.220 **не содержит обещанного верхнеуровневого `model`**:
модели перечислены в `modelUsage`, причём Anthropic может добавить туда
вспомогательную Haiku. Однозначный источник основной модели —
`system.init.model` в `stream-json`. Провайдер определяется профилем и его
endpoint, а не значением `modelUsage.*.provider`: прокси тоже помечены там как
`firstParty`.

| Профиль | Запрошено | Метаданные CLI | Самоописание модели | Вывод |
|---|---|---|---|---|
| `claude` | `opus` | `system.init.model=claude-opus-5`; `modelUsage` содержит основную `claude-opus-5` | `provider=anthropic; model=claude-opus-5` | `anthropic + claude-opus-5` |
| `claude-m` | `opus` | `system.init.model=MiniMax-M3`; `modelUsage.MiniMax-M3` | `provider=MiniMax; model=MiniMax-M3` | `minimax + MiniMax-M3` |
| `claude-z` | `opus` | `system.init.model=glm-5.2`; `modelUsage.glm-5.2` | в явном opus-прогоне — Z.AI/GLM; предыдущий прогон при тех же backend/model env назвал себя Anthropic/Claude | `z.ai + glm-5.2`; самоописанию не доверять |
| `codex` | default профиля | plain-mode banner: `provider: openai`, `model: gpt-5.6-sol`; JSONL поля модели не имеет | `provider=OpenAI; model=GPT-5.6 Codex` | `openai + gpt-5.6-sol`, подтверждается readiness-пробой banner |
| `cursor-agent` | — | бинарь отсутствует | не запускался | профиль недоступен на этом VPS |

Три Claude-профиля дают три различные пары provider+model. Стоп-условие T0.3
не сработало; четыре обязательных профиля по-прежнему дают четыре личности.

## 2. Матрица S1–S9

`cwd=missing` — дополнительная половина S8. Для Claude это ошибка launcher до
старта CLI; harness использовал GNU `env --chdir`, поэтому код 125 принадлежит
launcher, не Claude. В Codex `-C` является собственным флагом CLI.

| Профиль | S1 version | S2 PONG | S3 identity | S4 structured | S5 resume | S6 read-only | S7 SIGTERM | S8 errors | S9 pause/liveness |
|---|---|---|---|---|---|---|---|---|---|
| `claude` | `2.1.220`, 0 | stdout `PONG`, 0 | JSON, Opus 5, 0 | JSONL; финал `result/success`, 0 | тот же UUID, `ORBIT-47`, 0 | allowlist без write; файл не создан, 0 | 143; финал `error_during_execution/aborted_streaming`; 441 ms; без KILL | bad flag 1; missing cwd 125 before CLI | 14.038 s; max stdout gap 3.214 s; 0 |
| `claude-m` | `2.1.220`, 0 | stdout `PONG`, 0 | JSON, MiniMax-M3, 0 | JSONL; финал `result/success`, 0 | тот же UUID, `ORBIT-47`, 0 | allowlist без write; файл не создан, 0 | 143; финального события нет; 8 ms; без KILL | bad flag 1; missing cwd 125 before CLI | 15.892 s; max gap 3.238 s; 0 |
| `claude-z` | `2.1.220`, 0 | stdout `PONG`, 0 | JSON, glm-5.2, 0 | JSONL; финал `result/success`, 0 | тот же UUID, `ORBIT-47`, 0 | allowlist без write; файл не создан, 0 | 143; финального события нет; 62 ms; без KILL | bad flag 1; missing cwd 125 before CLI | 14.641 s; max gap 3.875 s; 0 |
| `codex` | `0.146.0`, 0 | stdout `PONG`; banner в stderr; 0 | JSONL без model metadata; self-report, 0 | JSONL; `item.completed/agent_message` перед `turn.completed`; 0 | тот же `thread_id`, `ORBIT-47`, 0 | `--sandbox read-only`; write получает EROFS; файл не создан; 0 | **0 без `turn.completed`**; 303 ms; без KILL | bad flag 2; missing `-C` cwd 1 | 20.603 s; max gap 6.909 s; 0 |
| `cursor-agent` | проверено: 127, не установлен | 127, недоступен | 127, недоступен | 127, недоступен | 127, недоступен | 127, недоступен | 127, сигнал послать некому | bad flag 127; missing cwd 125 before CLI | 127, недоступен |

Недоступность `cursor-agent` закрывает T0.4 допустимым вторым исходом и не
блокирует v1. Браузерная авторизация не запускалась и окружение автоматически
не изменялось.

## 3. Контракт адаптера Claude

Проверенный основной argv:

```text
claude -p <prompt> --model <requested> --output-format stream-json --verbose
```

Для профилей MiniMax и Z.AI рантайм должен запускать тот же бинарь напрямую,
передавая через env как минимум `ANTHROPIC_BASE_URL`,
`ANTHROPIC_AUTH_TOKEN=<secret_ref>` и model aliases. Рабочие обёртки во время
probe задавали:

| Профиль | `ANTHROPIC_MODEL` / Opus alias | Sonnet alias | Haiku / small-fast alias |
|---|---|---|---|
| `claude-m` | `MiniMax-M3` | `MiniMax-M2.7` | `MiniMax-M2.7` |
| `claude-z` | `glm-5.2` | `glm-5-turbo` | `glm-4.5-air` |

Контракт разбора:

- первая строка `system/init` даёт `session_id`, `model`, `permissionMode` и
  список реально доступных tools;
- любой корректно разобранный event и любой новый байт stdout/stderr —
  activity для heartbeat;
- успешный финал — только `type=result`, `subtype=success`, `is_error=false`;
  доменный ответ находится в `result`;
- UUID для продолжения берётся из `session_id`; проверенный resume-argv —
  `--resume <uuid>` вместе с новым `-p`;
- SIGTERM посылается группе процессов. Худший из трёх наблюдённых grace —
  441 ms; для v1 принимается консервативный `term_grace_s=2`, затем SIGKILL;
- exit 0 без успешного result не является успехом. Exit 143 — прерывание, даже
  если успел прийти error-result. Неизвестный флаг в 2.1.220 возвращает 1, а не
  предполагавшийся 2.

Строгий native read-only для ревьюера — allowlist:

```text
--tools Read,Grep,Glob
```

Попытка записи при таком allowlist не доходит до filesystem: write-capable
tools отсутствуют, рабочий каталог остаётся неизменным. `--permission-mode
plan` **не является строгим read-only**: дополнительный probe не изменил
checkout, но записал plan в `~/.claude/plans`. Это наблюдение сохранено как
`S6-plan-mode`; созданный plan-файл после фиксации удалён.

## 4. Контракт адаптера Codex

Проверенный основной argv:

```text
codex exec --json <prompt>
```

JSONL-последовательность happy path:

```text
thread.started(thread_id)
turn.started
item.completed(item.type=agent_message, item.text=<result>)
turn.completed(usage)
```

В многошаговом ответе `agent_message` может быть несколько. Финальный результат
— последний completed `agent_message` перед `turn.completed`. Успех требует
одновременно финального сообщения, `turn.completed` и exit 0. Это существенно:
при SIGTERM 0.146.0 вернул exit 0 после `thread.started` + `turn.started`, без
финального сообщения и без `turn.completed`.

Продолжение:

```text
codex exec resume --json <thread_id> <prompt>
```

Read-only:

```text
codex exec --json --sandbox read-only -C <checkout> <prompt>
```

Реальная попытка shell-записи получила `Read-only file system`; файл не создан.
События `item.started`/`item.completed` для tool execution и любые новые байты
считаются activity. В S9 команда с `sleep 6` дала максимальный наблюдённый
интервал 6.909 s.

JSONL не сообщает model/provider. Readiness/drift probe должен дополнительно
разобрать plain-mode banner (`model:`, `provider:`), а профиль обязан фиксировать
requested model. Нельзя принимать текстовое самоописание за `actual_model`.

## 5. Классификация ошибок

| Семейство | Наблюдение | Классификация адаптера |
|---|---|---|
| Claude | `result/success` + exit 0 | success |
| Claude | exit 0 без успешного `result` | protocol/contract failure |
| Claude | bad flag, exit 1 | configuration/usage error |
| Claude | exit 143 после TERM | cancelled/terminated |
| Codex | `turn.completed` + final agent message + exit 0 | success |
| Codex | exit 0 без complete marker | interrupted/incomplete, не success |
| Codex | bad flag, exit 2 | configuration/usage error |
| Codex | missing `-C` cwd, exit 1 | configuration/spawn error |
| Любой | missing cwd до запуска процесса | spawn error; vendor exit code отсутствует |
| Любой | после 2 s TERM grace ещё жив | SIGKILL; forced termination |

Старую таблицу предполагаемых Claude-кодов 2/3/4 и subtype
`error_max_turns`/`error_context_window`/`error_permission` эти короткие probes
не подтвердили. Их нельзя считать контрактом до отдельного воспроизводимого
сценария.

## 6. Фикстуры и fake CLI

Для каждого основного S1–S9 сохранены `.argv`, `.stdout`, `.stderr`, `.exit` и
`.notes`. Дополнительные `S5-setup`, `S8-missing-cwd` и `S6-plan-mode` сохраняют
многошаговые или альтернативные половины сценариев.

`../../fixtures/vendor-cli/fake-cli.py` выбирает профиль через `FAKE_PROFILE`,
находит fixture по argv и воспроизводит оба потока и exit code. Реализованы все
обязательные режимы `FAKE_MODE`: `broken_json`, `silent`, `no_finish`,
`ignore_term`, `slow`. Инструкции запуска находятся рядом в `README.md`.

Перед фиксацией fixture tree проверен против двух реальных значений токенов из
локальных wrapper-файлов и распространённых token patterns. Совпадений нет;
значения секретов нигде не записывались, в notes используется `<REDACTED>`.
