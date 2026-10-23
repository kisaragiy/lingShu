"""P0-4: irreversible 工具在 default 模式一律拒绝，只 full 放行（带 TTL）。

背景：`check_permission` 原来是 `return auto_confirm`，而 auto_confirm 默认 True
—— 等于默认全开。这一级里是 `code_execute`（任意代码执行），"安全三层"在默认
路径上不成立。现在改为 fail-closed，并落待批工单让人知道要批什么。

full 模式是高风险状态，带 TTL（默认 30 分钟）自动回落 default —— 全权模式不该
无限期停留。
"""

import json
import time
from pathlib import Path

import pytest

from agent_harness.core.safety import mode as mode_mod
from agent_harness.core.tools import permission as perm


@pytest.fixture()
def sandbox(monkeypatch, tmp_path):
    """隔离审计/工单/模式状态，并把安全模式固定在 default。"""
    monkeypatch.setattr(perm, "AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setattr(perm, "PENDING_DIR", str(tmp_path / "pending"))
    monkeypatch.setattr(mode_mod, "STATE_DIR", tmp_path)
    monkeypatch.setattr(mode_mod, "STATE_FILE", tmp_path / "safety_mode.json")
    monkeypatch.setattr(mode_mod, "AUDIT_DIR", tmp_path / "audit")
    monkeypatch.setattr(mode_mod, "_current_mode", mode_mod.MODE_DEFAULT)
    monkeypatch.setattr(mode_mod, "_expires_at", 0.0)
    monkeypatch.setattr("agent_harness.core.config.DISABLE_AUTH", False)
    monkeypatch.delenv("HARNESS_DEMO_ALLOW_WRITE_TOOLS", raising=False)
    return mode_mod


# ─── default：一律拒绝 ───

def test_default_denies_irreversible(sandbox):
    assert perm.check_permission("code_execute") is False
    assert perm.check_permission("code_execute", auto_confirm=True) is False


def test_default_denial_writes_pending_ticket(sandbox):
    perm.check_permission("code_execute", source="worker")

    tickets = list(Path(perm.PENDING_DIR).glob("req_*.json"))
    assert len(tickets) == 1, "每次拒绝都该留一张工单"
    body = json.loads(tickets[0].read_text(encoding="utf-8"))
    assert body["tool"] == "code_execute"
    assert body["source"] == "worker"
    assert body["status"] == "pending"
    assert "approve_hint" in body


def test_default_still_allows_reversible_and_readonly(sandbox):
    """不能把口子收得误伤主流程：调研靠 read-only，写报告靠 reversible。"""
    assert perm.check_permission("file_read") is True
    assert perm.check_permission("search") is True
    assert perm.check_permission("file_write") is True
    assert perm.check_permission("rag_index") is True


# ─── full：放行，但显式 auto_confirm=False 仍拒 ───

def test_full_allows_irreversible(sandbox):
    assert sandbox.set_mode("full")["ok"] is True
    assert sandbox.is_full_access() is True
    assert perm.check_permission("code_execute", auto_confirm=True) is True


def test_full_still_respects_explicit_auto_confirm_false(sandbox):
    sandbox.set_mode("full")
    assert perm.check_permission("code_execute", auto_confirm=False) is False


# ─── full 的 TTL ───

def test_full_sets_expiry(sandbox):
    res = sandbox.set_mode("full")
    assert res["expires_at"], "切 full 必须带到期时间，否则全权模式无限期"
    assert res["expires_at"] > time.time()


def test_full_expires_back_to_default(sandbox, monkeypatch):
    sandbox.set_mode("full")
    monkeypatch.setattr(sandbox, "_expires_at", time.time() - 1)  # 已过期

    assert sandbox.get_mode() == mode_mod.MODE_DEFAULT
    assert perm.check_permission("code_execute") is False

    # 回落要留审计，否则事后查不到「谁开过全权」
    audits = list(Path(sandbox.AUDIT_DIR).glob("safety_*.json"))
    assert any("mode_ttl_expired" in p.read_text(encoding="utf-8") for p in audits)


def test_switching_back_to_default_clears_expiry(sandbox):
    sandbox.set_mode("full")
    sandbox.set_mode("default")
    assert sandbox._expires_at == 0.0
    assert perm.check_permission("code_execute") is False


# ─── 重启不该续命 full ───

def test_persisted_full_past_expiry_loads_as_default(sandbox, monkeypatch):
    (sandbox.STATE_FILE).write_text(
        json.dumps({"mode": "full", "expires_at": time.time() - 10}), encoding="utf-8")
    monkeypatch.setattr(sandbox, "_current_mode", mode_mod.MODE_FULL)

    assert sandbox._load_persisted() == mode_mod.MODE_DEFAULT
