"""工具调用指标测试 — 失败原因分布的数据源。

验证 call_tool 每次调用都留下可聚合的指标：
- 成功 → status="success"
- 各类失败 → 按 error 前缀归类为可聚合的 reason（not_found/disabled/denied/...）
- 指标采集自身故障绝不影响工具调用结果（观测性故障不传导到业务）
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core import metrics
from agent_harness.core.tools import registry as reg


def _counter_value(tool: str, status: str) -> float:
    """读回当前计数器值（读不到视为 0）。"""
    try:
        return metrics.TOOL_CALLS_TOTAL.labels(tool, status)._value.get()
    except Exception:
        return 0.0


def _register_echo(name: str, privilege: str = "read-only"):
    def _echo(t: str):
        return t
    reg.register_tool(name, _echo, {
        "description": "回显(测试)", "properties": {"t": "string"},
    }, privilege=privilege)


def _cleanup(*names: str):
    for n in names:
        reg.TOOL_REGISTRY.pop(n, None)
    reg.PRE_HOOKS.clear()
    reg.POST_HOOKS.clear()


# ── 指标存在性 ──────────────────────────────────────

def test_tool_metric_declared():
    """工具指标必须声明存在（名字稳定，仪表盘/PromQL 依赖它）。"""
    assert metrics.TOOL_CALLS_TOTAL is not None
    assert metrics.TOOL_CALL_DURATION is not None


# ── 成功路径 ────────────────────────────────────────

def test_success_recorded():
    _register_echo("_m_ok")
    before = _counter_value("_m_ok", "success")
    try:
        res = reg.call_tool("_m_ok", t="hi")
        assert res["success"] is True
        assert _counter_value("_m_ok", "success") == before + 1
    finally:
        _cleanup("_m_ok")


# ── 失败原因分类（核心：失败不是一坨，要能分开统计）──

def test_unknown_tool_recorded_as_not_found():
    before = _counter_value("_m_missing", "not_found")
    res = reg.call_tool("_m_missing", t="x")
    assert res["success"] is False
    assert _counter_value("_m_missing", "not_found") == before + 1


def test_denied_tool_recorded_as_denied():
    """权限拒绝 → status=denied（与普通异常区分开）。"""
    _register_echo("_m_priv", privilege="irreversible")

    def _deny(name, source="harness", auto_confirm=True):
        return False

    import agent_harness.core.tools.permission as perm
    original = perm.check_permission
    perm.check_permission = _deny
    try:
        before = _counter_value("_m_priv", "denied")
        res = reg.call_tool("_m_priv", t="x")
        assert res["success"] is False
        assert "权限拒绝" in res["error"]
        assert _counter_value("_m_priv", "denied") == before + 1
    finally:
        perm.check_permission = original
        _cleanup("_m_priv")


def test_tool_exception_recorded_as_error():
    """工具内部抛异常 → status=error（与 not_found/denied 分开）。"""
    def _boom(t: str):
        raise ValueError("boom")
    reg.register_tool("_m_boom", _boom, {"description": "炸", "properties": {}},
                      privilege="read-only")
    try:
        before = _counter_value("_m_boom", "error")
        res = reg.call_tool("_m_boom", t="x")
        assert res["success"] is False
        assert "boom" in res["error"]
        assert _counter_value("_m_boom", "error") == before + 1
    finally:
        _cleanup("_m_boom")


@pytest.mark.parametrize("error,expected", [
    ("工具不存在: x", "not_found"),
    ("[开关拒绝] 工具 'x' 已被禁用", "disabled"),
    ("[权限拒绝] 工具 'x' 不可逆", "denied"),
    ("[需要确认] 操作被安全护栏拦截", "needs_confirm"),
    ("[安全护栏不可用] 已拒绝不可逆操作", "guard_unavailable"),
    ("工具执行超时 (30s)", "timeout"),
    ("Connection refused", "error"),
    (None, "error"),
])
def test_classify_failure(error, expected):
    assert metrics.classify_tool_failure(error) == expected


# ── 观测性故障不传导（关键：指标不能拖垮业务）──

def test_metric_failure_does_not_break_call_tool(monkeypatch):
    """指标采集抛异常时，工具调用结果必须原样返回。"""
    _register_echo("_m_safe")

    def _explode(*a, **kw):
        raise RuntimeError("metrics backend down")

    monkeypatch.setattr(metrics, "record_tool_call", _explode)
    try:
        res = reg.call_tool("_m_safe", t="still-works")
        assert res["success"] is True
        assert res["data"] == "still-works"
    finally:
        _cleanup("_m_safe")


# ── 耗时被观测 ──────────────────────────────────────

def test_duration_observed():
    _register_echo("_m_dur")
    try:
        reg.call_tool("_m_dur", t="x")
        count = metrics.TOOL_CALL_DURATION.labels("_m_dur")._sum.get()
        assert count > 0
    finally:
        _cleanup("_m_dur")


# ── 结果有效性层（调用成功 ≠ 结果有用）─────────────

def test_tool_result_verdict_recorded():
    """「调用成功但结果无效」必须能被单独看见（搜索降级返回 / 空结果）。

    只测 call_tool 的 success 不够：搜索后端挂了会返回降级文本，仍是 success。
    """
    before = metrics.TOOL_RESULTS_TOTAL.labels("_m_res", "error")._value.get()
    metrics.record_tool_result("_m_res", "error")
    assert metrics.TOOL_RESULTS_TOTAL.labels("_m_res", "error")._value.get() == before + 1


def test_tool_result_empty_verdict_becomes_unknown():
    """空 verdict 归 unknown —— 不产生空标签序列（PromQL 里查不到）。"""
    before = metrics.TOOL_RESULTS_TOTAL.labels("_m_res2", "unknown")._value.get()
    metrics.record_tool_result("_m_res2", "")
    assert metrics.TOOL_RESULTS_TOTAL.labels("_m_res2", "unknown")._value.get() == before + 1


def test_worker_wires_result_verdict():
    """接线锁：Worker 跑完工具必须记 verdict，否则结果有效性层永远是空的。"""
    import inspect

    from agent_harness.core.agents import workers
    src = inspect.getsource(workers)
    assert "record_tool_result" in src, (
        "workers.py 未调用 record_tool_result —— harness_tool_results_total 将恒为 0"
    )
    assert "validate_result" in src, "validate_result 调用点不见了（verdict 的数据源）"
