"""工具超时策略测试（dsh P2a：超时做成可替换 seam）

- get_timeout 默认策略（每工具 + default 兜底）
- set_default_timeout 覆盖
- set_timeout_policy 替换 lookup seam（"超时即插件"）
- call_with_timeout 快工具正常返回 / 慢工具超时抛 TimeoutError
- call_tool 对超时工具返回 {"success": False, error 含"超时"}
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core.tools import registry, timeout as to


# ─── get_timeout 默认策略 ───

def test_get_timeout_known_tool():
    assert to.get_timeout("search") == 30.0
    assert to.get_timeout("comfyui_text2img") == 180.0


def test_get_timeout_default_fallback():
    # 未配置的工具 → 用 DEFAULT_TIMEOUT
    assert to.get_timeout("no_such_tool") == to.DEFAULT_TIMEOUT == 60.0
    # 本地小工具为 0（不超时）
    assert to.get_timeout("think") == 0.0


def test_set_default_timeout_overrides():
    to.set_default_timeout("search", 5.0)
    try:
        assert to.get_timeout("search") == 5.0
    finally:
        to.set_default_timeout("search", 30.0)  # 还原


# ─── 可替换 seam ───

def test_timeout_policy_seam(monkeypatch):
    def policy(tool):
        return 99.0
    to.set_timeout_policy(policy)
    try:
        assert to.get_timeout("anything") == 99.0
        assert to.get_timeout("search") == 99.0
    finally:
        to.set_timeout_policy(None)


# ─── call_with_timeout ───

def test_call_with_timeout_fast_returns():
    res = to.call_with_timeout(lambda x: x * 2, {"x": 3}, timeout=5.0)
    assert res == 6


def test_call_with_timeout_no_timeout_sync():
    # timeout<=0 → 同步直接跑
    assert to.call_with_timeout(lambda: "ok", {}, timeout=0.0) == "ok"


def test_call_with_timeout_slow_raises():
    def slow():
        time.sleep(0.2)
        return "ok"
    with pytest.raises(TimeoutError):
        to.call_with_timeout(slow, {}, timeout=0.05)


def test_call_with_timeout_propagates_error():
    def boom():
        raise ValueError("工具内部错误")
    with pytest.raises(ValueError):
        to.call_with_timeout(boom, {}, timeout=5.0)


# ─── call_tool 应用超时 ───

def test_call_tool_timeout_returns_failure(monkeypatch):
    # 注册一个慢工具
    def _slow_tool():
        time.sleep(0.3)
        return "done"
    registry.register_tool("_p2_slow_tool", _slow_tool, {
        "description": "慢工具(测试)", "properties": {},
    }, privilege="read-only")
    to.set_default_timeout("_p2_slow_tool", 0.05)
    try:
        res = registry.call_tool("_p2_slow_tool")
        assert res["success"] is False
        assert "超时" in res["error"]
    finally:
        # 还原 + 清理
        to.set_default_timeout("_p2_slow_tool", 0.0)
        registry.TOOL_REGISTRY.pop("_p2_slow_tool", None)


def test_call_tool_fast_still_works():
    def _echo(t: str):
        return t
    registry.register_tool("_p2_echo_tool", _echo, {
        "description": "回显(测试)", "properties": {"t": "string"},
    }, privilege="read-only")
    try:
        res = registry.call_tool("_p2_echo_tool", t="hi")
        assert res["success"] is True
        assert res["data"] == "hi"
    finally:
        registry.TOOL_REGISTRY.pop("_p2_echo_tool", None)
