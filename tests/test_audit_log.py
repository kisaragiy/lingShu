"""ENG-22：审计日志的时间戳唯一性与写失败可见性。

背景（两条独立的静默失败）：
  1. 文件名只用时间戳。Windows 时钟粒度不足以保证同一微秒不重号，并发/连发时
     后一条会覆盖前一条 → 审计静默丢记录、顺序错乱。
  2. `except OSError: pass` 把写失败吞掉，磁盘满/权限不足时「审计断档」和
     「没有不可逆操作」在日志里长得一模一样。

修法：ts = 微秒 + 进程内单调序号；写失败往 stderr 告警且**不抛异常**（审计失败
不该杀死业务）。
"""
import json
import os
import sys
import threading

import pytest

from agent_harness.core.tools import permission


@pytest.fixture()
def audit_dir(tmp_path, monkeypatch):
    d = tmp_path / "audit"
    monkeypatch.setattr(permission, "AUDIT_DIR", str(d))
    return d


def _entries(d):
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("audit_*.json"))]


# ─── 1. 唯一性 / 单调性 ────────────────────────────────────────────────────

def test_consecutive_calls_have_distinct_timestamps(audit_dir):
    for i in range(50):
        permission.log_audit("code_exec", "harness", {"i": i})

    files = sorted(p.name for p in audit_dir.glob("audit_*.json"))
    assert len(files) == 50, f"50 条审计必须落 50 个文件，实际 {len(files)}（有碰撞被覆盖）"

    entries = _entries(audit_dir)
    ts = [e["ts"] for e in entries]
    assert len(set(ts)) == 50, "时间戳必须互不相同"
    seqs = [e["seq"] for e in entries]
    assert seqs == sorted(seqs) and len(set(seqs)) == 50, f"序号必须单调递增: {seqs}"


def test_concurrent_calls_do_not_collide(audit_dir):
    errors = []

    def worker(n):
        try:
            permission.log_audit("code_exec", "api", {"n": n})
        except Exception as exc:  # noqa: BLE001 — 并发下也不许抛
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"审计不得抛异常打断业务: {errors}"
    files = list(audit_dir.glob("audit_*.json"))
    assert len(files) == 24, f"并发写必须一条不丢，实际 {len(files)}/24"


# ─── 2. 写失败必须可见，且不打断业务 ──────────────────────────────────────

def test_write_failure_warns_on_stderr_and_does_not_raise(audit_dir, monkeypatch, capsys):
    def boom(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("builtins.open", boom)

    permission.log_audit("code_exec", "api", {"cmd": "ls"})  # 不得抛异常

    err = capsys.readouterr().err
    assert "[AUDIT]" in err, f"写失败必须告警到 stderr，实际: {err!r}"
    assert "No space left" in err or "OSError" in err, "告警要说清原因，否则没法排查"


def test_ensure_dir_failure_also_warns_and_does_not_raise(audit_dir, monkeypatch, capsys):
    """建目录就失败（权限/只读盘）时，原来是在 try 之外直接抛异常 → 杀死工具调用。"""

    def boom(*a, **k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(permission, "_ensure_audit_dir", boom)

    permission.log_audit("code_exec", "api", {})  # 关键：不许抛

    assert "[AUDIT]" in capsys.readouterr().err


# ─── 3. 正常路径内容不变 ──────────────────────────────────────────────────

def test_entry_payload_shape(audit_dir):
    permission.log_audit("code_exec", "harness", {"cmd": "x" * 500}, result="ok", duration_ms=12.34)
    entries = _entries(audit_dir)
    assert len(entries) == 1
    e = entries[0]
    assert e["tool"] == "code_exec" and e["source"] == "harness"
    assert len(e["args"]["cmd"]) == 200, "参数截断保持 200"
    assert e["duration_ms"] == 12.3
    assert e["ts"].split("_")[-1].isdigit(), "ts 末尾是定宽序号"

# ─── 4. 同一缺陷族第 3 处：core/auth/api_security.py 的 log_audit ────────
# 修 permission.py 时必须 grep 同类 —— 这两个模块各有一份 log_audit，
# 症状完全相同（文件名只用时间戳 + 写失败静默吞），只修一处等于没修。

from agent_harness.core.auth import api_security  # noqa: E402


@pytest.fixture()
def sec_audit_dir(tmp_path, monkeypatch):
    d = tmp_path / "sec_audit"
    monkeypatch.setattr(api_security, "AUDIT_DIR", d)
    return d


def test_api_security_audit_no_collision(sec_audit_dir):
    for i in range(30):
        api_security.log_audit("login", "api", {"i": i})

    files = list(sec_audit_dir.glob("audit_*.json"))
    assert len(files) == 30, f"30 条审计必须 30 个文件，实际 {len(files)}（撞名被覆盖）"
    ts = [json.loads(f.read_text(encoding="utf-8"))["ts"] for f in sorted(files)]
    assert len(set(ts)) == 30


def test_api_security_audit_write_failure_warns(sec_audit_dir, monkeypatch, capsys):
    def boom(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("builtins.open", boom)

    api_security.log_audit("login", "api", {})  # 不得抛

    assert "[AUDIT]" in capsys.readouterr().err, "api_security 的写失败必须可见"
