"""P0-7：工具超时不能是「假超时」—— 超时后副作用仍在跑、调用方以为失败会重试。

缺陷实测（gpt6 评审 P0-7）：``call_with_timeout`` 用 daemon 线程执行，
``q.get(timeout=)`` 超时即抛 TimeoutError，但**不取消底层线程**；
``timeout=0.01`` 抛错后，被包裹的工作继续执行到完成（副作用重复）。

修复后要求：
  1. 有副作用（irreversible/reversible 写类）的工具**不套假超时**，同步执行到结束；
  2. 真超时的文案明确「已放弃等待（底层可能仍在执行）」，调用方不会误判成「没执行」。
"""
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core.tools import registry, timeout as to  # noqa: E402


def test_side_effect_call_is_not_abandoned():
    """side_effect=True：不允许「调用方超时返回、底层继续跑」。"""
    counter = {"n": 0}

    def work():
        time.sleep(0.05)
        counter["n"] += 1
        return "ok"

    result = to.call_with_timeout(work, {}, timeout=0.01, side_effect=True)
    assert result == "ok"
    assert counter["n"] == 1


def test_read_only_call_still_times_out():
    def slow():
        time.sleep(0.5)
        return "late"

    with pytest.raises(TimeoutError):
        to.call_with_timeout(slow, {}, timeout=0.05)


def test_timeout_message_says_underlying_may_continue():
    def slow():
        time.sleep(0.3)
        return "late"

    with pytest.raises(TimeoutError) as ei:
        to.call_with_timeout(slow, {}, timeout=0.02)
    msg = str(ei.value)
    assert "放弃等待" in msg or "可能仍在执行" in msg, f"超时文案会误导调用方重试: {msg}"


def test_irreversible_tool_skips_fake_timeout(monkeypatch):
    """registry 层：irreversible 工具即便配了超时也不走假超时路径。"""
    calls = {"n": 0}

    def side_effect_tool(value: str = "x"):
        time.sleep(0.05)
        calls["n"] += 1
        return f"did-{value}"

    name = "p0_7_side_effect_probe"
    registry.register_tool(name, side_effect_tool, {"type": "object", "properties": {}},
                           privilege="reversible")
    try:
        to.set_default_timeout(name, 0.01)
        res = registry.call_tool(name, value="a")
        assert res["success"] is True, res
        assert calls["n"] == 1, "副作用执行次数不为 1（超时后被放弃但底层仍在跑）"
    finally:
        registry.TOOL_REGISTRY.pop(name, None)
        to.DEFAULT_TIMEOUTS.pop(name, None)
