# ResearchGuardAgent — исследовательский агент с observability и guardrails

Домашнее задание модуля «Контроль качества агента» (уроки **Evals/Observability** и **Guardrails**).

Один LLM-агент с циклом **Reason → Act → Observe**:
- планирует исследование,
- вызывает инструменты (`search_web`, `fetch_url`, `save_note`),
- анализирует результаты,
- повторяет до готовности,
- формирует финальный ответ со структурой `{answer, sources[], confidence, summary}`.

**Модель по умолчанию:** `qwen/qwen3.7-flash` через **RouterAI** (primary) + **OpenRouter** (fallback).
Цены RouterAI в рублях: qwen3.7-flash 3,10₽/13₽ за 1M; deepseek-v4-flash 9₽/18₽ за 1M
(конвертируются в USD для метрик бюджета через `RUB_TO_USD_RATE`).

## Возможности

| Блок | Что делает |
|---|---|
| LLM | OpenAI-совместимый SDK, **OpenRouter** (primary) + **RouterAI** (fallback), модель `deepseek/deepseek-v4-flash` (переопределяется `RESEARCHER_MODEL`/`LLM_MODEL`), retry с backoff (3 попытки: 1→2→4с), обработка 429/5xx/таймаутов |
| Поиск | **Yandex Search API v2** → fallback **DuckDuckGo (DDGS)** → fallback **Tavily** (только если ключ задан) |
| Инструменты | `search_web`, `fetch_url` (таймаут 15с, лимит 8000 символов), `save_note` |
| Метрики | `success`, `elapsed_sec`, `total_cost_usd`, `num_steps`, per-step метрики (инструмент, длительность, статус) |
| Логирование | Rich-консоль + JSONL-лог шагов в `logs/run_<timestamp>.jsonl` + `output/run_<timestamp>/run.log` |
| Phoenix (этап 2) | `phoenix.launch_app()` + OpenInference-инструментирование OpenAI — трассы LLM-вызовов видны в UI на http://localhost:6006 |
| Guardrails (этап 2) | лимит бюджета `MAX_COST_USD`, валидация финального ответа, фильтр prompt-injection, circuit breaker (3 ошибки подряд → fail closed) |
| Evals (этап 2) | LLM-as-judge (score 0..10 + вердикт), `eval_golden.py` по golden set, отчёты в `evals/` |

## Установка

```bash
# 1) venv
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# 2) зависимости
pip install -r requirements.txt

# 3) конфигурация
copy .env.example .env   # и заполните ключи (или скопируйте готовый .env из соседнего проекта)
```

## Запуск

```bash
python main.py "Кто такой Альберт Эйнштейн?"
python main.py "Что такое Langfuse?" --max-steps 4 --max-cost 0.3
python main.py "вопрос" --no-phoenix
```

Аргументы:

| Аргумент | Назначение | Default |
|---|---|---|
| `question` (позиционный) | Вопрос для исследования | — |
| `--max-steps N` | Максимум итераций цикла | `MAX_STEPS` (5) |
| `--max-cost USD` | Максимальный бюджет прогона | `MAX_COST_USD` (0.5) |
| `--no-phoenix` | Отключить запуск Phoenix | `PHOENIX_ENABLED` |

## Результаты прогона

После запуска в `output/run_<timestamp>/` создаются:
- `answer.md` — финальный ответ агента (answer, sources, confidence, summary);
- `run.log` — файловый лог;
- `dz_report.md` — сводка по ДЗ (этап 2).

JSONL-лог шагов: `logs/run_<timestamp>.jsonl`.

## Phoenix (observability)

```bash
python main.py "Что такое OpenTelemetry?"   # без --no-phoenix
# откройте http://localhost:6006 — трейсы LLM-вызовов и инструментов
```

Если Phoenix не установлен или упал — агент продолжит работу без трассировки (graceful degrade).

> **Известная проблема на Windows:** Phoenix SDK 19.x требует `greenlet` (для SQLAlchemy async).
> На Python 3.12 версия `greenlet 3.5.4` падает с `DLL load failed`, поэтому в
> `requirements.txt` зафиксирована рабочая `greenlet==3.1.1`. Также Phoenix хранит БД во
> временной папке и на OneDrive-путях может падать на очистке temp (`PermissionError`).
> В этом случае агент работает без UI (graceful degrade), трейсы не теряются — метрики
> и JSONL-логи шагов сохраняются всегда.

## Evals (этап 2)

```bash
python eval_golden.py            # прогон по golden set (5-10 вопросов)
```

Результаты: `evals/results_eval_<timestamp>.json` и `evals/report_<timestamp>.md`.

## Структура

```
research_guard_agent/
├── .env / .env.example
├── requirements.txt
├── README.md
├── main.py                  # CLI
├── src/
│   ├── config.py            # pydantic Settings
│   ├── llm_client.py        # OpenRouter + RouterAI, retry, fallback, cost
│   ├── tools.py             # search_web / fetch_url / save_note
│   ├── agent.py             # цикл Reason → Act → Observe
│   ├── metrics.py           # MetricsCollector
│   ├── logging_setup.py     # rich + JSONL + run.log
│   └── (этап 2) guardrails.py, judge.py
├── evals/                   # golden set + результаты eval
├── logs/                    # JSONL-логи шагов
└── output/                  # run-папки с answer.md и отчётами
```

## Переменные окружения

Все ключи читаются из `.env` (см. `.env.example`). Ключи в коде не хардкодятся.

| Переменная | Назначение |
|---|---|
| `OPENROUTER_API_KEY` / `OPENROUTER_BASE_URL` | Primary LLM-провайдер |
| `ROUTERAI_API_KEY` / `ROUTERAI_BASE_URL` | Fallback LLM-провайдер |
| `LLM_MODEL` / `RESEARCHER_MODEL` | Модель |
| `YANDEX_API_KEY` / `YANDEX_FOLDER_ID` | Yandex Search API |
| `TAVILY_API_KEY` | Tavily (опциональный fallback поиска) |
| `MAX_STEPS` | Лимит шагов (5) |
| `MAX_COST_USD` | Лимит бюджета (0.5) |
| `REQUEST_TIMEOUT` | Таймаут LLM-запросов, сек (30) |
| `PHOENIX_ENABLED` | Запускать Phoenix (true) |
| `COST_PER_1M_PROMPT` / `COST_PER_1M_COMPLETION` | Цены для расчёта стоимости, если провайдер не вернул `total_cost` |
