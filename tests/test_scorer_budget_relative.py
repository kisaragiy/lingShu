"""P1-11：latency/cost 改「相对基线」后，分数随规模正常化，而真退化仍被扣。

原口径（≤5s 满分、>30s 得 0；≤500tok 满分、>5000 得 0）对 multi-agent 路径
（3 worker × 多轮 replan、熔断 600s）意味着两维恒 0、总分上限 70，任一内容
维度失手即 fail —— 惩罚规模而非质量。这里把新契约钉死。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.apps.research.eval.budget import (  # noqa: E402
    DEFAULT_BASELINE,
    BudgetBaseline,
    ratio_score,
    resolve_baseline,
)
from agent_harness.apps.research.eval.scorer import score_task  # noqa: E402

TASK = {"id": "t1", "expected_keywords": ["a"], "expected_tool_used": "search"}


def _score(**kwargs):
    return score_task(TASK, "a", ["search"], **kwargs)


# ─── 报告验收项：同一输出，latency 40s → 20s，分数单调上升 ───

def test_latency_score_is_monotonic_not_double_zero():
    base = BudgetBaseline(latency_s=30, tokens=3000)
    slow = _score(latency_s=40, total_tokens=1000, baseline=base)
    fast = _score(latency_s=20, total_tokens=1000, baseline=base)
    assert slow.latency_score > 0, "40s 在旧口径下是 0——新口径不该再是 0"
    assert fast.latency_score > slow.latency_score
    assert fast.total_score > slow.total_score


def test_normal_multi_agent_run_no_longer_scores_zero_on_time_and_cost():
    """120s / 8000tok 是常规 multi-agent run；旧口径这两维双 0（总分上限 70）。"""
    score = _score(latency_s=120, total_tokens=8000)
    assert score.latency_score > 0 and score.cost_score > 0
    assert score.details["budget_baseline"]["source"] == "default_multi_agent"


def test_real_degradation_still_zeroed():
    """320s / 48000tok ≈ 默认基线的 1.8×/3.2× —— 真退化必须还被扣。"""
    score = _score(latency_s=320, total_tokens=48000)
    assert score.cost_score == 0.0
    assert score.latency_score <= 15 * 0.5


# ─── ratio_score 分档边界 ───

def test_ratio_score_tiers():
    assert ratio_score(100, 100, 15) == 15          # ≤1.2×
    assert ratio_score(120, 100, 15) == 15
    assert ratio_score(150, 100, 15) == 7.5          # ≤2×
    assert ratio_score(250, 100, 15) == 3.0          # ≤3× → 20%
    assert ratio_score(400, 100, 15) == 0.0
    assert ratio_score(999, 0, 15) == 15, "基线为 0 时给足分，不能除零"


# ─── 基线来源优先级 ───

def test_task_budget_beats_default_and_mapping():
    task = {"id": "t1", "budget": {"latency_s": 10, "tokens": 1000}}
    ref = resolve_baseline(task, {"t1": {"latency_s": 99, "tokens": 99}})
    assert ref.source == "task.budget" and ref.latency_s == 10


def test_mapping_used_when_task_declares_nothing():
    ref = resolve_baseline({"id": "t1"}, {"t1": {"latency_s": 45, "tokens": 4500}})
    assert ref.source == "baselines[t1]" and ref.tokens == 4500


def test_default_when_nothing_declared():
    assert resolve_baseline({"id": "nope"}, {}) is DEFAULT_BASELINE


# ─── 可观测性：单维崩塌不被总分掩盖 ───

def test_details_expose_baseline_and_passed_dimensions():
    score = _score(latency_s=5, total_tokens=100)
    details = score.details
    assert details["budget_baseline"]["latency_s"] == DEFAULT_BASELINE.latency_s
    assert set(details["passed_dimensions"]) == {"keyword", "factual", "tool", "latency", "cost"}


def test_broken_content_cannot_pass_on_cheap_and_fast_alone():
    """内容全崩但跑得很快：上限 30（latency+cost），仍 < 60 → fail。"""
    task = {"id": "t2", "expected_keywords": ["绝不可能出现"], "expected_tool_used": "search"}
    score = score_task(task, "完全不相关", [], latency_s=1, total_tokens=10)
    assert score.passed is False
    assert score.latency_score + score.cost_score == 30
