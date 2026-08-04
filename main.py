"""
CLI-точка входа исследовательского агента.

Примеры:
    python main.py "Кто такой Альберт Эйнштейн?"
    python main.py "Что такое Langfuse?" --max-steps 4 --max-cost 0.3
    python main.py "вопрос" --no-phoenix
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

# UTF-8 для stdout/stderr (Windows legacy-терминал иначе падает на юникоде)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Позволяем импортировать src.* из корня проекта
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src import config
from src.agent import ResearchAgent
from src.guardrails import validate_answer
from src.judge import Judge
from src.llm_client import LLMClient
from src.logging_setup import console, print_panel, setup_logging
from src.metrics import MetricsCollector

# --- Phoenix / OpenInference (этап 2, graceful degrade) ---
_phoenix = None


def _init_phoenix(enabled: bool) -> bool:
    """Инициализация Phoenix и OpenInference-инструментирования.

    Порядок (Phoenix SDK 19.x):
    1. px.launch_app()  — запуск локального UI (http://localhost:6006);
    2. register() из phoenix.otel — регистрация OTLP-экспортёра (localhost:4317)
       и глобального TracerProvider;
    3. OpenInferenceAutoInstrumentor().instrument() — авто-трассировка OpenAI.

    Returns:
        True, если Phoenix успешно запущен, иначе False (graceful degrade).
    """
    if not enabled:
        console.print("[dim]Phoenix отключён (--no-phoenix или PHOENIX_ENABLED=false)[/dim]")
        return False
    try:
        import phoenix as px
        from phoenix.otel import register
        from openinference.instrumentation.openai import OpenAIInstrumentor

        global _phoenix
        _phoenix = px.launch_app()          # 1) UI на http://localhost:6006
        register()                          # 2) OTLP-экспортёр + TracerProvider
        OpenAIInstrumentor().instrument()   # 3) авто-трассировка OpenAI

        console.print(
            "[ok]Phoenix UI: http://localhost:6006 (трейсы LLM-вызовов и инструментов)[/ok]"
        )
        return True
    except Exception as e:
        console.print(f"[warn]Phoenix недоступен ({e}). Работаем без трассировки.[/warn]")
        return False


def _save_answer(run_dir: Path, result: dict) -> Path:
    """Сохранить финальный ответ в answer.md."""
    path = run_dir / "answer.md"
    lines = [
        "# Ответ исследовательского агента",
        "",
        f"**Вопрос:** {result.get('_question', '')}",
        "",
        "## answer",
        result.get("answer", ""),
        "",
        "## summary",
        result.get("summary", ""),
        "",
        f"**confidence:** {result.get('confidence', 0.0)}",
        f"**success:** {result.get('success', False)}",
        f"**stop_reason:** {result.get('stop_reason', '')}",
        "",
        "## sources",
    ]
    for s in result.get("sources", []):
        lines.append(f"- {s}")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _save_dz_report(run_dir: Path, result: dict, judge_res: dict) -> Path:
    """Сформировать dz_report.md — сводку по ДЗ (метрики, guardrails, judge)."""
    m = result.get("metrics", {})
    lines = [
        "# Отчёт по ДЗ: ResearchGuardAgent",
        "",
        f"- **Вопрос:** {result.get('_question', '')}",
        f"- **Модель:** {result.get('_model', '')}",
        f"- **Провайдеры:** {result.get('_providers', '')}",
        f"- **Phoenix:** {'включён (UI http://localhost:6006)' if result.get('_phoenix') else 'не включён'}",
        "",
        "## Метрики",
        "",
        f"- success: **{m.get('success')}**",
        f"- stop_reason: `{m.get('stop_reason')}`",
        f"- elapsed_sec: **{m.get('elapsed_sec')} с**",
        f"- total_cost_usd: **${m.get('total_cost_usd')}**",
        f"- num_steps: **{m.get('num_steps')}**",
        f"- num_llm_calls: **{m.get('num_llm_calls')}**",
        f"- total_tokens: **{m.get('total_tokens')}**",
        "",
        "## Лог шагов",
        "",
        "| # | Инструмент | Статус | Длительность, с | Стоимость, $ |",
        "|---|------------|--------|-----------------|-------------|",
    ]
    for s in m.get("steps", []):
        lines.append(
            f"| {s.get('step_num')} | {s.get('tool')} | {s.get('status')} | "
            f"{s.get('duration_sec')} | {s.get('cost_usd')} |"
        )
    lines.append("")
    lines.append("## Guardrails")
    lines.append("")
    lines.append(f"- **prompt-injection:** {result.get('injection')}")
    lines.append(f"- **валидация ответа:** {result.get('validation')}")
    lines.append(f"- **circuit breaker:** {result.get('circuit_breaker')}")
    lines.append(f"- **бюджет (MAX_COST_USD):** $ {config.settings.MAX_COST_USD}")
    lines.append("")
    lines.append("## LLM-as-judge")
    lines.append("")
    lines.append(f"- **score:** {judge_res.get('score')}/10")
    lines.append(f"- **verdict:** {judge_res.get('verdict')}")
    lines.append(f"- **criteria:** {judge_res.get('criteria')}")
    lines.append(f"- **comment:** {judge_res.get('comment')}")
    lines.append("")
    lines.append("## Phoenix (что видно в UI)")
    lines.append("")
    if result.get("_phoenix"):
        lines.append(
            "В UI Phoenix (http://localhost:6006) видно: "
            "список трасс (traces) прогона агента; для каждого LLM-вызова — "
            "span с промптом, ответом, количеством токенов и стоимостью; "
            "для инструментов — duration и результат. Трейсы группируются в проект "
            "«default» (или в имя приложения из PHOENIX_PROJECT)."
        )
    else:
        lines.append("Phoenix не запускался в этом прогоне (--no-phoenix).")
    lines.append("")
    lines.append("## Финальный ответ")
    lines.append("")
    lines.append(result.get("answer", ""))
    lines.append("")
    lines.append("### sources")
    for s in result.get("sources", []):
        lines.append(f"- {s}")
    lines.append("")

    path = run_dir / "dz_report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _print_report(result: dict, run_dir: Path) -> None:
    """Rich-отчёт по метрикам прогона."""
    m = result.get("metrics", {})
    console.print("\n")
    console.rule("[bold]Итоговый отчёт[/bold]")

    table = Table(title="Метрики прогона", show_header=True, header_style="bold cyan")
    table.add_column("Метрика", style="cyan")
    table.add_column("Значение", style="white")
    table.add_row("success", str(m.get("success", False)))
    table.add_row("stop_reason", m.get("stop_reason", ""))
    table.add_row("elapsed_sec", f"{m.get('elapsed_sec', 0.0):.2f} с")
    table.add_row("total_cost_usd", f"${m.get('total_cost_usd', 0.0):.4f}")
    table.add_row("num_steps", str(m.get("num_steps", 0)))
    table.add_row("num_llm_calls", str(m.get("num_llm_calls", 0)))
    table.add_row("total_tokens", str(m.get("total_tokens", 0)))
    console.print(table)

    if m.get("steps"):
        steps_table = Table(title="Шаги", show_header=True, header_style="bold magenta")
        steps_table.add_column("#")
        steps_table.add_column("Инструмент")
        steps_table.add_column("Статус")
        steps_table.add_column("Длительность, с")
        steps_table.add_column("Детали")
        for s in m["steps"]:
            steps_table.add_row(
                str(s.get("step_num", "")),
                s.get("tool", ""),
                s.get("status", ""),
                f"{s.get('duration_sec', 0.0):.2f}",
                str(s.get("detail", ""))[:80],
            )
        console.print(steps_table)

    console.print(f"\n[dim]Файлы прогона:[/dim] {run_dir}")
    console.print(f"[dim]answer.md:[/dim] {run_dir / 'answer.md'}")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Исследовательский агент с циклом Reason → Act → Observe",
    )
    parser.add_argument("question", type=str, help="Вопрос для исследования")
    parser.add_argument(
        "--max-steps", type=int, default=None, help=f"Максимум шагов (default: {config.settings.MAX_STEPS})"
    )
    parser.add_argument(
        "--max-cost", type=float, default=None, help=f"Максимальный бюджет, USD (default: {config.settings.MAX_COST_USD})"
    )
    parser.add_argument(
        "--no-phoenix", action="store_true", help="Не запускать Phoenix (трассировка отключена)"
    )
    args = parser.parse_args()

    # 1) Run-папка
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = config.settings.OUTPUT_DIR / f"run_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    # 2) Логирование
    log_ctx = setup_logging(run_dir)
    step_logger = log_ctx["step_logger"]

    # 3) Phoenix
    phoenix_ok = _init_phoenix(enabled=(not args.no_phoenix) and config.settings.phoenix_enabled)

    # 4) Агент
    metrics = MetricsCollector()
    llm = LLMClient()
    agent = ResearchAgent(
        llm_client=llm,
        metrics_collector=metrics,
        step_logger=step_logger,
        max_steps=args.max_steps,
        max_cost_usd=args.max_cost,
    )

    print_panel("ResearchGuardAgent", f"Модель: {llm.model}\nПровайдеры: {', '.join(p['name'] for p in llm.providers)}", style="info")

    # 5) Запуск
    try:
        result = agent.run(args.question)
    except Exception as e:
        console.print(f"[err]Критическая ошибка: {e}[/err]")
        result = {
            "answer": "",
            "sources": [],
            "confidence": 0.0,
            "summary": "",
            "success": False,
            "stop_reason": f"fatal: {e}",
            "metrics": metrics.to_dict(),
        }
    finally:
        step_logger.close()

    result["_question"] = args.question
    result["_phoenix"] = phoenix_ok
    result["_model"] = llm.model
    result["_providers"] = ", ".join(p["name"] for p in llm.providers)

    # 6) LLM-as-judge (этап 2) — оцениваем ответ
    judge_res = {}
    if result.get("success") and result.get("answer"):
        console.print("\n[info]Оценка ответа судьёй (LLM-as-judge)...[/info]")
        judge = Judge(llm_client=llm)
        judge_res = judge.evaluate(args.question, result)
        console.print(
            f"  [info]Judge: {judge_res.get('score')}/10 ({judge_res.get('verdict')}) — {judge_res.get('comment','')[:100]}[/info]"
        )
    result["_judge"] = judge_res

    # 7) Сохранение ответа, отчёта и dz_report.md
    _save_answer(run_dir, result)
    _save_dz_report(run_dir, result, judge_res)
    _print_report(result, run_dir)

    return 0


if __name__ == "__main__":
    sys.exit(main())
