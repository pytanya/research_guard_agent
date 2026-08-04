"""
LLM-as-judge: отдельный LLM-вызов (та же модель) для оценки ответа.

Критерии: фактологичность по источникам, полнота, структура.
Возвращает score 0..10 + вердикт + разбор по критериям.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from .llm_client import LLMClient

logger = logging.getLogger("agent.judge")

JUDGE_SYSTEM_PROMPT = """Ты — строгий судья качества ответов исследовательского агента.
Оцени ответ по трём критериям (каждый 0..10):
1. factual_accuracy — фактологичность: ответ не содержит ошибок и соответствует источникам;
2. completeness — полнота: ответ покрывает ключевые аспекты вопроса;
3. structure — структура: есть answer, sources, confidence, summary; логичность изложения.

Верни ТОЛЬКО JSON:
{
  "score": 0..10 (среднее по критериям, 1 знак после запятой),
  "verdict": "excellent" | "good" | "fair" | "poor",
  "criteria": {
    "factual_accuracy": 0..10,
    "completeness": 0..10,
    "structure": 0..10
  },
  "comment": "краткий комментарий (1-3 предложения)"
}
"""


def _parse_score(text: str) -> Dict[str, Any]:
    """Парсинг JSON-ответа судьи с fallback на извлечение числа."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if m:
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                data = {}
        else:
            data = {}

    if not isinstance(data, dict):
        data = {}

    # Fallback: если score не извлечён — ищем число 0..10 в тексте
    score = data.get("score")
    if score is None:
        m = re.search(r"(\d{1,2}(?:\.\d+)?)\s*/\s*10", text)
        if m:
            score = float(m.group(1))
        else:
            m = re.search(r"score[:\s]+(\d{1,2}(?:\.\d+)?)", text, re.I)
            if m:
                score = float(m.group(1))
            else:
                score = 0.0

    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 0.0
    score = max(0.0, min(10.0, score))

    criteria = data.get("criteria", {})
    if not isinstance(criteria, dict):
        criteria = {}

    verdict = data.get("verdict", "")
    if verdict not in ("excellent", "good", "fair", "poor"):
        if score >= 8.5:
            verdict = "excellent"
        elif score >= 7.0:
            verdict = "good"
        elif score >= 5.0:
            verdict = "fair"
        else:
            verdict = "poor"

    return {
        "score": round(score, 1),
        "verdict": verdict,
        "criteria": criteria,
        "comment": data.get("comment", ""),
    }


class Judge:
    """Судья: оценивает ответ агента по критериям."""

    def __init__(self, llm_client: Optional[LLMClient] = None) -> None:
        self.llm = llm_client or LLMClient()

    def evaluate(
        self,
        question: str,
        answer: Dict[str, Any],
        sources: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Оценить ответ.

        Args:
            question: исходный вопрос.
            answer: dict с полями answer/sources/confidence/summary.
            sources: список источников (если отличается от answer["sources"]).

        Returns:
            dict: {score, verdict, criteria, comment, cost_usd, elapsed_sec}
        """
        src_list = sources or answer.get("sources", [])
        user_prompt = f"""Вопрос: {question}

Ответ агента:
{json.dumps(answer, ensure_ascii=False, indent=2)[:6000]}

Источники:
{chr(10).join('- ' + s for s in src_list[:15])}

Оцени ответ по критериям и верни JSON."""
        messages = [
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        start = time.monotonic()
        try:
            resp = self.llm.chat(messages)
        except Exception as e:
            logger.error("Judge: не удалось получить оценку: %s", e)
            return {
                "score": 0.0,
                "verdict": "error",
                "criteria": {},
                "comment": f"Ошибка judge: {e}",
                "cost_usd": 0.0,
                "elapsed_sec": round(time.monotonic() - start, 3),
            }

        elapsed = round(time.monotonic() - start, 3)
        parsed = _parse_score(resp.content or "")
        parsed["cost_usd"] = resp.cost_usd
        parsed["elapsed_sec"] = elapsed
        return parsed
