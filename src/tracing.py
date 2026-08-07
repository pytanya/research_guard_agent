"""
Кастомные OpenInference-спаны для наблюдаемости в Phoenix.

Помимо авто-трассировки LLM-вызовов (OpenAIInstrumentor) создаём собственные
спаны, чтобы в UI Phoenix был виден весь конвейер агента:

- `agent.run`            — корневой спан прогона (вопрос, ответ, request_id);
- `guardrail.*`          — проверки (prompt-injection, контент-фильтр, валидация);
- `tool.*`               — вызовы инструментов (search_web, fetch_url, save_note).

Спаны безопасны без коллектора: если Phoenix не зарегистрирован
(register() не вызывался), OTel использует no-op TracerProvider и спаны
никуда не экспортируются.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional, Union

from opentelemetry import trace
from openinference.semconv.trace import (
    OpenInferenceSpanKindValues,
    SpanAttributes,
)

TRACER_NAME = "research-guard-agent"

_JSON_MIME = "application/json"


def _as_text(value: Any, limit: int = 8000) -> str:
    """Приводит значение к строке для атрибутов спана (с ограничением длины)."""
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)[:limit]
        except (TypeError, ValueError):
            return str(value)[:limit]
    return str(value)[:limit]


@contextmanager
def span(
    name: str,
    *,
    kind: Union[str, OpenInferenceSpanKindValues],
    attributes: Optional[Dict[str, Any]] = None,
    input_value: Optional[Any] = None,
    output_value: Optional[Any] = None,
    mime_type: str = _JSON_MIME,
) -> Iterator[trace.Span]:
    """
    Контекстный менеджер кастомного OpenInference-спана.

    Args:
        name: имя спана (например "guardrail.prompt_injection").
        kind: OpenInference span kind (AGENT / TOOL / GUARDRAIL / ...).
        attributes: произвольные атрибуты (например request_id).
        input_value: вход (промпт/вопрос/аргументы) — кладётся в input.value.
        output_value: выход (результат проверки/инструмента) — output.value.
        mime_type: MIME-тип для input/output.
    """
    oi_kind = kind.value if isinstance(kind, OpenInferenceSpanKindValues) else str(kind)
    with trace.get_tracer(TRACER_NAME).start_as_current_span(
        name, kind=trace.SpanKind.INTERNAL
    ) as sp:
        sp.set_attribute(SpanAttributes.OPENINFERENCE_SPAN_KIND, oi_kind)
        if attributes:
            sp.set_attributes(attributes)
        if input_value is not None:
            sp.set_attribute(SpanAttributes.INPUT_VALUE, _as_text(input_value))
            sp.set_attribute(SpanAttributes.INPUT_MIME_TYPE, mime_type)
        if output_value is not None:
            sp.set_attribute(SpanAttributes.OUTPUT_VALUE, _as_text(output_value))
            sp.set_attribute(SpanAttributes.OUTPUT_MIME_TYPE, mime_type)
        yield sp
