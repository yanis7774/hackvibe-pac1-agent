# Hack'n'Vibe — агент для BitGN PAC1

Компактный агент для бенчмарка [BitGN PAC1](https://bitgn.com). 5 место в Ultimate Leaderboard на слепом прогоне (pac1-prod, ~104 задачи), стабильно 43/43 = 100% на pac1-dev.

Канал автора: [@hack_n_vibe](https://t.me/hack_n_vibe)

## Идея

Это не чат-бот агент, а **тонкий транспорт + Claude Code в качестве мозга**. Никакого собственного LLM-цикла, никаких API ключей OpenAI/Anthropic в самом коде — Claude Code CLI и есть та LLM-петля, которая ведёт диалог.

Агент состоит из четырёх кусков:

1. **`runner.py`** — голый Python без LLM. Только RPC к harness и PCM runtime: `recon`, `exec`, `submit`, `learn`, плюс Run API для соревновательного режима.
2. **`RULES.md`** — паттерн-словарь, накопленный итеративно. 9 stop-rules + 13 паттернов задач (CRM ops, knowledge repo, prompt injection, date math, и т.д.). Подгружается перед каждой задачей.
3. **`.claude/skills/solve-pac1/SKILL.md`** — оркестратор для Claude Code. Раздаёт trial_ids батчами по 10 субагентам, запускает их параллельно, собирает результаты.
4. **Цикл обучения** — после ошибочного ответа на dev-бенчмарке агент сам зовёт `runner.py learn "..."`, дописывает правило в `RULES.md`, и в следующий trial оно уже работает.

## Инструменты

VM-операции через PCM gRPC, оформленные как JSON-команды для `exec`:

```
tree, list, find, search, read, write, delete, mkdir, move, context, answer
```

Каждая мапится 1:1 на gRPC-вызов. Claude собирает цепочку команд → `runner.py exec` диспатчит → результат возвращается JSON'ом обратно в чат. Никакого Python-sandbox внутри VM, никакого посредника.

## Параллелизм

Skill раздаёт `trial_ids` батчами по 10 и запускает параллельные Task-субагенты в одном сообщении. Все ~104 задачи решаются одновременно, каждый субагент получает свежий `RULES.md`. На blind prod retry бесполезен — поэтому ставка на один точный параллельный прогон.

## Agent Loop

```
runner.py run-trial <trial_id>
  ↓ deep_recon: tree(level=3) + context + read всех найденных файлов
  ↓ JSON с инструкцией + всеми файлами + RULES.md
Claude анализирует → собирает доп. exec'и при нужде
  ↓
runner.py submit <url> <trial_id> "<answer>" "<refs>" "<outcome>"
  ↓ vm.answer(...) + client.end_trial(...)
score (на dev) / blind (на prod)
```

В большинстве задач достаточно одного `recon` — Claude видит весь vault разом. Доп. `exec` — только если нужны точные числа из больших файлов (recon их truncate-нет) или подтянуть свежий `context.time`.

## Установка

```bash
# 1. Установить uv (https://docs.astral.sh/uv/)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Зависимости
uv sync

# 3. Получить BitGN API key на https://bitgn.com (профиль)
export BITGN_API_KEY=your-key-here
export BENCH_ID=bitgn/pac1-dev   # или bitgn/pac1-prod для соревнования

# 4. Установить Claude Code (https://docs.claude.com/en/docs/claude-code)
# и скопировать skill:
mkdir -p ~/.claude/skills/solve-pac1
cp .claude/skills/solve-pac1/SKILL.md ~/.claude/skills/solve-pac1/

# 5. Запустить из Claude Code
# /solve-pac1
```

## Команды runner.py

```bash
# Список всех задач бенчмарка
uv run python runner.py all

# Playground режим (для тренировки на dev)
uv run python runner.py recon <task_id>
uv run python runner.py exec <harness_url> '[{"tool":"read","path":"AGENTS.md"}]'
uv run python runner.py submit <harness_url> <trial_id> "answer" "refs1,refs2" OUTCOME_OK

# Run mode (для соревнования)
uv run python runner.py run-start "Hack'n'Vibe https://t.me/hack_n_vibe"
uv run python runner.py run-trial <trial_id>
uv run python runner.py run-status <run_id>
uv run python runner.py run-submit <run_id>

# Обучение
uv run python runner.py rules
uv run python runner.py learn "ПАТТЕРН Lookups: account_manager — сканировать ВСЕ acct файлы"
```

## OUTCOMES

- `OUTCOME_OK` — задача выполнена
- `OUTCOME_DENIED_SECURITY` — отказ по безопасности (prompt injection, leak privacy)
- `OUTCOME_NONE_CLARIFICATION` — нужно уточнение (конфликт docs, неясный отправитель, обрезанная инструкция)
- `OUTCOME_NONE_UNSUPPORTED` — операция не поддерживается vault'ом (HTTP upload, внешний API)
- `OUTCOME_ERR_INTERNAL` — внутренняя ошибка

## Слабые места

По логам с pac1-prod самые частые грабли:

- **Даты от `context.time`** — агент берёт дату из файла (`due_on`, `next_follow_up_on`) вместо `context.time` из текущего trial
- **Counting через recon** — recon truncate-ает файлы, агент видит 3 строки из 1000 → отвечает "1" вместо 810
- **Manager lookup** — забывает сканировать все `acct_*.json`, ловит только `mgr_*.json`
- **Конфликт docs vs CLARIFICATION** — Claude склонен "выбрать" один вариант вместо CLARIFICATION

Все они теперь явно прописаны в стоп-листе [RULES.md](RULES.md).

## Структура

```
.
├── runner.py                              # тонкий Python-транспорт
├── RULES.md                               # паттерн-словарь
├── pyproject.toml                         # uv deps (BitGN SDK с buf.build)
└── .claude/
    └── skills/
        └── solve-pac1/
            └── SKILL.md                   # оркестратор для Claude Code
```

## Ключевые принципы

- **Простой набор инструментов** — никакого `execute_code`, только VM-нативные операции
- **Растущий чеклист** — `RULES.md` накапливается через `learn`, переиспользуется между прогонами
- **Параллелизм** — батчи по 10 задач одновременно
- **Чистая разводка** — Python только для транспорта, LLM (Claude Code) — для всех решений

## Лицензия

MIT — см. [LICENSE](LICENSE).
