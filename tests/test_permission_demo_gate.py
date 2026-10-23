"""SEC-20：demo / 共享实例下的写类工具闸门。

原状：`read-only` 和 `reversible` 都无条件 `return True`，只有 `irreversible` 看
`auto_confirm` —— 而 `auto_confirm` **默认 True**，等于默认全开。
真实后果：DISABLE_AUTH=1（公网 demo / 共享实例）时访客可调 `file_write` /
`rag_index` / `skill_generate`（写盘），以及 `code_execute`（任意代码执行）。

修法：demo 下写类工具一律拒绝（fail-closed），白名单
`HARNESS_DEMO_ALLOW_WRITE_TOOLS` 逐个点名才放开。本地单机（DISABLE_AUTH=0）行为不变。
"""

import pytest

from agent_harness.core.tools import permission as perm


@pytest.fixture()
def audit_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(perm, "AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setattr(perm, "PENDING_DIR", str(tmp_path / "pending"))
    # 安全模式是模块级全局：每个用例都从 default 起，避免用例之间串味
    from agent_harness.core.safety import mode as mode_mod
    monkeypatch.setattr(mode_mod, "_current_mode", mode_mod.MODE_DEFAULT)
    monkeypatch.setattr(mode_mod, "_expires_at", 0.0)
    return tmp_path / "audit"


@pytest.fixture()
def local_mode(monkeypatch, audit_dir):
    """本地单机：DISABLE_AUTH=0。"""
    monkeypatch.setattr("agent_harness.core.config.DISABLE_AUTH", False)
    monkeypatch.delenv("HARNESS_DEMO_ALLOW_WRITE_TOOLS", raising=False)


@pytest.fixture()
def demo_mode(monkeypatch, audit_dir):
    """公网 demo / 共享实例：DISABLE_AUTH=1。"""
    monkeypatch.setattr("agent_harness.core.config.DISABLE_AUTH", True)
    monkeypatch.delenv("HARNESS_DEMO_ALLOW_WRITE_TOOLS", raising=False)


# ─── 本地单机：行为必须零变化（这是回归红线）───

@pytest.mark.parametrize("tool", ["file_write", "rag_index", "skill_generate", "memory_import"])
def test_local_mode_reversible_allowed(local_mode, tool):
    assert perm.check_permission(tool) is True


def test_local_mode_irreversible_denied_in_default_mode(local_mode):
    """P0-4：default 模式下一律拒绝（不再看 auto_confirm），并落待批工单。

    旧语义是「默认放行、auto_confirm=False 才拒」，但 auto_confirm 默认 True，
    靠它兜底等于默认全开，而这一级是任意代码执行。改为 fail-closed。
    """
    import json
    from pathlib import Path

    assert perm.check_permission("code_execute", auto_confirm=True) is False
    assert perm.check_permission("code_execute", auto_confirm=False) is False

    tickets = list(Path(perm.PENDING_DIR).glob("req_*.json"))
    assert tickets, "拒绝必须落待批工单，否则事后没人知道要批什么"
    body = json.loads(tickets[0].read_text(encoding="utf-8"))
    assert body["tool"] == "code_execute"
    assert body["status"] == "pending"


@pytest.mark.parametrize("tool", ["search", "fetch", "rag_query", "file_read"])
def test_local_mode_read_only_allowed(local_mode, tool):
    assert perm.check_permission(tool) is True


# ─── demo：写类一律拒绝 ───

@pytest.mark.parametrize("tool", ["file_write", "rag_index", "skill_generate", "memory_import"])
def test_demo_mode_reversible_denied(demo_mode, tool):
    """reversible 全是写盘工具 —— demo 访客不该有。"""
    assert perm.check_permission(tool) is False


def test_demo_mode_irreversible_denied_even_with_auto_confirm(demo_mode):
    """关键：auto_confirm 默认 True，靠它兜底等于默认全开 —— demo 下必须硬拒。"""
    assert perm.check_permission("code_execute", auto_confirm=True) is False
    assert perm.check_permission("code_execute", auto_confirm=False) is False


@pytest.mark.parametrize("tool", ["search", "fetch", "rag_query"])
def test_demo_mode_read_only_still_allowed(demo_mode, tool):
    """demo 的调研主流程靠 read-only 工具，不能被误伤。"""
    assert perm.check_permission(tool) is True


# ─── 白名单：显式放开 ───

def test_demo_mode_whitelist_allows_named_tool(demo_mode, monkeypatch):
    monkeypatch.setenv("HARNESS_DEMO_ALLOW_WRITE_TOOLS", "rag_index, file_write")
    assert perm.check_permission("rag_index") is True
    assert perm.check_permission("file_write") is True
    # 没点名的仍然拒绝
    assert perm.check_permission("skill_generate") is False


def test_demo_mode_whitelist_is_exact_match(demo_mode, monkeypatch):
    """白名单只认完整工具名，前缀/子串不能蒙混过关。"""
    monkeypatch.setenv("HARNESS_DEMO_ALLOW_WRITE_TOOLS", "rag")
    assert perm.check_permission("rag_index") is False


# ─── 拒绝必须留痕 ───

def test_demo_denial_is_audited(demo_mode):
    perm.check_permission("file_write", source="api")
    entries = list((perm.AUDIT_DIR and __import__("pathlib").Path(perm.AUDIT_DIR)).glob("audit_*.json"))
    assert entries, "拒绝必须写审计日志（否则事后无法追溯谁试图写盘）"
    import json
    body = json.loads(entries[0].read_text(encoding="utf-8"))
    assert body["tool"] == "file_write"
    assert "denied in demo mode" in body["result"]
