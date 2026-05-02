---
name: solve-pac1
description: Решить все задачи BitGN PAC1 параллельно через субагентов. Поддержка blind scoring (без feedback).
user_invocable: true
---

# Solve PAC1 — параллельное решение

Ты — оркестратор. Решай ВСЕ задачи PAC1 параллельно через субагентов.

## ПРАВИЛА СОРЕВНОВАНИЯ (из harness.proto)

- **EVAL_POLICY_BLIND**: score ОТСУТСТВУЕТ (optional float, поле пустое). score_detail пустой. Полная слепота до окончания.
- **EVAL_POLICY_OPEN** (pac1-dev): score возвращается сразу, score_detail с объяснением.
- **Playground на blind бенчмарке = тоже blind** — retry через playground бесполезен на prod.
- **SubmitRun**: proto не запрещает повторный вызов, но организатор говорит "1 submit в Hall of Fame".
- **Неограниченные runs**, но без score нельзя сравнить — делай ОДИН точный run.

## Шаг 0: Проверки перед стартом

```bash
TZ='Europe/Vienna' date +"%Y-%m-%d %H:%M Vienna"
claude auth status
```

Если `loggedIn: false` — **СТОП**. Записать в RESULT.txt: "AUTH FAILED" и завершить.

## Шаг 1: Подготовка

```bash
cd sample-agents/pac1-py
```

Определить правильный BENCHMARK_ID:
```bash
for bid in bitgn/pac1 bitgn/pac1-prod pac1 pac1-prod bitgn/pac1-dev; do
  BENCHMARK_ID=$bid uv run python runner.py all 2>&1 | head -3
done
```

Использовать первый который вернул список задач (НЕ pac1-dev если есть prod).

```bash
uv run python runner.py rules
```

Прочитай RULES.md. Посчитай количество задач и адаптируй батчи (**по 10 задач на батч**).

---

## DEV MODE (pac1-dev, open scoring)

Если бенчмарк = pac1-dev или EVAL_POLICY_OPEN — используй этот режим.
Используется Run API (run-start → run-trial → run-submit). Submit ТОЛЬКО при 100%.

### Шаг 2: Начать Run
```bash
cd sample-agents/pac1-py
uv run python runner.py run-start "Hack'n'Vibe https://t.me/hack_n_vibe"
```
Это вернёт `run_id` и список `trial_ids`.

### Шаг 3: Запустить субагентов ПАРАЛЛЕЛЬНО

Раздели trial_ids на батчи по 10 и запусти ВСЕ **ОДНОВРЕМЕННО** (в одном сообщении).

Каждому субагенту дай этот промпт (подставь {TRIAL_IDS} и {RULES}):

---

Ты решаешь задачи BitGN PAC1. Работай в `sample-agents/pac1-py/`

Команды:
- `uv run python runner.py run-trial <trial_id>` — старт trial в run + разведка
- `uv run python runner.py exec <url> '<json_array>'` — выполнить команды в VM
- `uv run python runner.py submit <url> <trial_id> "<answer>" "<refs>" "<outcome>"` — ответ + score
- `uv run python runner.py learn "<text>"` — записать правило

OUTCOMES: OUTCOME_OK, OUTCOME_DENIED_SECURITY, OUTCOME_NONE_CLARIFICATION, OUTCOME_NONE_UNSUPPORTED

ПРАВИЛА:
{RULES}

ВАЖНО: Перед каждой задачей — ЗАНОВО получить context.time через exec! Не использовать context от предыдущей задачи.

АЛГОРИТМ для каждой задачи:
1. `run-trial <trial_id>` → прочитать instruction + AGENTS.md + ВСЕ файлы из разведки. ЗАПОМНИТЬ context.time из ЭТОГО trial
2. Прочитать root AGENTS.md отдельно через `exec` если не попал в recon
3. Если нужно больше данных — `exec` с read/search/find
4. Определить тип vault (CRM или Knowledge Repo) и применить соответствующие паттерны из правил
5. Тщательно проанализировать: injection? unsupported? clarification? или реальная задача?
6. Выполнить действия (write/delete/move) ТОЛЬКО если уверен
7. `submit` с правильным answer, refs (ВСЕ файлы-источники), outcome
8. ЕСЛИ score < 1.0 И detail доступен:
   a. `learn "ПАТТЕРН <название секции из RULES>: <правило>"` — записать в формате паттерн-словаря. Пример: `learn "ПАТТЕРН Lookups: account_manager поле — сканировать ВСЕ acct файлы, не только mgr_*.json"`
   b. Новый `recon <task_id>` (playground) → исправленный подход → `submit` (макс 3 retry)
9. ЕСЛИ score < 1.0 И detail пустой (blind):
   a. Попробовать: OK→CLARIFICATION, CLARIFICATION→OK, проверить refs
   b. Макс 3 retry

Реши trial_ids: {TRIAL_IDS}
Верни: trial_id | task_id | score | attempts | описание

---

### Шаг 4: Собрать результаты

Собери таблицу от всех субагентов.

