"""plugin_loader 能力缝测试（dsh P2b：Provider 归属 / 无特权核心）

- 加载插件后能归属"哪个插件提供了哪些工具"（get_plugin_tool_map）
- get_plugin_tools 只返回插件贡献的工具（不再误含内置）
- 加载失败不阻塞其他插件
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core.tools import registry
import agent_harness.plugin_loader as pl


def _write_plugin(tdir: Path, name: str, body: str):
    p = tdir / f"{name}.py"
    p.write_text(body, encoding="utf-8")
    return p


def test_load_plugins_attributes_provider(monkeypatch, tmp_path):
    """插件注册的工具应归属到该插件 Provider。"""
    monkeypatch.setattr(pl, "_HARNESS_PLUGINS_DIR", tmp_path)
    registry.TOOL_REGISTRY.pop("prov_a_tool", None)

    _write_plugin(tmp_path, "prov_a", (
        "from agent_harness.core.tools.registry import register_tool\n"
        "def _f(): return 'a'\n"
        'register_tool("prov_a_tool", _f, {"description":"a","properties":{}}, privilege="read-only")\n'
    ))

    pl.load_plugins()
    tool_map = pl.get_plugin_tool_map()
    assert "prov_a_tool" in tool_map.get("prov_a", []), tool_map
    assert "prov_a_tool" in pl.get_plugin_tools()

    registry.TOOL_REGISTRY.pop("prov_a_tool", None)


def test_get_plugin_tools_only_plugin_contributed():
    """get_plugin_tools 应只含插件提供的工具，不误含内置工具。"""
    assert "think" not in pl.get_plugin_tools()
    assert "search" not in pl.get_plugin_tools()


def test_load_plugins_error_does_not_block(monkeypatch, tmp_path):
    """一个插件加载失败不应阻塞其他插件。"""
    monkeypatch.setattr(pl, "_HARNESS_PLUGINS_DIR", tmp_path)

    _write_plugin(tmp_path, "good", (
        "from agent_harness.core.tools.registry import register_tool\n"
        "def _g(): return 'g'\n"
        'register_tool("good_tool", _g, {"description":"g","properties":{}}, privilege="read-only")\n'
    ))
    _write_plugin(tmp_path, "bad", "raise RuntimeError('boom')\n")

    results = pl.load_plugins()
    by_name = {r["name"]: r for r in results}
    assert by_name["good"]["success"] is True
    assert by_name["bad"]["success"] is False
    assert "good_tool" in pl.get_plugin_tools()

    registry.TOOL_REGISTRY.pop("good_tool", None)
