"""Session 事件源测试（dsh P1：append-only 事件日志 + 投影 + 迁移 + meta 分离）

覆盖（对应实现映射 3.3 C2.x）：
- C2.1 旧 JSON 迁移：存量 {id}.json → load_session 返回相同 messages
- C2.2 可复现：同一 log 多次投影结果一致
- C2.3 增量正确：追加 N 个新消息 → 只新增 N，旧投影不变
- C2.5 检查点：从任意 seq 投影
- C2.6 meta 分离：高频改 title/pin 不重写消息日志，meta 原子
- 公开契约：save/load/list/search/delete/session_count 签名与行为保持
- 幂等/替换语义：save 全量列表，尾部增量 append；结构变更走 replace 事件
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# 导入模块后强制指向隔离目录
from agent_harness.core.pipeline import session_store as ss


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """每个测试用独立 SESSION_DIR + 重置模块级状态。"""
    d = tmp_path / "sessions"
    monkeypatch.setattr(ss, "SESSION_DIR", str(d))
    os.makedirs(d, exist_ok=True)
    yield d


def _msg(role, content, **kw):
    m = {"role": role, "content": content, "ts": kw.pop("ts", 1000)}
    m.update(kw)
    return m


def _read_log_text(sid):
    p = Path(ss.SESSION_DIR) / f"{sid}.log"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _log_events(sid):
    """读取某 session 的日志事件列表。"""
    text = _read_log_text(sid)
    return [json.loads(l) for l in text.splitlines() if l.strip()]


# ─── 公开契约 roundtrip ───

def test_save_load_roundtrip():
    msgs = [_msg("user", "你好"), _msg("assistant", "你好！")]
    ss.save_session("s1", msgs, owner_id="u1")
    got = ss.load_session("s1")
    assert got == msgs
    # list_sessions 摘要字段完整
    summary = ss.list_sessions(owner_id="u1")
    assert len(summary) == 1
    s = summary[0]
    assert s["id"] == "s1"
    assert s["message_count"] == 2
    assert s["exchanges"] == 1
    assert s["title"] == ""
    assert s["owner_id"] == "u1"


# ─── C2.3 增量正确 ───

def test_append_incremental_only_new_tail():
    m1 = [_msg("user", "hi")]
    m2 = m1 + [_msg("assistant", "hello"), _msg("user", "hi again")]
    ss.save_session("inc", m1)
    ss.save_session("inc", m2)
    assert ss.load_session("inc") == m2  # 投影 = 完整列表
    evs = _log_events("inc")
    # 只 append 了新增的 2 条 message 事件，没有重复旧消息
    msg_events = [e for e in evs if e["type"] == "message"]
    assert len(msg_events) == 3, f"期望3条message事件, 实际 {len(msg_events)}"
    assert all(e["surface_op"] == "append" for e in msg_events)


def test_idempotent_save_no_duplicate():
    msgs = [_msg("user", "a"), _msg("assistant", "b")]
    ss.save_session("idem", msgs)
    ss.save_session("idem", msgs)  # 同一列表重复保存
    assert ss.load_session("idem") == msgs
    assert len(_log_events("idem")) == 2  # 不重复 append


def test_replace_when_structure_changes():
    ss.save_session("rep", [_msg("user", "a"), _msg("assistant", "b")])
    # 结构不同（改了中间）/非简单尾部延伸 → 走 replace 事件快照
    ss.save_session("rep", [_msg("user", "a2"), _msg("assistant", "b2")])
    assert ss.load_session("rep") == [_msg("user", "a2"), _msg("assistant", "b2")]
    evs = _log_events("rep")
    assert any(e["type"] == "replace" for e in evs)


# ─── C2.2 可复现 ───

def test_reproducible_projection():
    msgs = [_msg("user", "x"), _msg("assistant", "y"), _msg("user", "z")]
    ss.save_session("rp", msgs)
    got1 = ss.load_session("rp")
    got2 = ss.load_session("rp")
    assert got1 == got2 == msgs


# ─── C2.5 检查点 / 从 seq 投影 ───

def test_checkpoint_reconstruct_from_offset():
    msgs = [_msg("user", "m1"), _msg("assistant", "m2"), _msg("user", "m3")]
    ss.save_session("cp", msgs)
    seqs = [e["seq"] for e in _log_events("cp")]
    assert seqs == [0, 1, 2]  # 单调递增
    # 从第 2 条之后投影 → 最后 1 条
    tail = ss.project_from_seq("cp", seqs[1] + 1)
    assert tail == msgs[2:]
    # 从 0 投影 = 全量
    assert ss.project_from_seq("cp", 0) == msgs


# ─── C2.6 meta 分离 ───

def test_meta_update_does_not_rewrite_log():
    msgs = [_msg("user", "a"), _msg("assistant", "b")]
    ss.save_session("meta", msgs)
    log_before = _read_log_text("meta")
    ss.update_session_meta("meta", title="我的会话", pinned=True)
    # 日志内容不变（不重写消息）
    assert _read_log_text("meta") == log_before, "meta 更新不应改写消息日志"
    # 但 meta 已更新
    s = ss.list_sessions()[0]
    assert s["title"] == "我的会话"
    assert s["pinned"] is True
    assert ss.load_session("meta") == msgs  # 消息不受影响


def test_meta_separate_file_atomic():
    # meta 应存放在独立 meta 文件，而非塞进日志
    msgs = [_msg("user", "a")]
    ss.save_session("m2", msgs)
    ss.update_session_meta("m2", title="t")
    assert (Path(ss.SESSION_DIR) / "m2.meta.json").exists()
    assert not (Path(ss.SESSION_DIR) / "m2.json").exists()  # 旧的单 JSON 不再用


# ─── C2.1 旧 JSON 迁移 ───

def test_migrate_legacy_json():
    d = Path(ss.SESSION_DIR)
    legacy = {
        "session_id": "old1", "title": "老会话", "pinned": True,
        "updated_at": 2000, "created_at": 1500, "message_count": 2,
        "exchanges": 1, "last_preview": "hi", "owner_id": "u9",
        "messages": [_msg("user", "hi"), _msg("assistant", "yo")],
    }
    (d / "old1.json").write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    ss.init_store()  # 触发迁移
    got = ss.load_session("old1")
    assert got == legacy["messages"], "旧 JSON 迁移后消息应一致"
    s = ss.list_sessions(owner_id="u9")[0]
    assert s["title"] == "老会话"
    assert s["pinned"] is True
    # 旧 json 已备份/移除
    assert not (d / "old1.json").exists(), "旧 JSON 应被迁移走"


# ─── delete / count ───

def test_delete_and_count():
    ss.save_session("d1", [_msg("user", "a")])
    ss.save_session("d2", [_msg("user", "b")])
    assert ss.session_count() == 2
    assert ss.delete_session("d1") is True
    assert ss.load_session("d1") is None
    assert ss.session_count() == 1
