# Отчёт по ДЗ: ResearchGuardAgent

- **Вопрос:** Что такое Langfuse?
- **Модель:** qwen/qwen3.7-flash
- **Провайдеры:** routerai, openrouter
- **Phoenix:** не включён

## Метрики

- success: **False**
- stop_reason: `error: Все провайдеры и модели недоступны: routerai/qwen/qwen3.7-flash: Connection error.; openrouter/qwen/qwen3.7-flash: Connection error.`
- elapsed_sec: **17.141 с**
- total_cost_usd: **$0**
- num_steps: **0**
- num_llm_calls: **0**
- total_tokens: **0**

## Лог шагов

| # | Инструмент | Статус | Длительность, с | Стоимость, $ |
|---|------------|--------|-----------------|-------------|

## Guardrails

- **prompt-injection:** {'injection': False, 'matched': [], 'confidence': 0.0}
- **валидация ответа:** {'valid': False, 'errors': ['sources пустой или не список'], 'data': {'answer': 'Не удалось завершить исследование в рамках лимита шагов. Ниже — предварительные данные, собранные в ходе поиска.', 'sources': [], 'confidence': 0.2, 'summary': 'Исследование прервано по лимиту шагов/бюджета; требуется повторный запуск с большим лимитом.'}}
- **circuit breaker:** {'open': False, 'consecutive_failures': 1, 'failure_threshold': 3}
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
