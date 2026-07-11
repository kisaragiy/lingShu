"""Evaluation Runner — runs all eval tasks through Agent Harness and reports scores."""

import json
import os
import time
from dataclasses import dataclass, field

from .dataset import CI_TASKS, EVAL_DATASET, NETWORK_TASKS
from .scorer import EvalScore, score_task


@dataclass
class EvalReport:
    """Complete evaluation report."""
    timestamp: str
    total_tasks: int
    passed: int
    failed: int
    skipped: int
    avg_score: float
    total_latency_s: float
    total_tokens: int
    scores: list[EvalScore] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"\n{'='*60}",
            "  Agent Harness Evaluation Report",
            f"  {self.timestamp}",
            f"{'='*60}",
            f"  Tasks: {self.total_tasks} total, {self.passed} passed, "
            f"{self.failed} failed, {self.skipped} skipped",
            f"  Avg Score: {self.avg_score:.0f}/100",
            f"  Total Latency: {self.total_latency_s:.1f}s",
            f"  Total Tokens: {self.total_tokens:,}",
            f"{'='*60}",
        ]
        for s in self.scores:
            lines.append(f"  {s.summary()}")
        if self.failures:
            lines.append("\n  Failures:")
            for f in self.failures:
                lines.append(f"    - {f}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "total_tasks": self.total_tasks,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "avg_score": round(self.avg_score, 1),
            "total_latency_s": round(self.total_latency_s, 1),
            "total_tokens": self.total_tokens,
            "scores": [
                {
                    "task_id": s.task_id,
                    "passed": s.passed,
                    "total_score": s.total_score,
                    "details": s.details,
                }
                for s in self.scores
            ],
            "failures": self.failures,
        }


def run_eval(
    runner_func,
    tasks: list[dict] | None = None,
    skip_network: bool = True,
    verbose: bool = True,
) -> EvalReport:
    """Run evaluation on all or selected tasks.

    Args:
        runner_func: Function(task_request) → {"final_output": str, "trace_tree": ..., "elapsed_s": float}
        tasks: Specific tasks to run (default: all non-network tasks if skip_network)
        skip_network: Skip tasks that require network access
        verbose: Print per-task results

    Returns:
        EvalReport with aggregated scores
    """
    if tasks is None:
        tasks = CI_TASKS if skip_network else EVAL_DATASET

    from datetime import datetime
    report = EvalReport(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        total_tasks=len(tasks),
        passed=0,
        failed=0,
        skipped=0,
        avg_score=0,
        total_latency_s=0,
        total_tokens=0,
    )

    for task in tasks:
        task_id = task["id"]

        # Skip network tasks if offline
        if skip_network and task_id in NETWORK_TASKS:
            report.skipped += 1
            if verbose:
                print(f"  ⏭ {task_id}: skipped (network)")
            continue

        if verbose:
            print(f"\n  📋 {task_id}: {task['request'][:60]}...")

        t0 = time.time()
        try:
            result = runner_func(task["request"])
            output = result.get("final_output", "")

            # Extract tool calls from trace
            trace_tree = result.get("trace_tree")
            tool_calls = []
            total_tokens = 0
            if trace_tree:
                tool_calls = list(trace_tree.tool_stats.keys())
                total_tokens = trace_tree.total_tokens

            latency = result.get("elapsed_s", time.time() - t0)

            score = score_task(
                task=task,
                output=output,
                tool_calls=tool_calls,
                trace_tree=trace_tree,
                latency_s=latency,
                total_tokens=total_tokens,
            )
        except Exception as e:
            score = EvalScore(
                task_id=task_id,
                passed=False,
                total_score=0,
                keyword_score=0,
                tool_score=0,
                latency_score=0,
                cost_score=0,
                details={"error": str(e)},
            )
            report.failures.append(f"{task_id}: {e}")

        report.scores.append(score)
        if score.passed:
            report.passed += 1
        else:
            report.failed += 1
        report.total_latency_s += score.details.get("latency_s", 0)
        report.total_tokens += score.details.get("total_tokens", 0)

    # Calculate average
    if report.scores:
        report.avg_score = sum(s.total_score for s in report.scores) / len(report.scores)

    return report


def save_report(report: EvalReport, path: str = ""):
    """Save eval report to JSON file."""
    if not path:
        path = f"eval_report_{report.timestamp.replace(' ', '_').replace(':', '')}.json"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
    return path
