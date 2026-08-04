"""
Единый LLM-клиент исследовательского агента.

- Primary: RouterAI (по умолчанию, LLM_PRIMARY_PROVIDER), Fallback: OpenRouter и др.
- У каждого провайдера своя модель (ROUTERAI_MODEL / OPENROUTER_MODEL / LLM_MODEL)
- Fallback по моделям: основная → deepseek/deepseek-v4-flash-0731 → qwen/qwen3.7-flash
- Retry с экспоненциальным backoff (макс 3 попытки, 1→2→4 сек)
  при ошибках 429, 5xx, ConnectionError, Timeout
- Подсчёт стоимости: usage.total_cost (OpenRouter/RouterAI, если есть),
  иначе — приблизительно по токенам (цены deepseek-v4-flash).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import openai

from . import config

logger = logging.getLogger("agent.llm")

# Приблизительные цены за 1M токенов (USD) — fallback для RouterAI
PROMPT_PRICE_PER_1M = config.settings.COST_PER_1M_PROMPT
COMPLETION_PRICE_PER_1M = config.settings.COST_PER_1M_COMPLETION

# Коды ответов, при которых нужно повторить запрос
RETRY_STATUS_CODES = {408, 429, 500, 502, 503, 504}
MAX_RETRIES = 3
BACKOFF_BASE = 1.0  # 1 → 2 → 4 сек


@dataclass
class LLMResponse:
    """Результат LLM-вызова."""

    content: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    usage: Dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0
    cost_raw: float = 0.0          # стоимость как вернул провайдер (RouterAI — в рублях)
    currency: str = "USD"          # валюта cost_raw
    provider: str = ""
    model: str = ""


def _estimate_cost(usage: Dict[str, int], provider: str = "", model: str = "") -> float:
    """Приблизительная стоимость по токенам (fallback).

    Для RouterAI используются рублёвые цены за 1M токенов с конвертацией в USD:
      - deepseek/deepseek-v4-flash и -0731: 9₽ входящие / 18₽ исходящие
      - qwen/qwen3.7-flash: 3,10₽ входящие / 13₽ исходящие
    Для остальных провайдеров — долларовые цены deepseek-flash.
    """
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    if provider == "routerai":
        s = config.settings
        if "qwen" in model:
            p, c = s.QWEN_COST_PER_1M_PROMPT_RUB, s.QWEN_COST_PER_1M_COMPLETION_RUB
        else:
            p, c = s.ROUTERAI_COST_PER_1M_PROMPT_RUB, s.ROUTERAI_COST_PER_1M_COMPLETION_RUB
        return (
            prompt_tokens / 1_000_000 * p
            + completion_tokens / 1_000_000 * c
        ) * s.RUB_TO_USD_RATE
    return (
        prompt_tokens / 1_000_000 * PROMPT_PRICE_PER_1M
        + completion_tokens / 1_000_000 * COMPLETION_PRICE_PER_1M
    )


class LLMClient:
    """Обёртка над OpenAI-совместимым API с retry и fallback между провайдерами."""

    def __init__(self, settings: Optional[config.Settings] = None):
        self.settings = settings or config.settings
        self.model = self.settings.model
        self.timeout = self.settings.REQUEST_TIMEOUT
        self.providers = self.settings.providers

        if not self.providers:
            raise RuntimeError(
                "Нет ни одного настроенного LLM-провайдера. "
                "Укажите OPENROUTER_API_KEY или ROUTERAI_API_KEY в .env"
            )

        self._clients: Dict[str, openai.OpenAI] = {}
        for p in self.providers:
            try:
                self._clients[p["name"]] = openai.OpenAI(
                    base_url=p["base_url"],
                    api_key=p["api_key"],
                    timeout=self.timeout,
                    max_retries=0,  # retry реализуем сами
                )
            except Exception as e:  # pragma: no cover
                logger.warning("Не удалось создать клиент для %s: %s", p["name"], e)

        if not self._clients:
            raise RuntimeError("Не удалось инициализировать ни одного LLM-клиента")

        # Статистика для отчёта
        self.provider_used: List[str] = []

    # ------------------------------------------------------------------
    def _make_request(
        self,
        client: openai.OpenAI,
        provider_name: str,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        tool_choice: Any,
    ) -> LLMResponse:
        """Один запрос к конкретному провайдеру с конкретной моделью (без retry)."""
        kwargs: Dict[str, Any] = {"model": model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice

        resp = client.chat.completions.create(**kwargs)

        message = resp.choices[0].message
        content = message.content
        tool_calls = None
        if message.tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ]

        usage_raw = resp.usage
        usage: Dict[str, int] = {}
        if usage_raw is not None:
            usage = {
                "prompt_tokens": getattr(usage_raw, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(usage_raw, "completion_tokens", 0) or 0,
                "total_tokens": getattr(usage_raw, "total_tokens", 0) or 0,
            }

        # Стоимость: RouterAI возвращает поле cost (в рублях),
        # OpenRouter — usage.total_cost (в USD)
        cost = 0.0
        cost_raw = 0.0
        currency = "USD"

        if provider_name == "routerai":
            # RouterAI: поле response.cost — стоимость в рублях (как у провайдера)
            cost_raw = getattr(resp, "cost", 0.0) or 0.0
            try:
                cost_raw = float(cost_raw)
            except (TypeError, ValueError):
                cost_raw = 0.0
            if cost_raw > 0:
                currency = "RUB"
                cost = cost_raw * config.settings.RUB_TO_USD_RATE
        elif usage_raw is not None and hasattr(usage_raw, "total_cost"):
            # OpenRouter: usage.total_cost в USD
            raw_cost = getattr(usage_raw, "total_cost", None)
            if raw_cost is not None:
                try:
                    cost = float(raw_cost)
                except (TypeError, ValueError):
                    cost = 0.0
                else:
                    cost_raw = cost

        if cost <= 0.0:
            cost = _estimate_cost(usage, provider=provider_name, model=model)
            if cost > 0:
                cost_raw = cost
                currency = "USD"

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            cost_usd=round(cost, 6),
            cost_raw=round(cost_raw, 6),
            currency=currency,
        )

    # ------------------------------------------------------------------
    def _request_with_retry(
        self,
        provider: Dict[str, Any],
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        tool_choice: Any,
    ) -> LLMResponse:
        """Запрос с retry на одном провайдере и модели. Пробрасывает исключение после исчерпания."""
        name = provider["name"]
        client = self._clients.get(name)
        if client is None:
            raise ConnectionError(f"Клиент {name} не инициализирован")

        last_error: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self._make_request(client, name, model, messages, tools, tool_choice)
                resp.provider = name
                resp.model = model
                self.provider_used.append(name)
                return resp
            except openai.RateLimitError as e:
                last_error = e
            except openai.APIConnectionError as e:
                last_error = e
            except openai.APITimeoutError as e:
                last_error = e
            except openai.APIStatusError as e:
                last_error = e
                status = e.status_code
                if status not in RETRY_STATUS_CODES:
                    # Неповторяемая ошибка (например 400/401/403) — не ретраим
                    raise
            except ConnectionError as e:
                last_error = e
            except TimeoutError as e:
                last_error = e
            except Exception as e:  # остальные — не ретраим
                logger.error("LLM %s: необработанная ошибка: %s", name, e)
                raise

            if attempt < MAX_RETRIES:
                delay = BACKOFF_BASE * (2 ** (attempt - 1))
                logger.warning(
                    "LLM %s [%s]: попытка %d/%d не удалась (%s). Backoff %.1fс",
                    name, model, attempt, MAX_RETRIES, last_error, delay,
                )
                time.sleep(delay)

        raise last_error if last_error else RuntimeError(f"Провайдер {name} исчерпал retry")

    # ------------------------------------------------------------------
    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Any = None,
    ) -> LLMResponse:
        """
        Основной метод. Пробует провайдеров по приоритету (primary → fallback),
        внутри каждого провайдера — по моделям (основная → fallback-модели).

        Returns:
            LLMResponse(content, tool_calls, usage, cost_usd)
        """
        providers = list(self.providers)
        if not providers:
            raise RuntimeError("Нет доступных провайдеров")

        fallback_models = self.settings.fallback_models
        errors: List[str] = []

        for idx, provider in enumerate(providers):
            # Модели для этого провайдера: его модель первой, затем общие fallback
            provider_model = provider.get("model") or self.settings.model
            models = [provider_model] + [
                m for m in fallback_models if m != provider_model
            ]

            for model in models:
                try:
                    resp = self._request_with_retry(
                        provider, model, messages, tools, tool_choice
                    )
                    return resp
                except openai.APIStatusError as e:
                    # 403/401 — модель недоступна на этом провайдере, пробуем следующую
                    errors.append(f"{provider['name']}/{model}: {e}")
                    logger.warning(
                        "Провайдер %s, модель %s недоступна (%s). Пробую следующую модель.",
                        provider["name"], model, e,
                    )
                except Exception as e:
                    errors.append(f"{provider['name']}/{model}: {e}")
                    logger.error(
                        "Провайдер %s, модель %s не смог обработать запрос. %s",
                        provider["name"], model, e,
                    )
                    break  # с моделью провайдера что-то серьёзное — переходим к след. провайдеру

            if idx < len(providers) - 1:
                logger.info("Переключаюсь на fallback-провайдера...")

        raise RuntimeError("Все провайдеры и модели недоступны: " + "; ".join(errors))
