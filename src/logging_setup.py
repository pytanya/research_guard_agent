"""
Настройка логирования исследовательского агента.

- Rich console для человекопонятного вывода (панели, спиннеры).
- JSONL-лог каждого шага в logs/run_<timestamp>.jsonl
  (request_id, timestamp, step_num, agent_action, tool, duration, status, tokens, cost).
- Файловый лог в output/run_<timestamp>/run.log.
- Маскирование PII/секретов в логах (API-ключи, email, URL с ключами).
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from rich.console import Console
from rich.panel import Panel
from rich.theme import Theme

from . import config

# ----------------------------------------------------------------------
# Rich
# ----------------------------------------------------------------------
RICH_THEME = Theme(
    {
        "ok": "bold green",
        "err": "bold red",
        "warn": "bold yellow",
        "info": "cyan",
        "dim": "dim",
        "metric": "bold magenta",
    }
)

console = Console(theme=RICH_THEME, force_terminal=True)

# ----------------------------------------------------------------------
# Логгер шагов (для JSONL)
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# Маскирование PII/секретов
# ----------------------------------------------------------------------
# Паттерны для маскирования: API-ключи, email, URL с embedded-ключами
_SENSITIVE_PATTERNS: list[tuple[re.Pattern, str]] = [
    # API-ключи: sk-... (OpenAI-подобные), sk-or-v1-... (OpenRouter)
    (re.compile(r'\b(sk-[a-zA-Z0-9]{20,})\b'), r'sk-***masked***'),
    (re.compile(r'\b(sk-or-v1-[a-zA-Z0-9]{20,})\b'), r'sk-or-v1-***masked***'),
    # API-ключи Yandex: AQVN...
    (re.compile(r'\b(AQVN[a-zA-Z0-9_-]{10,})\b'), r'AQVN***masked***'),
    # Email-адреса
    (re.compile(r'\b([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)\b'), r'***@***.***'),
    # URL с API-ключами (например ?key=... или &api_key=...)
    (re.compile(r'(\?|&)(api[_-]?key|token|secret)=[^&\s]+'), r'\1\2=***masked***'),
    # Токены в URL-путях (например /v1/.../sk-...)
    (re.compile(r'(/[a-zA-Z0-9]+-)(sk-[a-zA-Z0-9]{20,})'), r'\1***masked***'),
]


def mask_sensitive(value: Any) -> Any:
    """Рекурсивно маскирует PII/секреты в строках внутри структур данных."""
    if isinstance(value, str):
        text = value
        for pattern, replacement in _SENSITIVE_PATTERNS:
            text = pattern.sub(replacement, text)
        return text
    if isinstance(value, dict):
        return {k: mask_sensitive(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_sensitive(v) for v in value]
    if isinstance(value, tuple):
        return tuple(mask_sensitive(v) for v in value)
    return value


class JsonlStepLogger:
    """Пишет структурированные записи шагов в JSONL-файл (с маскированием PII)."""

    def __init__(self, path: Path, request_id: Optional[str] = None) -> None:
        self.path = path
        self.request_id = request_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(path, "a", encoding="utf-8")

    def log_step(
        self,
        *,
        step_num: int,
        agent_action: str,
        tool: Optional[str] = None,
        duration: Optional[float] = None,
        status: Optional[str] = None,
        tokens: Optional[int] = None,
        cost: Optional[float] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        record: Dict[str, Any] = {
            "request_id": self.request_id,
            "timestamp": datetime.datetime.now().isoformat(timespec="milliseconds"),
            "step_num": step_num,
            "agent_action": agent_action,
            "tool": tool,
            "duration": round(duration, 3) if duration is not None else None,
            "status": status,
            "tokens": tokens,
            "cost": round(cost, 6) if cost is not None else None,
        }
        if extra:
            # Маскируем PII/секреты в extra-данных (аргументы инструментов и т.д.)
            record["extra"] = mask_sensitive(extra)
        self._f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._f.flush()

    def close(self) -> None:
        try:
            self._f.close()
        except Exception:
            pass


# ----------------------------------------------------------------------
# Инициализация
# ----------------------------------------------------------------------
def setup_logging(run_dir: Path) -> Dict[str, Any]:
    """
    Настроить логирование для конкретного прогона.

    Args:
        run_dir: папка прогона output/run_<timestamp>/ (создаётся при необходимости).

    Returns:
        dict с {request_id, run_dir, run_log, jsonl_path, step_logger}
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = config.settings.LOGS_DIR
    logs_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    # Идентификатор запроса: связывает JSONL-лог, run-папку и отчёты между собой.
    request_id = f"req_{timestamp}_{uuid.uuid4().hex[:8]}"

    # 1) Файловый лог в run-папке
    run_log_path = run_dir / "run.log"
    file_handler = logging.FileHandler(run_log_path, encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    )

    # 2) Консольный handler (минимальный, rich дублирует вывод)
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s")
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Не дублируем хендлеры при повторных вызовах
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)

    # DDGS перебирает десятки движков и на INFO логирует каждый сбой (сеть
    # у нас капризная — это десятки строк спама на один поиск). Глушим до WARNING;
    # для отладки сети уровень можно вернуть на INFO.
    logging.getLogger("ddgs").setLevel(logging.WARNING)
    # primp — HTTP-клиент ddgs, тоже пишет каждый ответ на INFO.
    logging.getLogger("primp").setLevel(logging.WARNING)

    # 3) JSONL-лог шагов
    jsonl_path = logs_dir / f"run_{timestamp}.jsonl"
    step_logger = JsonlStepLogger(jsonl_path, request_id=request_id)

    return {
        "request_id": request_id,
        "run_dir": run_dir,
        "run_log": run_log_path,
        "jsonl_path": jsonl_path,
        "step_logger": step_logger,
    }


# ----------------------------------------------------------------------
# Rich-хелперы
# ----------------------------------------------------------------------
def print_panel(title: str, body: str, style: str = "info") -> None:
    """Панель в консоли."""
    console.print(Panel(body, title=title, border_style=style, expand=False))


def print_step_header(step_num: int, total: int, action: str) -> None:
    console.rule(f"[bold]Шаг {step_num}/{total}[/bold] — {action}")
