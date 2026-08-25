"""Reverse engineering utility tests — protobuf recovery, Stalker tool registration, Ghidra scripts"""

import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def test_tools_registered():
    from agent_harness.core.tools.registry import TOOL_REGISTRY
    assert "frida_stalker" in TOOL_REGISTRY
    assert "protobuf_recover" in TOOL_REGISTRY
    assert TOOL_REGISTRY["protobuf_recover"]["privilege"] == "read-only"
    assert TOOL_REGISTRY["frida_stalker"]["privilege"] == "irreversible"


def test_protobuf_recover_varint():
    from agent_harness.core.tools.rev_utils import _tool_protobuf_recover
    # A simple protobuf message with field 1 = varint 150
    # key = (1 << 3) | 0 = 0x08, value 150 = 0x9601
    hex_str = "089601"
    result = json.loads(_tool_protobuf_recover(hex_data=hex_str))
    assert result["ok"]
    assert result["fields_count"] >= 1
    assert any(f["field_number"] == 1 for f in result["fields"])


def test_protobuf_recover_string():
    from agent_harness.core.tools.rev_utils import _tool_protobuf_recover
    # field 2, wire_type 2 (length-delimited), length 5, "hello"
    # key = (2 << 3) | 2 = 0x12, length = 0x05, data = "hello"
    hex_str = "120568656c6c6f"
    result = json.loads(_tool_protobuf_recover(hex_data=hex_str))
    assert result["ok"]
    assert any(f["field_number"] == 2 and f["type"] == "string" and "hello" in str(f["sample"])
               for f in result["fields"])


def test_protobuf_recover_empty_hex():
    from agent_harness.core.tools.rev_utils import _tool_protobuf_recover
    result = json.loads(_tool_protobuf_recover(hex_data=""))
    assert not result.get("ok")


def test_protobuf_recover_invalid_hex():
    from agent_harness.core.tools.rev_utils import _tool_protobuf_recover
    result = json.loads(_tool_protobuf_recover(hex_data="nothex"))
    assert not result.get("ok")


def test_frida_stalker_no_frida():
    from agent_harness.core.tools.rev_utils import _tool_frida_stalker
    result = json.loads(_tool_frida_stalker("notepad.exe", "0x1234"))
    # Without frida installed, should still return ok with hint
    assert result["ok"]
    assert "stalker_js_ready" in result


def test_protobuf_generates_proto():
    from agent_harness.core.tools.rev_utils import _infer_protobuf_schema
    hex_str = "089601"  # field 1, varint 150
    result = _infer_protobuf_schema(hex_str)
    assert result["ok"]
    assert "proto" in result
    assert "syntax = \"proto3\"" in result["proto"]
    assert "message InferredMessage" in result["proto"]


def test_ghidra_scripts_exist():
    import os
    scripts_dir = Path(__file__).resolve().parent.parent / "reference" / "ghidra_scripts"
    assert (scripts_dir / "analyze_calls.py").exists()
    assert (scripts_dir / "export_calls.py").exists()


def test_rev_utils_module_imports_clean():
    from agent_harness.core.tools.rev_utils import (
        _tool_frida_stalker,
        _tool_protobuf_recover,
        _infer_protobuf_schema,
        GHIDRA_DOC,
        BINDIFF_DOC,
    )
    assert callable(_tool_frida_stalker)
    assert callable(_tool_protobuf_recover)
    assert callable(_infer_protobuf_schema)
    assert len(GHIDRA_DOC) > 100
    assert len(BINDIFF_DOC) > 100