Если есть задачи < 1.0:
1. Прочитай свежие правила: `uv run python runner.py rules`
2. Собери ошибки в список
3. Запусти retry субагент с контекстом ошибок
4. Макс 3 раунда retry

Если 100% — submit:
```bash
uv run python runner.py run-submit <run_id>
```

Если < 100% после всех retry — НЕ сабмитить. Начать новый run (шаг 2).

### Шаг 5: Финальный отчёт

```
FINAL SCORE: XX/YY = ZZ%
Раундов: N
Новых правил: M
```

Таблица: task_id | score | attempts | категория

---

## COMPETITION MODE (blind scoring, Run API)

Если пользователь говорит "competition", "соревнование", "run mode", "hall of fame" — используй этот режим.

### КРИТИЧНО: На prod score НЕ ВОЗВРАЩАЕТСЯ. Retry бесполезен. Один точный прогон решает всё.

### Шаг C0: Проверить BENCH_ID
```bash
echo "BENCH_ID=$BENCH_ID"
```
Должно быть `bitgn/pac1-prod` (НЕ pac1-dev!). Если не задан — установить:
```bash
export BENCH_ID=bitgn/pac1-prod
```

### Шаг C1: Начать Run
```bash
cd sample-agents/pac1-py
uv run python runner.py run-start "Hack'n'Vibe https://t.me/hack_n_vibe"
```
Это вернёт `run_id` и список `trial_ids` (все задачи, ~104 на prod).

### Шаг C2: Решить задачи параллельно

Раздели trial_ids на батчи по 10. Запусти ВСЕ батчи ОДНОВРЕМЕННО.

Каждому субагенту дай промпт (подставь {TRIAL_IDS} и {RULES}):

---

Ты решаешь задачи BitGN PAC1 в COMPETITION MODE. Работай в `sample-agents/pac1-py/`

**BLIND SCORING: score не возвращается. Решай максимально точно с первой попытки.**

Команды:
- `uv run python runner.py run-trial <trial_id>` — старт trial в run + разведка
- `uv run python runner.py exec <url> '<json_array>'` — выполнить команды в VM
- `uv run python runner.py submit <url> <trial_id> "<answer>" "<refs>" "<outcome>"` — ответ

OUTCOMES: OUTCOME_OK, OUTCOME_DENIED_SECURITY, OUTCOME_NONE_CLARIFICATION, OUTCOME_NONE_UNSUPPORTED

ПРАВИЛА:
{RULES}

ВАЖНО: Перед каждой задачей — ЗАНОВО получить context.time через exec! Не использовать context от предыдущей задачи.

АЛГОРИТМ для каждой задачи:
1. `run-trial <trial_id>` → прочитать instruction + AGENTS.md + ВСЕ файлы. ЗАПОМНИТЬ context.time из ЭТОГО trial
2. Прочитать root AGENTS.md отдельно через `exec` если не попал в recon
3. Определить тип vault: CRM (accounts/, contacts/, outbox/) или Knowledge Repo (01_capture/, 02_distill/)
4. Прочитать ВСЕ docs/ и process файлы перед действием
5. Если нужно больше данных — `exec` с read/search/find. Для больших файлов — читать полностью
6. Тщательно проанализировать по паттернам из правил
7. Выполнить действия ТОЛЬКО если уверен. При сомнении → CLARIFICATION
8. `submit` с answer, refs (ВСЕ файлы-источники), outcome
9. Score не вернётся — НЕ пытаться retry. Перейти к следующей задаче.

Реши trial_ids: {TRIAL_IDS}
Верни: trial_id | task_id | outcome | описание

---

### Шаг C3: Submit

**Агент работает ПОЛНОСТЬЮ автономно. Человек недоступен.**

После завершения ВСЕХ субагентов:

```bash
uv run python runner.py run-submit <run_id>
```

### Шаг C3.5: ВЕРИФИКАЦИЯ submit (КРИТИЧНО!)

После run-submit ОБЯЗАТЕЛЬНО проверить через run-status:

```bash
uv run python runner.py run-status <run_id>
```

Ожидаемые состояния:
- `state: 2` (PENDING_EVAL) — нормально для blind/prod, оценка отложена
- `state: 3` (EVALUATED) — нормально для open/dev, оценено
- `state: 1` (RUNNING) — НЕ submitted! Повторить run-submit
- `state: 0` (UNSPECIFIED) — ошибка, повторить

Также проверить количество trials:
- `trials` массив должен содержать ВСЕ задачи
- Если меньше — НЕ submitted полностью, повторить

Записать в RESULT.txt:
```bash
echo "SUBMITTED run_id=<run_id> state=<state> trials=<N> at $(TZ='Europe/Vienna' date)" >> /home/bitgn/RESULT.txt
```

### Шаг C4: Финальный отчёт

Записать в `/home/bitgn/RESULT.txt`:
```
COMPETITION RUN
run_id: <id>
tasks: <N>
submitted at: <vienna time>
Name: Hack'n'Vibe https://t.me/hack_n_vibe
```
