"""工具三阶瀑布测试（dsh 最后一项：pre/execute/post 可插拔管线）

- call_tool 默认定时/权限/规范化行为保持（无回归）
- register_pre_hook 拦截：pre 阶段返回 block → 不执行工具
- register_post_hook 变换：post 阶段改写结果
- 钩子清理后回到默认行为
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core.tools import registry as reg


def _cleanup_tool():
    reg.TOOL_REGISTRY.pop("_p3_echo", None)
    # 清空注册的钩子，避免跨测试污染
    reg.PRE_HOOKS.clear()
    reg.POST_HOOKS.clear()


def test_call_tool_default_preserved():
    """默认行为保持：注册一个 read-only 工具，正常返回。"""
    def _echo(t: str):
        return t
    reg.register_tool("_p3_echo", _echo, {
        "description": "回显(测试)", "properties": {"t": "string"},
    }, privilege="read-only")
    try:
        res = reg.call_tool("_p3_echo", t="hi")
        assert res["success"] is True
        assert res["data"] == "hi"
    finally:
        _cleanup_tool()


def test_register_pre_hook_blocks():
    """pre 阶段注册的钩子返回 block → 拦截，不执行工具。"""
    def _echo(t: str):
        return t
    reg.register_tool("_p3_echo", _echo, {
        "description": "回显(测试)", "properties": {"t": "string"},
    }, privilege="read-only")
    called = {"n": 0}

    def pre_hook(name, kwargs):
        if name == "_p3_echo":
            return {"success": False, "error": "pre-hook-blocked", "data": None}
        return None
    reg.register_pre_hook(pre_hook)
    try:
        res = reg.call_tool("_p3_echo", t="x")
        assert res["success"] is False
        assert "pre-hook-blocked" in res["error"]
    finally:
        _cleanup_tool()


def test_register_post_hook_transforms():
    """post 阶段注册的钩子改写结果。"""
    def _echo(t: str):
        return t
    reg.register_tool("_p3_echo", _echo, {
        "description": "回显(测试)", "properties": {"t": "string"},
    }, privilege="read-only")

    def post_hook(name, result, kwargs):
        if name == "_p3_echo" and result["success"]:
            result = {**result, "data": result.get("data") + "|post"}
        return result
    reg.register_post_hook(post_hook)
    try:
        res = reg.call_tool("_p3_echo", t="hi")
        assert res["success"] is True
        assert res["data"] == "hi|post"
    finally:
        _cleanup_tool()


def test_hook_cleanup_restores_default():
    """清空钩子后回到默认行为（无 block/无变换）。"""
    def _echo(t: str):
        return t
    reg.register_tool("_p3_echo", _echo, {
        "description": "回显(测试)", "properties": {"t": "string"},
    }, privilege="read-only")
    # 先注册会 block 的钩子
    reg.register_pre_hook(lambda name, kwargs: {"success": False, "error": "b", "data": None} if name == "_p3_echo" else None)
    try:
        assert reg.call_tool("_p3_echo", t="x")["success"] is False
    finally:
        reg.PRE_HOOKS.clear()
    # 清理后应正常
    res = reg.call_tool("_p3_echo", t="ok")
    assert res["success"] is True
    assert res["data"] == "ok"
    _cleanup_tool()
