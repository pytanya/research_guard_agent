"""
Guardrails исследовательского агента.

1. Prompt-injection фильтр на входе (эвристики).
2. Контент-фильтр на входе: ненормативная лексика, оскорбления, жаргонизмы.
3. Валидация финального ответа (answer/sources/confidence).
4. Circuit breaker: 3 ошибки подряд LLM/инструмента → fail closed,
   с half-open cooldown (30с) для автоматического восстановления.
5. Бюджет: MAX_COST_USD (проверка в цикле агента).
"""

from __future__ import annotations

import re
import time
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
    r"new\s+instructions?\s+override",
    r"не\s+выполняй\s+(свои|системные|предыдущие)\s+инструкции",
    r"не\s+следуй\s+(своим|системным|предыдущим)\s+инструкциям",
    r"удали\s+(все\s+)?предыдущие\s+инструкции",
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


# ----------------------------------------------------------------------
# Контент-фильтр: ненормативная лексика / оскорбления / жаргонизмы
# ----------------------------------------------------------------------
# Категории и эвристики (регистронезависимые, применяются к lowercased тексту).
# Подбор паттернов: характерные основы/слова, чтобы не блокировать обычные запросы.
CONTENT_PATTERNS: Dict[str, List[str]] = {
    "profanity": [
        # русский мат и грубая лексика
        r"ху(й|я|е|ё|и|ю)",
        r"пизд",
        r"бля(д|ть|ха|дь)?\b",
        r"еб(ал|ат|ан|у|ёт|ёш|ет|ну)",
        r"ёб",
        r"залуп",
        r"муд(ак|ац)",
        r"говн",
        r"гандон",
        r"пидор",
        r"сук(а|ин)",
        r"шлюх",
        r"долбо",
        r"оху(й|ен|ев|ел)",
        r"сос(ать|у|ёт|ёшь|и|ить)",
        r"проститут",
        r"выбляд",
        r"нахер",
        # английский мат
        r"\bfuck(ing|er|ed|s|u)?\b",
        r"\bshit(t?y|head)?\b",
        r"\bbitch(es)?\b",
        r"\basshole(s)?\b",
        r"\bdick(head|s)?\b",
        r"\bcunt(s)?\b",
        r"\bmotherfuck(er|ing)?\b",
        r"\bwhore(s)?\b",
        r"\bslut(s)?\b",
        r"\bbastard(s)?\b",
        r"\bcocksucker(s)?\b",
        r"\bpussy\b",
        r"\bwanker(s)?\b",
        r"\btwat(s)?\b",
        r"\bbullshit\b",
    ],
    "insults": [
        # русские оскорбления
        r"идиот",
        r"дебил",
        r"кретин",
        r"придурок",
        r"недоумок",
        r"имбицил",
        r"урод",
        r"тупиц",
        r"олух",
        r"мразь",
        r"твар(ь|ью)",
        r"сволоч",
        r"скотин",
        # английские оскорбления
        r"\bidiot(s)?\b",
        r"\bmoron(s)?\b",
        r"\bimbecile(s)?\b",
        r"\bretard(ed|s|ation)?\b",
        r"\bstupid\b",
    ],
    "offensive_slang": [
        # жаргонизмы и оскорбительный сленг
        r"быдло",
        r"чмо\b",
        r"лох(и|и)?\b",
        r"гопник",
        r"алкаш",
        r"шмара",
        r"шалаву?",
        r"черномазый",
        r"чухна",
        r"чурка",
        r"хач",
        r"\bjerk(s)?\b",
        r"\bscumbag(s)?\b",
        r"\bjackass(es)?\b",
        r"\bschmuck(s)?\b",
        r"\bposer(s)?\b",
    ],
}


def check_inappropriate_content(text: str) -> Dict[str, Any]:
    """
    Контент-фильтр входа: ненормативная лексика, оскорбления, жаргонизмы.

    Returns:
        {"blocked": bool, "categories": {cat: [паттерны]},
         "matched": [паттерны], "confidence": 0..1}
    """
    text_lower = (text or "").lower()
    matched: List[str] = []
    categories: Dict[str, List[str]] = {}
    for category, patterns in CONTENT_PATTERNS.items():
        hits = [p for p in patterns if re.search(p, text_lower)]
        if hits:
            categories[category] = hits
            matched.extend(hits)
    if matched:
        return {
            "blocked": True,
            "categories": categories,
            "matched": matched,
            "confidence": min(0.5 + 0.15 * len(matched), 0.98),
        }
    return {
        "blocked": False,
        "categories": {},
        "matched": [],
        "confidence": 0.0,
    }


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
    Circuit breaker: при N ошибок подряд — fail closed, с half-open cooldown.

    Состояния:
      - closed: нормальная работа, ошибки считаются.
      - open:  цепь разомкнута, все вызовы блокируются (fail closed).
      - half-open: после cooldown-таймера — один пробный вызов.
        Если успешен → closed; ошибка → open (снова).

    - record_success(): сбрасывает счётчик; переводит half-open → closed.
    - record_failure(): увеличивает счётчик; если порог достигнут → open.
    - is_open(): цепь открыта (все дальнейшие вызовы запрещены).
    """

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 30.0) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(1.0, cooldown_seconds)
        self._consecutive_failures = 0
        self._state: str = "closed"  # closed | open | half-open
        self._last_failure_time: float = 0.0

    def record_success(self) -> None:
        if self._state == "half-open":
            # Пробный вызов успешен → закрываем цепь
            self._state = "closed"
            self._consecutive_failures = 0
        elif self._state == "closed":
            self._consecutive_failures = 0

    def record_failure(self) -> None:
        if self._state == "half-open":
            # Пробный вызов не удался → снова open
            self._state = "open"
            self._last_failure_time = time.monotonic()
            return
        if self._state == "open":
            return
        # closed
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold:
            self._state = "open"
            self._last_failure_time = time.monotonic()

    def is_open(self) -> bool:
        """Проверяет, открыта ли цепь (с учётом half-open cooldown)."""
        if self._state == "closed":
            return False
        if self._state == "open":
            # Проверяем cooldown
            if time.monotonic() - self._last_failure_time >= self.cooldown_seconds:
                self._state = "half-open"
                # half-open считается «не открытым» — пробный вызов разрешён
                return False
            return True
        # half-open — не блокируем
        return False

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def state(self) -> str:
        return self._state

    def reset(self) -> None:
        self._consecutive_failures = 0
        self._state = "closed"
        self._last_failure_time = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self._state,
            "open": self._state == "open",
            "consecutive_failures": self._consecutive_failures,
            "failure_threshold": self.failure_threshold,
            "cooldown_seconds": self.cooldown_seconds,
        }
