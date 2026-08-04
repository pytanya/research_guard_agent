"""
Сбор метрик исследовательского агента.

MetricsCollector:
- start()/stop() — общее время прогона;
- add_step(name, duration, status, cost) — метрики одного шага (инструмента);
- total_cost — сумма стоимости шагов и LLM-вызовов;
- to_dict() — словарь со всеми метриками (success, elapsed_sec, total_cost_usd,
  num_steps, steps[]).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


class MetricsCollector:
    """Коллектор метрик прогона агента."""

    def __init__(self) -> None:
        self._started_at: Optional[float] = None
        self._stopped_at: Optional[float] = None
        self._steps: List[Dict[str, Any]] = []
        self._llm_calls: List[Dict[str, Any]] = []
        self.success: bool = False
        self.stop_reason: str = ""

    # ------------------------------------------------------------------
    def start(self) -> None:
        """Зафиксировать время старта."""
        self._started_at = time.monotonic()

    def stop(self) -> None:
        """Зафиксировать время остановки."""
        self._stopped_at = time.monotonic()

    # ------------------------------------------------------------------
    @property
    def elapsed_sec(self) -> float:
        """Общее время прогона, сек."""
        if self._started_at is None:
            return 0.0
        end = self._stopped_at or time.monotonic()
        return round(end - self._started_at, 3)

    # ------------------------------------------------------------------
    def add_step(
        self,
        name: str,
        duration: float,
        status: str = "OK",
        cost: float = 0.0,
        detail: str = "",
    ) -> None:
        """
        Зафиксировать шаг (обычно — вызов инструмента).

        Args:
            name: имя инструмента (например "search_web").
            duration: длительность, сек.
            status: "OK" или "ERROR".
            cost: стоимость шага, USD.
            detail: доп. информация (например ошибка или число результатов).
        """
        self._steps.append(
            {
                "step_num": len(self._steps) + 1,
                "tool": name,
                "duration_sec": round(duration, 3),
                "status": status,
                "cost_usd": round(cost, 6),
                "detail": detail,
            }
        )

    def add_llm_call(
        self,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
        provider: str = "",
        duration: float = 0.0,
        kind: str = "step",
    ) -> None:
        """Зафиксировать отдельный LLM-вызов (для детального учёта стоимости)."""
        self._llm_calls.append(
            {
                "kind": kind,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "cost_usd": round(cost_usd, 6),
                "provider": provider,
                "duration_sec": round(duration, 3),
            }
        )

    # ------------------------------------------------------------------
    @property
    def total_cost(self) -> float:
        """Суммарная стоимость: шаги + LLM-вызовы."""
        step_cost = sum(s.get("cost_usd", 0.0) for s in self._steps)
        llm_cost = sum(c.get("cost_usd", 0.0) for c in self._llm_calls)
        return round(step_cost + llm_cost, 6)

    @property
    def num_steps(self) -> int:
        """Число шагов (инструментных вызовов)."""
        return len(self._steps)

    @property
    def num_llm_calls(self) -> int:
        return len(self._llm_calls)

    @property
    def total_tokens(self) -> int:
        """Суммарное число токенов во всех LLM-вызовах."""
        return sum(
            c.get("prompt_tokens", 0) + c.get("completion_tokens", 0)
            for c in self._llm_calls
        )

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Словарь со всеми метриками."""
        return {
            "success": self.success,
            "stop_reason": self.stop_reason,
            "elapsed_sec": self.elapsed_sec,
            "total_cost_usd": self.total_cost,
            "num_steps": self.num_steps,
            "num_llm_calls": self.num_llm_calls,
            "total_tokens": self.total_tokens,
            "steps": list(self._steps),
            "llm_calls": list(self._llm_calls),
        }
