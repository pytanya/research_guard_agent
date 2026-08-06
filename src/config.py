"""
Конфигурация исследовательского агента.

Все настройки читаются из .env через pydantic-модель Settings.
Ключи API никогда не хардкодятся в коде.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

# Корень проекта: research_guard_agent/
BASE_DIR = Path(__file__).resolve().parent.parent

# Загружаем .env из корня проекта (если он существует)
load_dotenv(BASE_DIR / ".env", override=False)


class Settings(BaseSettings):
    """Pydantic-модель настроек агента. Поля соответствуют переменным .env."""

    # --- LLM провайдеры ---
    OPENROUTER_API_KEY: str = Field(default="", description="API-ключ OpenRouter (primary)")
    OPENROUTER_BASE_URL: str = Field(default="https://openrouter.ai/api/v1")
    ROUTERAI_API_KEY: str = Field(default="", description="API-ключ RouterAI (fallback)")
    ROUTERAI_BASE_URL: str = Field(default="https://routerai.ru/api/v1")

    # --- Модель ---
    LLM_MODEL: str = Field(default="qwen/qwen3.7-flash")
    RESEARCHER_MODEL: str = Field(default="", description="Переопределение модели для исследователя")
    # Модель LLM-as-judge (роль «судья») — отдельная от модели исследователя
    JUDGE_MODEL: str = Field(
        default="google/gemini-3.5-flash-lite",
        description="Модель для роли судьи (LLM-as-judge)",
    )
    LLM_PRIMARY_PROVIDER: str = Field(
        default="routerai",
        description="Провайдер по умолчанию: 'routerai' или 'openrouter' (остальные — fallback)",
    )
    # Модели для каждого провайдера (если не заданы — используется LLM_MODEL)
    ROUTERAI_MODEL: str = Field(default="", description="Модель для RouterAI (иначе LLM_MODEL)")
    OPENROUTER_MODEL: str = Field(default="", description="Модель для OpenRouter (иначе LLM_MODEL)")
    # Модели судьи для каждого провайдера (если не заданы — используется JUDGE_MODEL)
    ROUTERAI_JUDGE_MODEL: str = Field(default="", description="Модель судьи для RouterAI (иначе JUDGE_MODEL)")
    OPENROUTER_JUDGE_MODEL: str = Field(default="", description="Модель судьи для OpenRouter (иначе JUDGE_MODEL)")
    # Дополнительные fallback-модели (через запятую), пробуются после основной
    FALLBACK_MODELS: str = Field(
        default="deepseek/deepseek-v4-flash-0731,qwen/qwen3.7-flash",
        description="Список fallback-моделей через запятую, если основная модель недоступна",
    )

    # --- Поиск ---
    YANDEX_API_KEY: str = Field(default="")
    YANDEX_FOLDER_ID: str = Field(default="")
    TAVILY_API_KEY: str = Field(default="")
    SEARCH_PRIMARY: str = Field(
        default="yandex",
        description="Поисковик по умолчанию: 'yandex' или 'tavily' (DDGS — всегда fallback)",
    )

    # --- Лимиты агента ---
    MAX_STEPS: int = Field(default=6, ge=1, le=50)
    MAX_COST_USD: float = Field(default=0.5, ge=0.0)
    REQUEST_TIMEOUT: float = Field(default=30.0, gt=0)
    # Таймаут для RouterAI (primary) — RouterAI отвечает медленнее,
    # поэтому ему нужен отдельный (больший) таймаут, чем REQUEST_TIMEOUT.
    ROUTERAI_TIMEOUT: float = Field(default=120.0, gt=0)

    # --- Таймауты веб-поиска и загрузки страниц ---
    SEARCH_TIMEOUT: float = Field(
        default=30.0, gt=0, description="Таймаут веб-поиска (Yandex/DDGS/Tavily), сек"
    )
    FETCH_TIMEOUT: float = Field(
        default=15.0, gt=0, description="Таймаут загрузки страницы fetch_url, сек"
    )

    # --- Observability ---
    PHOENIX_ENABLED: bool = Field(default=True)
    PHOENIX_PROJECT_NAME: str = Field(default="research-guard-agent")

    # --- Оценка стоимости (fallback, если провайдер не вернул total_cost) ---
    # OpenRouter/иные — в USD
    COST_PER_1M_PROMPT: float = Field(default=0.14)
    COST_PER_1M_COMPLETION: float = Field(default=0.28)
    # RouterAI — цены в рублях за 1M токенов (по моделям):
    # deepseek/deepseek-v4-flash: 9₽ входящие / 18₽ исходящие
    ROUTERAI_COST_PER_1M_PROMPT_RUB: float = Field(default=9.0)
    ROUTERAI_COST_PER_1M_COMPLETION_RUB: float = Field(default=18.0)
    # qwen/qwen3.7-flash: 3,10₽ входящие / 13₽ исходящие
    QWEN_COST_PER_1M_PROMPT_RUB: float = Field(default=3.1)
    QWEN_COST_PER_1M_COMPLETION_RUB: float = Field(default=13.0)
    # deepseek/deepseek-v4-flash-0731 — та же цена, что и v4-flash (9/18₽)
    # Курс рубль→доллар для приведения стоимости RouterAI к USD (по умолчанию ~90₽/$)
    RUB_TO_USD_RATE: float = Field(default=0.0111)

    # --- Каталоги ---
    LOGS_DIR: Path = BASE_DIR / "logs"
    OUTPUT_DIR: Path = BASE_DIR / "output"
    EVALS_DIR: Path = BASE_DIR / "evals"

    @field_validator("OPENROUTER_API_KEY", "ROUTERAI_API_KEY", mode="before")
    @classmethod
    def _strip_keys(cls, v):
        """Обрезаем пробелы/кавычки у ключей."""
        if isinstance(v, str):
            return v.strip().strip('"').strip("'")
        return v

    @field_validator("PHOENIX_ENABLED", mode="before")
    @classmethod
    def _parse_bool(cls, v):
        """Пермиссивный парсинг булевых значений из env."""
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on", "y")
        return bool(v)

    @property
    def model(self) -> str:
        """Активная модель исследователя: RESEARCHER_MODEL (если задан) иначе LLM_MODEL."""
        return self.RESEARCHER_MODEL or self.LLM_MODEL

    @property
    def judge_model(self) -> str:
        """Активная модель судьи (LLM-as-judge): JUDGE_MODEL (или LLM_MODEL, если не задана)."""
        return self.JUDGE_MODEL or self.model

    @property
    def providers(self) -> List[dict]:
        """Список доступных провайдеров, отсортированный по приоритету.

        Primary — LLM_PRIMARY_PROVIDER (routerai по умолчанию), остальные — fallback.
        Если primary-провайдер не настроен (нет ключа), primary становится первый
        доступный — например, OpenRouter с аналогичными моделями (deepseek-v4-flash,
        qwen3.7-flash и т.д.). У каждого провайдера своя модель исследователя:
        ROUTERAI_MODEL / OPENROUTER_MODEL (иначе LLM_MODEL) и своя модель судьи:
        ROUTERAI_JUDGE_MODEL / OPENROUTER_JUDGE_MODEL (иначе JUDGE_MODEL).
        У RouterAI — отдельный, больший таймаут (ROUTERAI_TIMEOUT).
        """
        available = {}
        if self.ROUTERAI_API_KEY:
            available["routerai"] = {
                "name": "routerai",
                "base_url": self.ROUTERAI_BASE_URL,
                "api_key": self.ROUTERAI_API_KEY,
                "model": self.ROUTERAI_MODEL or self.LLM_MODEL,
                "judge_model": self.ROUTERAI_JUDGE_MODEL or self.JUDGE_MODEL,
                "timeout": self.ROUTERAI_TIMEOUT,
            }
        if self.OPENROUTER_API_KEY:
            available["openrouter"] = {
                "name": "openrouter",
                "base_url": self.OPENROUTER_BASE_URL,
                "api_key": self.OPENROUTER_API_KEY,
                "model": self.OPENROUTER_MODEL or self.LLM_MODEL,
                "judge_model": self.OPENROUTER_JUDGE_MODEL or self.JUDGE_MODEL,
                "timeout": None,  # иначе REQUEST_TIMEOUT
            }

        primary = self.LLM_PRIMARY_PROVIDER.strip().lower()
        ordered = []
        if primary in available:
            ordered.append(available.pop(primary))
        # Остальные в алфавитном порядке (стабильно).
        # Если primary (например routerai) не настроен — первый из доступных
        # (например openrouter) становится primary автоматически.
        for name in sorted(available):
            ordered.append(available[name])
        return ordered

    @property
    def search_engines(self) -> List[str]:
        """Список поисковиков по приоритету (по аналогии с providers).

        Primary — SEARCH_PRIMARY (yandex по умолчанию). Доступны только
        настроенные движки: yandex (нужны YANDEX_API_KEY + YANDEX_FOLDER_ID),
        tavily (нужен TAVILY_API_KEY). Если primary не настроен — primary
        становится первый доступный. DDGS (duckduckgo) всегда в конце — ключей
        не требует, поэтому служит универсальным fallback.
        """
        available = []
        if self.YANDEX_API_KEY and self.YANDEX_FOLDER_ID:
            available.append("yandex")
        if self.TAVILY_API_KEY:
            available.append("tavily")

        primary = self.SEARCH_PRIMARY.strip().lower()
        ordered = []
        if primary in available:
            ordered.append(primary)
        # Остальные в алфавитном порядке (стабильно)
        for name in sorted(available):
            if name not in ordered:
                ordered.append(name)
        # DDGS — всегда универсальный fallback (без ключей)
        if "ddgs" not in ordered:
            ordered.append("ddgs")
        return ordered

    @property
    def fallback_models(self) -> List[str]:
        """Список fallback-моделей (после основной модели провайдера)."""
        models = [m.strip() for m in self.FALLBACK_MODELS.split(",") if m.strip()]
        # Первой идёт основная модель активного провайдера
        base = self.model
        return [base] + [m for m in models if m != base]

    @property
    def phoenix_enabled(self) -> bool:
        return self.PHOENIX_ENABLED

    class Config:
        env_file = BASE_DIR / ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


def get_settings() -> Settings:
    """Singleton-получение настроек."""
    return Settings()


# Удобные алиасы для использования в модулях
settings = get_settings()
