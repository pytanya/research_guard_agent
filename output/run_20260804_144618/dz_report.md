# Отчёт по ДЗ: ResearchGuardAgent

- **Вопрос:** Что такое Langfuse?
- **Модель:** qwen/qwen3.7-flash
- **Провайдеры:** routerai, openrouter
- **Phoenix:** не включён

## Метрики

- success: **False**
- stop_reason: `max_steps_exceeded`
- elapsed_sec: **118.375 с**
- total_cost_usd: **$0.000246**
- num_steps: **5**
- num_llm_calls: **3**
- total_tokens: **5258**

## Лог шагов

| # | Инструмент | Статус | Длительность, с | Стоимость, $ |
|---|------------|--------|-----------------|-------------|
| 1 | search_web | OK | 7.75 | 0.0 |
| 2 | search_web | OK | 2.907 | 0.0 |
| 3 | fetch_url | OK | 3.015 | 0.0 |
| 4 | fetch_url | OK | 2.375 | 0.0 |
| 5 | save_note | OK | 0.0 | 0.0 |

## Guardrails

- **prompt-injection:** {'injection': False, 'matched': [], 'confidence': 0.0}
- **валидация ответа:** {'valid': True, 'errors': [], 'data': {'answer': 'Не удалось завершить исследование в рамках лимита шагов. Ниже — предварительные данные, собранные в ходе поиска.', 'sources': ['https://langfuse.com/', 'https://habr.com/ru/companies/datafeel/articles/931500/', 'https://dzen.ru/a/Z2JifQufQEC7kpHQ', 'https://clickhouse.com/docs/ru/products/cloud/features/ai-ml/langfuse', 'https://github.com/langfuse/langfuse', 'https://langfuse.com/docs/observability/overview', 'https://habr.com/ru/articles/1008300/', 'https://www.youtube.com/watch?v=2E8iTvGo9Hs'], 'confidence': 0.2, 'summary': 'Исследование прервано по лимиту шагов/бюджета; требуется повторный запуск с большим лимитом.'}}
- **circuit breaker:** {'open': False, 'consecutive_failures': 0, 'failure_threshold': 3}
- **бюджет (MAX_COST_USD):** $ 0.5

## LLM-as-judge

- **score:** None/10
- **verdict:** None
- **criteria:** None
- **comment:** None

## Phoenix (что видно в UI)

Phoenix не запускался в этом прогоне (--no-phoenix).

## Финальный ответ

Не удалось завершить исследование в рамках лимита шагов. Ниже — предварительные данные, собранные в ходе поиска.

### sources
- https://langfuse.com/
- https://habr.com/ru/companies/datafeel/articles/931500/
- https://dzen.ru/a/Z2JifQufQEC7kpHQ
- https://clickhouse.com/docs/ru/products/cloud/features/ai-ml/langfuse
- https://github.com/langfuse/langfuse
- https://langfuse.com/docs/observability/overview
- https://habr.com/ru/articles/1008300/
- https://www.youtube.com/watch?v=2E8iTvGo9Hs
