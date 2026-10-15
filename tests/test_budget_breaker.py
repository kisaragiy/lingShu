"""P0-4 熔断 — 日聚合预算 / session kill switch / 无进展检测。

覆盖：
- DailyBudget：按日累计 cost，超 warn/halt 阈值返回停机信号；跨日自动重置
- TokenBudget session kill switch：kill() 后 can_continue 恒 False
- with_no_progress_guard：连续 K 次「同指纹」调用 → 抛 NoProgressError；指纹变化归零
"""
import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from agent_harness.core.budget import DailyBudget, DailyLevel, TokenBudget, BudgetLevel
from agent_harness.core.retry import (
    NoProgressError,
    with_no_progress_guard,
    with_retry,
)


# ═══════════════ DailyBudget 日聚合熔断 ═══════════════

def test_daily_budget_default_ok():
    db = DailyBudget(daily_limit_usd=10.0)
    assert db.check() == DailyLevel.OK
    assert db.can_continue() is True


def test_daily_budget_warn_threshold():
    db = DailyBudget(daily_limit_usd=10.0, warn_at=0.7, halt_at=1.0)
    lvl = db.add_cost(7.5)          # 75% → warn
    assert lvl == DailyLevel.WARN
    assert db.can_continue() is True


def test_daily_budget_halt_threshold():
    db = DailyBudget(daily_limit_usd=10.0, warn_at=0.7, halt_at=1.0)
    lvl = db.add_cost(10.5)         # >100% → halt
    assert lvl == DailyLevel.HALT
    assert db.can_continue() is False


def test_daily_budget_accumulates_across_calls():
    db = DailyBudget(daily_limit_usd=10.0)
    db.add_cost(3.0)
    db.add_cost(3.0)
    assert db.used_cost == pytest.approx(6.0)
    assert db.check() == DailyLevel.OK      # 6/10 = 60% < 70%
    db.add_cost(2.0)                         # 8/10 = 80% ≥ warn
    assert db.check() == DailyLevel.WARN


def test_daily_budget_halt_is_sticky():
    db = DailyBudget(daily_limit_usd=1.0)
    db.add_cost(5.0)
    assert db.can_continue() is False
    assert db.check() == DailyLevel.HALT


def test_daily_budget_rollover_resets():
    db = DailyBudget(daily_limit_usd=1.0)
    db.add_cost(5.0)
    assert db.halted is True
    db.day = "1970-01-01"           # 假装到了另一天
    assert db.check() == DailyLevel.OK
    assert db.can_continue() is True
    assert db.used_cost == 0.0


def test_daily_budget_summary():
    db = DailyBudget(daily_limit_usd=2.0)
    db.add_cost(1.0)
    s = db.summary()
    assert s["used_cost_usd"] == pytest.approx(1.0)
    assert s["daily_limit_usd"] == 2.0
    assert s["level"] == "ok"               # 50% < 70%
    assert s["halted"] is False


# ═══════════════ session kill switch ═══════════════

def test_kill_switch_blocks_can_continue():
    b = TokenBudget(soft_limit=1000)
    assert b.can_continue(BudgetLevel.OK) is True
    b.kill("用户在设置页点了停止")
    assert b.killed is True
    assert b.can_continue(BudgetLevel.OK) is False
    assert b.can_continue(BudgetLevel.WARN) is False


def test_kill_switch_not_set_by_default():
    b = TokenBudget()
    assert b.killed is False
    assert b.can_continue(BudgetLevel.OK) is True


def test_kill_switch_reason_recorded():
    b = TokenBudget()
    b.kill("成本异常")
    assert "成本异常" in b.kill_reason


# ═══════════════ with_no_progress_guard ═══════════════

def test_no_progress_trips_after_k_repeats():
    calls = []

    @with_no_progress_guard(max_repeats=3)
    def do(tool, arg):
        calls.append((tool, arg))
        return "same"

    do("search", "x")
    do("search", "x")            # 连续 2 次，尚未熔断
    with pytest.raises(NoProgressError):
        do("search", "x")        # 第 3 次连续同参 → 熔断
    assert calls == [("search", "x"), ("search", "x")], "熔断那一次不应真正执行"


def test_no_progress_resets_on_changed_args():
    calls = []

    @with_no_progress_guard(max_repeats=3)
    def do(tool, arg):
        calls.append(arg)
        return arg

    do("search", "a")
    do("search", "a")
    do("search", "b")            # 指纹变化 → 计数归零
    do("search", "b")
    do("search", "c")
    assert calls == ["a", "a", "b", "b", "c"]  # 全程未熔断


def test_no_progress_custom_key_fn():
    """key_fn 注入：只按 tool 名判重（同 tool 不同参也视为无进展）。"""
    calls = []

    @with_no_progress_guard(max_repeats=2, key_fn=lambda *a, **k: a[0])
    def do(tool, arg):
        calls.append(arg)
        return arg

    do("search", "a")
    with pytest.raises(NoProgressError):
        do("search", "b")        # 同 tool → 指纹相同 → 第 2 次熔断
    assert calls == ["a"]


def test_no_progress_error_metadata():
    @with_no_progress_guard(max_repeats=1)
    def do(x):
        return x

    with pytest.raises(NoProgressError) as ei:
        do("x")
    assert ei.value.code == "NO_PROGRESS"
    assert ei.value.repeats >= 1


def test_no_progress_guard_passthrough_value():
    @with_no_progress_guard(max_repeats=5)
    def do(x):
        return x * 2

    assert do(21) == 42


def test_no_progress_async():
    calls = []

    @with_no_progress_guard(max_repeats=2)
    async def do(x):
        calls.append(x)
        return x

    async def main():
        await do(1)
        with pytest.raises(NoProgressError):
            await do(1)

    asyncio.run(main())
    assert calls == [1]


def test_with_retry_still_works():
    """新增 guard 不影响既有 with_retry 行为。"""
    n = {"c": 0}

    @with_retry(max_attempts=3, base_delay=0.0, retryable_exceptions=(ValueError,))
    def flaky():
        n["c"] += 1
        if n["c"] < 2:
            raise ValueError("boom")
        return "ok"

    assert flaky() == "ok"
    assert n["c"] == 2
