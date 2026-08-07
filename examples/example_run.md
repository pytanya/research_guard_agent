# Пример запроса и результата

Реальный прогон агента: `output/run_20260806_122125/` (папка и JSONL-лог
`logs/run_20260806_122125.jsonl`), вопрос из golden set.

## Запрос

```
python main.py "Что такое OpenTelemetry?"
```

Конфигурация прогона:

| Параметр | Значение |
|---|---|
| Модель-исследователь | `qwen/qwen3.7-flash` |
| Модель-судья | `google/gemini-3.5-flash-lite` |
| Провайдеры | routerai (primary), openrouter (fallback) |
| request_id | `req_20260806_122125_f5511859` |
| Phoenix | включён (UI http://localhost:6006) |
| Лимит шагов | `MAX_STEPS` |
| Бюджет | `MAX_COST_USD = $0.5` |

## Результат

### Метрики

| Метрика | Значение |
|---|---|
| success | `True` |
| stop_reason | `completed` |
| elapsed | 78.27 c |
| total_cost_usd | $0.000282 |
| num_steps | 2 |
| num_llm_calls | 2 |
| total_tokens | 4350 |

### Лог шагов

| # | Инструмент | Статус | Длительность, с | Стоимость, $ |
|---|------------|--------|-----------------|-------------|
| 1 | search_web | OK | 13.297 | 0.0 |
| 2 | search_web | OK | 2.282 | 0.0 |

### Финальный ответ

> OpenTelemetry (OTel) — это открытый стандарт и комплексный набор инструментов для сбора телеметрических данных из приложений, позволяющий обеспечить полноту наблюдаемости системы (observability). Проект находится под эгидой Cloud Native Computing Foundation (CNCF) и был создан в результате объединения двух популярных ранее инициатив — OpenTracing и OpenCensus.
>
> Основная цель OpenTelemetry заключается в том, чтобы предоставить единый универсальный способ сбора метрик, логов и распределённой трассировки (traces) независимо от языка программирования, используемого фреймворка или конкретной облачной инфраструктуры. Это решает проблему фрагментации в сфере мониторинга, когда разработчикам приходилось использовать множество различных решений для разных задач.
>
> OpenTelemetry предоставляет стандартизированный API и SDK для инструментирования приложений. Эти компоненты позволяют генерировать три основных типа сигналов: трассировки (показывающие путь запроса через микросервисы), метрики (цифровые показатели производительности) и логи (текстовые записи о событиях). Данные затем могут быть отправлены в любую систему хранения или аналитики.

**confidence:** 0.95, **sources:**
- https://opentelemetry.io/docs/what-is-opentelemetry/
- https://tproger.ru/articles/chto-takoe-opentelemetry-i-kak-ona-mozhet-uluchwit-kachestvo-vawih-servisov
- https://statuser.cloud/blog/chto-takoe-opentelemetry-i-kak-sobirat-metriki-i-logi-iz-prilozheniy

### Guardrails

- **prompt-injection:** `{'injection': False, 'matched': [], 'confidence': 0.0}`
- **валидация ответа:** `{'valid': True, 'errors': [], ...}`
- **circuit breaker:** `{'state': 'closed', 'open': False, 'consecutive_failures': 0, 'failure_threshold': 3}`

### Оценка судьи (LLM-as-judge)

- **score:** 10.0/10
- **verdict:** excellent
- **criteria:** `{'factual_accuracy': 10, 'completeness': 10, 'structure': 10}`
- **comment:** «Ответ полностью точен, подробно раскрывает тему (включая историю создания и основные сигналы), а также строго соответствует требуемой структуре.»

### Что лежит в `output/run_<timestamp>/`

```
output/run_20260806_122125/
├── answer.md        # финальный ответ + sources + confidence
├── dz_report.md     # метрики, лог шагов, guardrails, verdict судьи
└── run.log          # подробный лог прогона
```

Связка артефактов: по `request_id` `req_20260806_122125_f5511859` из шапки
`answer.md` записи того же запроса находятся в `logs/run_20260806_122125.jsonl`.

## Пример неуспешного исхода (для сравнения)

Когда все провайдеры недоступны или превышен `MAX_STEPS`, агент возвращает частичный результат с явным `stop_reason`:

```
**confidence:** 0.2
**success:** False
**stop_reason:** max_steps_exceeded
```

и списком собранных источников — вместо молчаливого обрыва или частичного ответа без пометки.
