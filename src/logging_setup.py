"""
Настройка логирования исследовательского агента.

- Rich console для человекопонятного вывода (панели, спиннеры).
- JSONL-лог каждого шага в logs/run_<timestamp>.jsonl
  (timestamp, step_num, agent_action, tool, duration, status, tokens, cost).
- Файловый лог в output/run_<timestamp>/run.log.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import sys
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
class JsonlStepLogger:
    """Пишет структурированные записи шагов в JSONL-файл."""

    def __init__(self, path: Path) -> None:
        self.path = path
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
            record["extra"] = extra
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
        dict с {run_dir, run_log, jsonl_path, step_logger}
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = config.settings.LOGS_DIR
    logs_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

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

    # 3) JSONL-лог шагов
    jsonl_path = logs_dir / f"run_{timestamp}.jsonl"
    step_logger = JsonlStepLogger(jsonl_path)

    return {
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
