"""Evaluation Scorer — scores agent outputs against expected results."""

from dataclasses import dataclass, field


@dataclass
class EvalScore:
    """Score for a single eval task."""
    task_id: str
    passed: bool
    total_score: float         # 0-100
    keyword_score: float       # 0-40
    tool_score: float          # 0-30
    latency_score: float       # 0-15
    cost_score: float          # 0-15
    details: dict = field(default_factory=dict)

    def summary(self) -> str:
        status = "✅ PASS" if self.passed else "❌ FAIL"
        return (f"{status} {self.task_id}: {self.total_score:.0f}/100 "
                f"(keywords:{self.keyword_score:.0f} tool:{self.tool_score:.0f} "
                f"latency:{self.latency_score:.0f} cost:{self.cost_score:.0f})")


def score_task(
    task: dict,
    output: str,
    tool_calls: list[str],
    trace_tree=None,
    latency_s: float = 0,
    total_tokens: int = 0,
) -> EvalScore:
    """Score a single eval task.

    Scoring rubric:
      - Keyword match: 40 points (proportional to matched keywords)
      - Tool usage: 30 points (correct tool used)
      - Latency: 15 points (faster = better, <5s = full, >30s = 0)
      - Token cost: 15 points (fewer tokens = better, <500 = full, >5000 = 0)

    Pass threshold: >= 60 points
    """
    task_id = task["id"]
    expected_keywords = task.get("expected_keywords", [])
    expected_tool = task.get("expected_tool_used", "")

    # ─── Keyword score (40 points) ───
    if expected_keywords:
        matched = sum(1 for kw in expected_keywords if kw.lower() in output.lower())
        keyword_score = (matched / len(expected_keywords)) * 40
    else:
        keyword_score = 40  # no keyword check = full points

    # ─── Tool score (30 points) ───
    if expected_tool:
        tool_score = 30 if expected_tool in tool_calls else 0
    else:
        tool_score = 30  # no tool expected = full points

    # ─── Latency score (15 points) ───
    if latency_s <= 5:
        latency_score = 15
    elif latency_s <= 15:
        latency_score = 10
    elif latency_s <= 30:
        latency_score = 5
    else:
        latency_score = 0

    # ─── Cost score (15 points) ───
    if total_tokens <= 500:
        cost_score = 15
    elif total_tokens <= 1500:
        cost_score = 10
    elif total_tokens <= 5000:
        cost_score = 5
    else:
        cost_score = 0

    total = keyword_score + tool_score + latency_score + cost_score
    passed = total >= 60

    return EvalScore(
        task_id=task_id,
        passed=passed,
        total_score=total,
        keyword_score=keyword_score,
        tool_score=tool_score,
        latency_score=latency_score,
        cost_score=cost_score,
        details={
            "output_preview": output[:200],
            "tool_calls": tool_calls,
            "expected_tool": expected_tool,
            "expected_keywords": expected_keywords,
            "latency_s": round(latency_s, 1),
            "total_tokens": total_tokens,
        },
    )
