"""
Прогон по golden set: оценка качества агента.

- Проходит по вопросам из evals/golden_set.json;
- запускает агента на каждом вопросе;
- подсчитывает pass@1 (ответ содержит ключевые сущности);
- LLM-as-judge оценивает каждый ответ (score 0..10);
- результаты: evals/results_eval_<timestamp>.json и evals/report_<timestamp>.md.

Пример:
    python eval_golden.py [--max-steps N] [--max-cost USD] [--limit N]
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rich.console import Console
from rich.table import Table

from src import config
from src.agent import ResearchAgent
from src.judge import Judge
from src.llm_client import LLMClient
from src.metrics import MetricsCollector
from src.logging_setup import console

GOLDEN_SET = Path(__file__).resolve().parent / "evals" / "golden_set.json"


def normalize(text: str) -> str:
    return (text or "").lower().strip()


def check_pass_at_1(answer_text: str, expected: list) -> bool:
    """pass@1: ответ (или его нижний регистр) содержит ВСЕ ключевые сущности."""
    ans = normalize(answer_text)
    return all(normalize(kw) in ans for kw in expected)


def main() -> int:
    parser = argparse.ArgumentParser(description="Прогон по golden set")
    parser.add_argument("--max-steps", type=int, default=4, help="Лимит шагов на вопрос")
    parser.add_argument("--max-cost", type=float, default=0.3, help="Лимит бюджета на вопрос, USD")
    parser.add_argument("--limit", type=int, default=None, help="Ограничить число вопросов")
    parser.add_argument("--no-phoenix", action="store_true", help="Не запускать Phoenix")
    args = parser.parse_args()

    if not GOLDEN_SET.exists():
        console.print(f"[err]Golden set не найден: {GOLDEN_SET}[/err]")
        return 1

    golden = json.loads(GOLDEN_SET.read_text(encoding="utf-8"))
    if args.limit:
        golden = golden[: args.limit]

    console.print(f"[info]Golden set: {len(golden)} вопросов[/info]")

    llm = LLMClient()
    judge = Judge(llm_client=llm)
    results = []
    pass_count = 0
    total_cost = 0.0
    started = time.monotonic()

    for i, item in enumerate(golden, start=1):
        question = item["question"]
        expected = item.get("expected", [])
        console.rule(f"[bold]Вопрос {i}/{len(golden)}[/bold] — {question}")

        metrics = MetricsCollector()
        agent = ResearchAgent(
            llm_client=llm,
            metrics_collector=metrics,
            max_steps=args.max_steps,
            max_cost_usd=args.max_cost,
        )
        result = agent.run(question)

        answer_text = result.get("answer", "")
        passed = check_pass_at_1(answer_text, expected)
        if passed:
            pass_count += 1

        # Judge
        judge_res = judge.evaluate(question, result)
        score = judge_res.get("score", 0.0)

        total_cost += result["metrics"].get("total_cost_usd", 0.0)

        entry = {
            "question": question,
            "expected": expected,
            "passed": passed,
            "agent_success": result.get("success"),
            "stop_reason": result.get("stop_reason"),
            "judge_score": score,
            "judge_verdict": judge_res.get("verdict"),
            "judge_comment": judge_res.get("comment"),
            "metrics": result.get("metrics", {}),
        }
        results.append(entry)

        console.print(
            f"  [{'ok' if passed else 'err'}]pass@1: {'PASS' if passed else 'FAIL'} | "
            f"judge: {score}/10 ({judge_res.get('verdict')})[/{'ok' if passed else 'err'}]"
        )

    elapsed = round(time.monotonic() - started, 1)
    pass_rate = round(pass_count / len(golden) * 100, 1) if golden else 0.0
    avg_judge = round(sum(r["judge_score"] for r in results) / len(results), 1) if results else 0.0

    # Сохраняем JSON
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    results_json = {
        "timestamp": timestamp,
        "num_questions": len(golden),
        "pass_at_1": pass_count,
        "pass_rate_pct": pass_rate,
        "avg_judge_score": avg_judge,
        "total_cost_usd": round(total_cost, 6),
        "elapsed_sec": elapsed,
        "results": results,
    }
    json_path = config.settings.EVALS_DIR / f"results_eval_{timestamp}.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(results_json, ensure_ascii=False, indent=2), encoding="utf-8")

    # Markdown-отчёт
    md = [
        "# Отчёт по eval (golden set)",
        "",
        f"- Дата: {timestamp}",
        f"- Вопросов: {len(golden)}",
        f"- pass@1: {pass_count}/{len(golden)} ({pass_rate}%)",
        f"- Средний judge-score: {avg_judge}/10",
        f"- Суммарная стоимость: ${round(total_cost, 6)}",
        f"- Время: {elapsed} с",
        "",
        "## Детали",
        "",
        "| # | Вопрос | pass@1 | Judge | Вердикт | success | stop_reason |",
        "|---|--------|--------|-------|---------|---------|-------------|",
    ]
    for i, r in enumerate(results, start=1):
        md.append(
            f"| {i} | {r['question'][:60]} | {'PASS' if r['passed'] else 'FAIL'} | "
            f"{r['judge_score']} | {r['judge_verdict']} | {r['agent_success']} | {r['stop_reason']} |"
        )
    md.append("")
    md.append("## Комментарии judge")
    for r in results:
        md.append(f"**{r['question'][:60]}** — {r['judge_comment']}")
        md.append("")
    md.append(f"\nПодробности: `{json_path.name}`")

    md_path = config.settings.EVALS_DIR / f"report_{timestamp}.md"
    md_path.write_text("\n".join(md), encoding="utf-8")

    # Rich-таблица
    table = Table(title="Итоги eval", show_header=True, header_style="bold cyan")
    table.add_column("Метрика")
    table.add_column("Значение")
    table.add_row("pass@1", f"{pass_count}/{len(golden)} ({pass_rate}%)")
    table.add_row("Средний judge-score", f"{avg_judge}/10")
    table.add_row("Стоимость", f"${round(total_cost, 6)}")
    table.add_row("Время", f"{elapsed} с")
    console.print(table)
    console.print(f"[info]JSON: {json_path}[/info]")
    console.print(f"[info]Markdown: {md_path}[/info]")

    return 0


if __name__ == "__main__":
    sys.exit(main())
