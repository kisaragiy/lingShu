"""P1 记忆跨产品导入器测试"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

def test_memory_import_tool_registered():
    from agent_harness.core.tools.registry import TOOL_REGISTRY
    assert "memory_import" in TOOL_REGISTRY
    entry = TOOL_REGISTRY["memory_import"]
    assert entry.get("privilege") == "reversible"

def test_memory_import_empty_input():
    from agent_harness.core.tools.misc import _tool_memory_import
    result = json.loads(_tool_memory_import(""))
    assert not result.get("ok")
    assert "为空" in result.get("error", "")
