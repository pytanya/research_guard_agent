"""
Guardrails исследовательского агента.

1. Prompt-injection фильтр на входе (эвристики).
2. Валидация финального ответа (answer/sources/confidence).
3. Circuit breaker: 3 ошибки подряд LLM/инструмента → fail closed.
4. Бюджет: MAX_COST_USD (проверка в цикле агента).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# Эвристики prompt-injection (регистронезависимые)
INJECTION_PATTERNS: List[str] = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"ignore\s+(all\s+)?prior\s+instructions",
    r"disregard\s+(all\s+)?previous\s+instructions",
    r"system\s+prompt",
    r"system\s+message",
    r"developer\s+message",
    r"initial\s+instructions",
    r"forget\s+(all\s+)?previous\s+instructions",
    r"you\s+are\s+now\s+an?\s+unfiltered",
    r"you\s+are\s+now\s+an?\s+uncensored",
    r"скажи\s+как\s+системный\s+промпт",
    r"забудь\s+(все\s+)?предыдущие\s+инструкции",
    r"игнорируй\s+(все\s+)?предыдущие\s+инструкции",
    r"проигнорируй\s+(все\s+)?предыдущие\s+инструкции",
    r"игнорируй\s+свой\s+системный",
    r"покажи\s+свой\s+системный\s+промпт",
    r"раскрой\s+свой\s+промпт",
    r"do\s+anything\s+now",
    r"act\s+as\s+an?\s+unrestricted",
    r"jailbreak",
    r"dан\s+нou\s+new\s+instructions",
    r"new\s+instructions?\s+override",
]


def check_prompt_injection(text: str) -> Dict[str, Any]:
    """
    Проверка входа на prompt-injection.

    Returns:
        {"injection": bool, "matched": [паттерны], "confidence": 0..1}
    """
    text_lower = (text or "").lower()
    matched = []
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text_lower):
            matched.append(pattern)
    if matched:
        return {
            "injection": True,
            "matched": matched,
            "confidence": min(0.5 + 0.15 * len(matched), 0.98),
        }
    return {"injection": False, "matched": [], "confidence": 0.0}


def validate_answer(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Валидация финального ответа.

    Требования: answer — непустая строка; sources — непустой список;
    confidence — число 0..1; summary — непустая строка.

    Returns:
        {"valid": bool, "errors": [str], "data": result}
    """
    errors: List[str] = []

    answer = result.get("answer", "")
    if not isinstance(answer, str) or not answer.strip():
        errors.append("answer пустой или не строка")

    sources = result.get("sources", [])
    if not isinstance(sources, list) or len(sources) == 0:
        errors.append("sources пустой или не список")
    else:
        bad = [s for s in sources if not isinstance(s, str) or not s.strip()]
        if bad:
            errors.append("sources содержит пустые/невалидные элементы")

    confidence = result.get("confidence", None)
    if confidence is None:
        errors.append("confidence отсутствует")
    elif not isinstance(confidence, (int, float)):
        errors.append("confidence не число")
    else:
        try:
            conf = float(confidence)
            if not (0.0 <= conf <= 1.0):
                errors.append("confidence вне диапазона 0..1")
        except (TypeError, ValueError):
            errors.append("confidence не конвертируется в число")

    summary = result.get("summary", "")
    if not isinstance(summary, str) or not summary.strip():
        errors.append("summary пустой или не строка")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "data": result,
    }


class CircuitBreaker:
    """
    Circuit breaker: при N ошибок подряд — fail closed.

    - record_success(): сбрасывает счётчик.
    - record_failure(): увеличивает счётчик; если достигнут порог — открывает цепь.
    - is_open(): цепь открыта (все дальнейшие вызовы запрещены).
    """

    def __init__(self, failure_threshold: int = 3) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self._consecutive_failures = 0
        self.open = False

    def record_success(self) -> None:
        self._consecutive_failures = 0

    def record_failure(self) -> None:
        if self.open:
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold:
            self.open = True

    def is_open(self) -> bool:
        return self.open

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def reset(self) -> None:
        self._consecutive_failures = 0
        self.open = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "open": self.open,
            "consecutive_failures": self._consecutive_failures,
            "failure_threshold": self.failure_threshold,
        }
