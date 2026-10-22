"""Evaluation Scorer — scores agent outputs against expected results.

评分维度（100 分）：
  - Keyword match 20：输出是否含期望关键词（表面）
  - **Factual 20**：输出的事实断言是否成立（实质）—— ENG-40
  - Tool usage 30：是否用了对的工具
  - Latency 15：越快越好
  - Token cost 15：越省越好

为什么要有 factual 这一维：原来 keyword 独占 40 分，但「市值 1 亿」和「市值 1 万亿」
只要都含关键词就同样高分 —— 分数证明的是「输出了相关词」，不是「内容对」。
factual 维度把「必须出现的数字 / 不得出现的数字 / 必须引用的来源 / 未核验是否标注」
变成可机械复现的断言（复用 graph_multi._normalize_number_tokens 的中文单位口径）。

没有定义 factual 断言的任务给满分并标记 `asserted: False` —— 不假装测了没测的东西。
"""

from dataclasses import dataclass, field


@dataclass
class EvalScore:
    """Score for a single eval task."""
    task_id: str
    passed: bool
    total_score: float         # 0-100
    keyword_score: float       # 0-20
    tool_score: float          # 0-30
    latency_score: float       # 0-15
    cost_score: float          # 0-15
    factual_score: float = 0.0  # 0-20（无断言时满分）
    details: dict = field(default_factory=dict)

    def summary(self) -> str:
        status = "✅ PASS" if self.passed else "❌ FAIL"
        return (f"{status} {self.task_id}: {self.total_score:.0f}/100 "
                f"(keywords:{self.keyword_score:.0f} factual:{self.factual_score:.0f} "
                f"tool:{self.tool_score:.0f} latency:{self.latency_score:.0f} "
                f"cost:{self.cost_score:.0f})")


def _to_number(value) -> float | None:
    """把 1.2e12 / "1.2万亿" / "987亿" 这类值转成同一口径的 float。"""
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    text = str(value).strip().replace(",", "")
    try:
        return round(float(text), 2)
    except ValueError:
        pass
    try:
        from agent_harness.core.graph.graph_multi import _normalize_number_tokens
        nums = _normalize_number_tokens(text)
        return max(nums) if nums else None
    except Exception:
        return None


def _insignificant(value: float | None) -> bool:
    """与 graph_multi._normalize_number_tokens 的保守判据对齐：0-9 的整数不提取。

    这种数出现在断言里会永远判 fail，所以要显式跳过并提示作者。
    """
    if value is None:
        return False
    return float(value) == int(value) and 0 <= value <= 9


def _factual_score(task: dict, output: str) -> tuple[float, dict]:
    """事实性 20 分。断言来自 task["factual"]：

    - must_have_numbers:     必须出现的数字（该说的没说 → 扣分）
    - must_not_have_numbers: 不得出现的数字（编造数字 → 扣分）
    - must_cite_domains:     必须出现的来源域名（无来源裸答 → 扣分）
    - requires_fact_check_note: 报告必须标注「未核验」或「未找到依据」
                              （静默通过 → 扣分，对应 ENG-33）
    """
    spec = task.get("factual") or {}
    if not spec:
        return 20.0, {"asserted": False, "reason": "任务未定义 factual 断言，不计入"}

    out_lower = (output or "").lower()
    out_nums: set = set()
    try:
        from agent_harness.core.graph.graph_multi import _normalize_number_tokens
        out_nums = _normalize_number_tokens(output or "")
    except Exception:
        pass

    checks: list[dict] = []
    skipped: list[str] = []
    for raw in spec.get("must_have_numbers", []):
        v = _to_number(raw)
        if _insignificant(v):
            # _normalize_number_tokens 刻意剔除 0-9 的单位数（防噪音）；
            # 断言若写了这种数会永远判 fail —— 显式跳过并落 details，不静默。
            skipped.append(f"must_have:{raw}")
            continue
        checks.append({"check": f"must_have:{raw}", "pass": v is not None and v in out_nums})
    for raw in spec.get("must_not_have_numbers", []):
        v = _to_number(raw)
        if _insignificant(v):
            skipped.append(f"must_not_have:{raw}")
            continue
        checks.append({"check": f"must_not_have:{raw}", "pass": v is None or v not in out_nums})
    for domain in spec.get("must_cite_domains", []):
        checks.append({
            "check": f"cite:{domain}",
            "pass": str(domain).lower() in out_lower,
        })
    if spec.get("requires_fact_check_note"):
        checks.append({
            "check": "fact_check_note",
            "pass": ("未核验" in (output or "")) or ("未找到依据" in (output or "")),
        })

    if not checks:
        return 20.0, {
            "asserted": False,
            "reason": "factual 断言为空或全为不显著数字（0-9 会被数字提取器剔除）",
            "skipped_insignificant": skipped,
        }

    ok = sum(1 for c in checks if c["pass"])
    failed = [c["check"] for c in checks if not c["pass"]]
    return 20.0 * ok / len(checks), {
        "asserted": True,
        "checks": checks,
        "failed": failed,
        "passed_count": ok,
        "total_checks": len(checks),
        "skipped_insignificant": skipped,
    }


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
      - Keyword match: 20 points (proportional to matched keywords)
      - Factual assertions: 20 points (proportional to passed checks)
      - Tool usage: 30 points (correct tool used)
      - Latency: 15 points (faster = better, <5s = full, >30s = 0)
      - Token cost: 15 points (fewer tokens = better, <500 = full, >5000 = 0)

    Pass threshold: >= 60 points
    """
    task_id = task["id"]
    expected_keywords = task.get("expected_keywords", [])
    expected_tool = task.get("expected_tool_used", "")

    # ─── Keyword score (20 points) ───
    if expected_keywords:
        matched = sum(1 for kw in expected_keywords if kw.lower() in (output or "").lower())
        keyword_score = (matched / len(expected_keywords)) * 20
    else:
        keyword_score = 20  # no keyword check = full points

    # ─── Factual score (20 points) ───
    factual_score, factual_detail = _factual_score(task, output or "")

    # ─── Tool score (30 points) ───
    tool_score = (30 if expected_tool in tool_calls else 0) if expected_tool else 30

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

    total = keyword_score + factual_score + tool_score + latency_score + cost_score
    passed = total >= 60

    return EvalScore(
        task_id=task_id,
        passed=passed,
        total_score=total,
        keyword_score=keyword_score,
        tool_score=tool_score,
        latency_score=latency_score,
        cost_score=cost_score,
        factual_score=factual_score,
        details={
            "output_preview": (output or "")[:200],
            "tool_calls": tool_calls,
            "expected_tool": expected_tool,
            "expected_keywords": expected_keywords,
            "factual": factual_detail,
            "latency_s": round(latency_s, 1),
            "total_tokens": total_tokens,
        },
    )
