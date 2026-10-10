"""种子 ⑪：危险操作待确认队列的加载失败必须 fail-loud（@bughunter/@architect 2026-09-10）。

背景：`risk._PendingConfirm._load` 把解析失败静默 `except: pass`。症状是危险操作确认请求
**无声消失**——用户看不到、无从批准、不可重放。方向是 fail-closed（消费端取不到就拒），
但**不可感知的失败**必须留痕，否则"队列丢了"和"从来没有过"在数据上无法区分。

修法（architect 拟定）：损坏条目移入 `pending/corrupt/` 留档 + ERROR 日志。
"""
import json
import logging

from agent_harness.core.safety.risk import _PendingConfirm


def test_corrupt_pending_entries_are_quarantined_and_logged(tmp_path, caplog):
    pending = tmp_path / "pending"
    pending.mkdir()
    (pending / "truncated.json").write_text('{"code": "aa', encoding="utf-8")          # 解析失败
    (pending / "nocode.json").write_text(json.dumps({"status": "pending"}), encoding="utf-8")  # 缺 code
    (pending / "done.json").write_text(
        json.dumps({"status": "confirmed", "code": "zzz"}), encoding="utf-8")           # 正常：非 pending
    (pending / "ok.json").write_text(
        json.dumps({"status": "pending", "code": "abc123"}), encoding="utf-8")          # 正常：待确认

    caplog.set_level(logging.ERROR)
    q = _PendingConfirm(tmp_path)

    assert set(q._items) == {"abc123"}, f"正常条目必须留下，实测 {sorted(q._items)}"
    corrupt = pending / "corrupt"
    assert (corrupt / "truncated.json").exists(), "解析失败条目应移入 pending/corrupt/ 留档"
    assert (corrupt / "nocode.json").exists(), "缺 code 键条目同样不可恢复，也应留档"
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 2, f"每条损坏都应有 ERROR 日志（fail-loud），实测 {len(errors)} 条"
    assert not (pending / "truncated.json").exists(), "留档后应从 pending/ 移走，避免每次启动重复报"
