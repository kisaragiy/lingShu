"""Safety 护栏测试 — 权限双层架构（WorkBuddy P0 移植）"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core import safety
from agent_harness.core.safety import (
    MODE_DEFAULT,
    MODE_FULL,
    backup_before_write,
    check_operation,
    confirm_operation,
    get_mode,
    is_full_access,
    pending_operations,
    safe_delete,
    set_mode,
)


@pytest.fixture(autouse=True)
def _reset_mode(tmp_path, monkeypatch):
    """每个测试前重置模式为 default，隔离状态目录。"""
    monkeypatch.setattr(safety.mode, "STATE_DIR", tmp_path / "safety")
    monkeypatch.setattr(safety.mode, "STATE_FILE", tmp_path / "safety" / "safety_mode.json")
    monkeypatch.setattr(safety.mode, "AUDIT_DIR", tmp_path / "safety" / "audit")
    monkeypatch.setattr(safety.backup, "TRASH_DIR", tmp_path / "safety" / "trash")
    # 重置内存状态
    safety.mode._current_mode = MODE_DEFAULT
    safety.risk._pending = None
    yield
    safety.mode._current_mode = MODE_DEFAULT
    safety.risk._pending = None


# ─── mode.py ───

class TestMode:
    def test_default_mode_initial(self):
        assert get_mode() == MODE_DEFAULT
        assert not is_full_access()

    def test_switch_to_full_and_back(self):
        r = set_mode("full", source="test")
        assert r["ok"] and r["changed"] and r["mode"] == MODE_FULL
        assert is_full_access()
        r2 = set_mode("default", source="test")
        assert r2["ok"] and r2["changed"] and r2["mode"] == MODE_DEFAULT

    def test_invalid_mode_rejected(self):
        r = set_mode("superadmin", source="test")
        assert not r["ok"]
        assert get_mode() == MODE_DEFAULT

    def test_persist_roundtrip(self, tmp_path):
        set_mode("full", source="test")
        # 模拟重启：重新加载
        safety.mode._current_mode = MODE_DEFAULT
        safety.mode._load_persisted()
        assert get_mode() == MODE_FULL

    def test_mode_switch_audited(self, tmp_path):
        set_mode("full", source="test", operator="alice")
        audit_files = list((tmp_path / "safety" / "audit").glob("safety_*.json"))
        assert audit_files
        entry = json.loads(audit_files[0].read_text(encoding="utf-8"))
        assert entry["action"] == "mode_switch"
        assert entry["from_mode"] == MODE_DEFAULT
        assert entry["to_mode"] == MODE_FULL
        assert entry["operator"] == "alice"


# ─── risk.py ───

class TestRiskMatrix:
    def test_sensitive_write_blocked_in_default(self):
        r = check_operation("file_write", {"path": "C:\\Windows\\system32\\x.dll"},
                            require_confirm=True)
        assert not r["ok"]
        assert "sensitive_write" == r["risk"]
        assert r["confirm_code"]

    def test_secret_file_always_blocked(self):
        r = check_operation("file_write", {"path": "/home/user/.env"}, require_confirm=True)
        assert not r["ok"]
        assert "敏感文件" in r["reason"]

    def test_bulk_delete_blocked(self):
        r = check_operation("delete", {"path": "C:/data/*.log"}, require_confirm=True)
        assert not r["ok"]
        assert "bulk_delete" == r["risk"]

    def test_path_escape_blocked(self):
        r = check_operation("file_write", {"path": "../../etc/passwd"}, require_confirm=True)
        assert not r["ok"]
        assert "path_escape" == r["risk"]

    def test_script_exec_blocked(self):
        r = check_operation("code_execute", {"code": "print('hi')"}, require_confirm=True)
        assert not r["ok"]
        assert "script_exec" == r["risk"]

    def test_external_send_blocked(self):
        r = check_operation("qq_send", {"to": "张三", "message": "hi"}, require_confirm=True)
        assert not r["ok"]
        assert "external_send" == r["risk"]

    def test_network_non_allowlist_blocked(self):
        r = check_operation("fetch", {"url": "https://example.com/x"}, require_confirm=True)
        assert not r["ok"]
        assert "network_fetch" == r["risk"]

    def test_network_localhost_allowed(self):
        r = check_operation("fetch", {"url": "http://127.0.0.1:8080/health"}, require_confirm=True)
        assert r["ok"]

    def test_safe_tool_passes(self):
        r = check_operation("think", {"prompt": "hi"}, require_confirm=True)
        assert r["ok"]
        r2 = check_operation("file_read", {"path": "a.txt"}, require_confirm=True)
        assert r2["ok"]

    def test_full_mode_allows_but_audits(self, tmp_path):
        r = check_operation("qq_send", {"to": "张三", "message": "hi"},
                            require_confirm=False, operator="bob")
        assert r["ok"]
        audit_files = list((tmp_path / "safety" / "audit").glob("safety_*.json"))
        assert audit_files
        entry = json.loads(audit_files[0].read_text(encoding="utf-8"))
        assert entry["action"] == "risk_allowed"
        assert entry["operator"] == "bob"

    def test_confirm_flow(self):
        r = check_operation("file_write", {"path": "C:\\Windows\\x.txt"}, require_confirm=True)
        code = r["confirm_code"]
        assert pending_operations()
        cr = confirm_operation(code, operator="alice")
        assert cr["ok"] and cr["tool"] == "file_write"
        # 一次性：再确认无效
        cr2 = confirm_operation(code, operator="alice")
        assert not cr2["ok"]

    def test_confirm_invalid_code(self):
        r = confirm_operation("nope1234")
        assert not r["ok"]


# ─── backup.py ───

class TestBackup:
    def test_backup_before_overwrite(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("old", encoding="utf-8")
        b = backup_before_write(f, mode="w")
        assert b is not None and Path(b).exists()
        assert Path(b).read_text(encoding="utf-8") == "old"
        # 状态目录在 tmp 下
        assert "backup" in str(b)

    def test_no_backup_on_append(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("old", encoding="utf-8")
        b = backup_before_write(f, mode="a")
        assert b is None

    def test_no_backup_when_missing(self, tmp_path):
        b = backup_before_write(tmp_path / "nope.txt", mode="w")
        assert b is None

    def test_rotation_keeps_last_n(self, tmp_path, monkeypatch):
        monkeypatch.setattr(safety.backup, "_BACKUP_KEEP", 3)
        f = tmp_path / "doc.txt"
        for i in range(5):
            f.write_text(f"v{i}", encoding="utf-8")
            backup_before_write(f, mode="w")
        backup_dir = safety.mode.STATE_DIR / "backup" / os.path.basename(str(tmp_path))
        # 日期目录在 tmp 下，找所有备份
        all_baks = list(safety.mode.STATE_DIR.glob("backup/*/*.bak*"))
        assert len(all_baks) <= 3

    def test_safe_delete_trashes(self, tmp_path):
        f = tmp_path / "victim.txt"
        f.write_text("secret", encoding="utf-8")
        r = safe_delete(f)
        assert r["ok"]
        assert not f.exists()
        assert Path(r["trashed"]).exists()
        assert Path(r["trashed"]).read_text(encoding="utf-8") == "secret"

    def test_safe_delete_missing(self, tmp_path):
        r = safe_delete(tmp_path / "nope.txt")
        assert not r["ok"]

    def test_safe_delete_directory(self, tmp_path):
        d = tmp_path / "folder"
        d.mkdir()
        (d / "a.txt").write_text("x", encoding="utf-8")
        r = safe_delete(d)
        assert r["ok"]
        assert not d.exists()
        assert Path(r["trashed"]).is_dir()


# ─── call_tool 集成 ───

class TestCallToolIntegration:
    def test_risky_tool_intercepted_via_registry(self):
        from agent_harness.core.tools.registry import call_tool
        r = call_tool("qq_send", to="张三", message="hi", _source="api")
        assert not r["success"]
        assert "需要确认" in r["error"]
        assert "confirm_code" in r.get("data", {})

    def test_safe_tool_passes_via_registry(self):
        from agent_harness.core.tools.registry import call_tool
        r = call_tool("datetime", _source="api")
        assert r["success"]

    def test_full_mode_bypasses_confirm(self):
        set_mode("full", source="test")
        from agent_harness.core.tools.registry import call_tool
        # code_execute 在 full 模式下仍会被原 permission 的 irreversible 拒绝？
        # irreversible + auto_confirm=True 默认放行，验证走的是 risk 层
        r = call_tool("code_execute", code="print(1+1)", _source="harness")
        # 工具本身能跑（沙箱），只验证不被 risk 拦截
        assert "需要确认" not in (r.get("error") or "")


# ─── fail-closed: 护栏模块不可用不得静默裸奔（Day 5 硬化） ───

class TestFailClosed:
    """护栏加载失败时必须 fail-closed，绝不静默放行危险操作。"""

    def test_safety_unavailable_refuses_irreversible(self, monkeypatch):
        import sys
        monkeypatch.setitem(sys.modules, "agent_harness.core.safety", None)
        from agent_harness.core.tools.registry import call_tool
        r = call_tool("code_execute", code="print(1)", _source="harness")
        assert not r["success"]
        assert "安全护栏不可用" in r["error"]

    def test_safety_unavailable_reversible_logs_but_allows(self, monkeypatch, caplog):
        import sys
        monkeypatch.setitem(sys.modules, "agent_harness.core.safety", None)
        from agent_harness.core.tools.registry import call_tool
        r = call_tool("datetime", _source="api")
        assert r["success"]  # 可逆工具不被卡死
        assert any("护栏失效" in m for m in caplog.messages)

    def test_permission_unavailable_fails_closed(self, monkeypatch):
        import sys
        monkeypatch.setitem(sys.modules, "agent_harness.core.tools.permission", None)
        from agent_harness.core.tools.registry import call_tool
        with pytest.raises(RuntimeError, match="fail-closed"):
            call_tool("datetime", _source="api")
