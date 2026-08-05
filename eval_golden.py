"""
Прогон по golden set: оценка качества агента.

- Проходит по вопросам из evals/golden_set.json;
- запускает агента на каждом вопросе;
- подсчитывает pass@1 (ответ содержит ключевые сущности);
- LLM-as-judge оценивает каждый ответ (score 0..10);
- при --runs N > 1 оценивает стабильность: разброс pass@1/стоимости/времени по прогонам;
- трассирует прогон в Phoenix (если PHOENIX_ENABLED и не --no-phoenix);
- результаты: evals/results_eval_<timestamp>.json и evals/report_<timestamp>.md.

Примеры:
    python eval_golden.py [--max-steps N] [--max-cost USD] [--limit N]
    python eval_golden.py --runs 3              # стабильность по 3 прогонам
"""

from __future__ import annotations

import argparse
import datetime
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rich.console import Console
from rich.table import Table

from main import _init_phoenix, _shutdown_phoenix
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


def _run_question(item: dict, llm: LLMClient, judge: Judge, max_steps: int, max_cost: float) -> dict:
    """Один вопрос: запуск агента + pass@1 + judge."""
    question = item["question"]
    expected = item.get("expected", [])
    console.rule(f"[bold]Вопрос — {question}[/bold]")

    metrics = MetricsCollector()
    agent = ResearchAgent(
        llm_client=llm,
        metrics_collector=metrics,
        max_steps=max_steps,
        max_cost_usd=max_cost,
    )
    result = agent.run(question)

    answer_text = result.get("answer", "")
    passed = check_pass_at_1(answer_text, expected)

    judge_res = judge.evaluate(question, result)
    score = judge_res.get("score", 0.0)

    console.print(
        f"  [{'ok' if passed else 'err'}]pass@1: {'PASS' if passed else 'FAIL'} | "
        f"judge: {score}/10 ({judge_res.get('verdict')})[/{'ok' if passed else 'err'}]"
    )

    return {
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Прогон по golden set")
    parser.add_argument("--max-steps", type=int, default=6, help="Лимит шагов на вопрос")
    parser.add_argument("--max-cost", type=float, default=0.3, help="Лимит бюджета на вопрос, USD")
    parser.add_argument("--limit", type=int, default=None, help="Ограничить число вопросов")
    parser.add_argument("--runs", type=int, default=1, help="Число прогонов для оценки стабильности")
    parser.add_argument("--no-phoenix", action="store_true", help="Не запускать Phoenix")
    args = parser.parse_args()

    if args.runs < 1:
        console.print("[err]--runs должен быть >= 1[/err]")
        return 1

    if not GOLDEN_SET.exists():
        console.print(f"[err]Golden set не найден: {GOLDEN_SET}[/err]")
        return 1

    golden = json.loads(GOLDEN_SET.read_text(encoding="utf-8"))
    if args.limit:
        golden = golden[: args.limit]

    console.print(f"[info]Golden set: {len(golden)} вопросов, прогонов: {args.runs}[/info]")

    phoenix_ok = _init_phoenix(enabled=(not args.no_phoenix) and config.settings.phoenix_enabled)
    llm = LLMClient()
    # Судья на своей модели (JUDGE_MODEL) — отдельно от модели исследователя.
    judge = Judge()

    started = time.monotonic()
    # results_all[run_idx] -> list[entry] (по вопросам)
    results_all: list[list[dict]] = []
    try:
        for run_idx in range(1, args.runs + 1):
            if args.runs > 1:
                console.rule(f"[bold]Прогон {run_idx}/{args.runs}[/bold]")
            run_results: list[dict] = []
            for item in golden:
                run_results.append(
                    _run_question(item, llm, judge, args.max_steps, args.max_cost)
                )
            results_all.append(run_results)
    finally:
        _shutdown_phoenix()

    elapsed_total = round(time.monotonic() - started, 1)

    # --- Агрегация ---
    nq = len(golden)
    total_runs = len(results_all)
    all_entries = [e for run in results_all for e in run]
    pass_count = sum(1 for e in all_entries if e["passed"])
    pass_rate_pct = round(pass_count / len(all_entries) * 100, 1) if all_entries else 0.0
    avg_judge = round(sum(e["judge_score"] for e in all_entries) / len(all_entries), 1) if all_entries else 0.0
    total_cost = round(sum(e["metrics"].get("total_cost_usd", 0.0) for e in all_entries), 6)

    # Стабильность (по прогонам)
    stability: dict = {"runs": total_runs}
    if total_runs > 1:
        run_pass_rates = [
            round(sum(1 for e in run if e["passed"]) / len(run) * 100, 1) if run else 0.0
            for run in results_all
        ]
        run_success_rates = [
            round(sum(1 for e in run if e.get("agent_success")) / len(run) * 100, 1) if run else 0.0
            for run in results_all
        ]
        run_costs = [round(sum(e["metrics"].get("total_cost_usd", 0.0) for e in run), 6) for run in results_all]
        run_elapsed = [
            round(sum(e["metrics"].get("elapsed_sec", 0.0) for e in run), 1) for run in results_all
        ]
        stability.update(
            {
                "pass_rate_per_run_pct": run_pass_rates,
                "pass_rate_mean_pct": round(statistics.mean(run_pass_rates), 1),
                "pass_rate_std_pct": round(statistics.pstdev(run_pass_rates), 1),
                "success_rate_mean_pct": round(statistics.mean(run_success_rates), 1),
                "success_rate_std_pct": round(statistics.pstdev(run_success_rates), 1),
                "cost_per_run_usd": run_costs,
                "cost_std_usd": round(statistics.pstdev(run_costs), 6),
                "elapsed_per_run_sec": run_elapsed,
                "elapsed_std_sec": round(statistics.pstdev(run_elapsed), 1),
                "per_question_success_rate_pct": [
                    round(sum(1 for r in range(total_runs) if results_all[r][q].get("agent_success")) / total_runs * 100, 1)
                    for q in range(nq)
                ],
                "per_question_pass_rate_pct": [
                    round(sum(1 for r in range(total_runs) if results_all[r][q]["passed"]) / total_runs * 100, 1)
                    for q in range(nq)
                ],
            }
        )

    # --- Сохраняем JSON ---
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    results_json = {
        "timestamp": timestamp,
        "num_questions": nq,
        "runs": total_runs,
        "pass_at_1": pass_count,
        "pass_rate_pct": pass_rate_pct,
        "avg_judge_score": avg_judge,
        "total_cost_usd": total_cost,
        "elapsed_sec": elapsed_total,
        "phoenix": phoenix_ok,
        "stability": stability,
        "results": results_all[0] if total_runs == 1 else results_all,
    }
    json_path = config.settings.EVALS_DIR / f"results_eval_{timestamp}.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(results_json, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- Markdown-отчёт ---
    md = [
        "# Отчёт по eval (golden set)",
        "",
        f"- Дата: {timestamp}",
        f"- Вопросов: {len(golden)}",
        f"- Прогонов: {total_runs}",
        f"- pass@1: {pass_count}/{len(all_entries)} ({pass_rate_pct}%)",
        f"- Средний judge-score: {avg_judge}/10",
        f"- Суммарная стоимость: ${total_cost}",
        f"- Время: {elapsed_total} с",
        f"- Phoenix: {'включён' if phoenix_ok else 'не включён'}",
        "",
    ]

    if total_runs > 1:
        st = stability
        md += [
            "## Стабильность",
            "",
            f"- pass@1 по прогонам: {st['pass_rate_per_run_pct']} (mean {st['pass_rate_mean_pct']}%, std {st['pass_rate_std_pct']}%)",
            f"- success по прогонам: mean {st['success_rate_mean_pct']}% (std {st['success_rate_std_pct']}%)",
            f"- стоимость по прогонам, $: {st['cost_per_run_usd']} (std {st['cost_std_usd']})",
            f"- время по прогонам, с: {st['elapsed_per_run_sec']} (std {st['elapsed_std_sec']})",
            "",
            "### По вопросам (доля успешных/сданных прогонов)",
            "",
            "| # | Вопрос | success, % | pass@1, % |",
            "|---|--------|-----------|-----------|",
        ]
        for q in range(nq):
            q_name = results_all[0][q]["question"][:50]
            md.append(
                f"| {q + 1} | {q_name} | {st['per_question_success_rate_pct'][q]} | "
                f"{st['per_question_pass_rate_pct'][q]} |"
            )
        md.append("")

    md += [
        "## Детали",
        "",
        "| # | Вопрос | pass@1 | Judge | Вердикт | success | stop_reason |",
        "|---|--------|--------|-------|---------|---------|-------------|",
    ]
    display = results_all[0] if total_runs == 1 else [e for run in results_all for e in run]
    for i, r in enumerate(display, start=1):
        md.append(
            f"| {i} | {r['question'][:60]} | {'PASS' if r['passed'] else 'FAIL'} | "
            f"{r['judge_score']} | {r['judge_verdict']} | {r['agent_success']} | {r['stop_reason']} |"
        )
    md.append("")
    md.append("## Комментарии judge")
    for r in display:
        md.append(f"**{r['question'][:60]}** — {r['judge_comment']}")
        md.append("")
    md.append(f"\nПодробности: `{json_path.name}`")

    md_path = config.settings.EVALS_DIR / f"report_{timestamp}.md"
    md_path.write_text("\n".join(md), encoding="utf-8")

    # --- Rich-таблица ---
    table = Table(title="Итоги eval", show_header=True, header_style="bold cyan")
    table.add_column("Метрика")
    table.add_column("Значение")
    table.add_row("pass@1", f"{pass_count}/{len(all_entries)} ({pass_rate_pct}%)")
    table.add_row("Средний judge-score", f"{avg_judge}/10")
    table.add_row("Стоимость", f"${total_cost}")
    table.add_row("Время", f"{elapsed_total} с")
    if total_runs > 1:
        table.add_row("Стабильность pass@1", f"mean {stability['pass_rate_mean_pct']}%, std {stability['pass_rate_std_pct']}%")
    console.print(table)
    console.print(f"[info]JSON: {json_path}[/info]")
    console.print(f"[info]Markdown: {md_path}[/info]")

    return 0


if __name__ == "__main__":
    sys.exit(main())
