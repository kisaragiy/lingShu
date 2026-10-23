"""P1-10：知识库检索失败必须可见，不能被 ``except Exception: pass`` 吞掉。

原缺陷（gpt6 评审 P1-10）：`apps/research/api.py` 两处检索循环与两处 KB 注入
都是裸 `except Exception: pass` —— 嵌入服务不可用时，用户拿到一份「没用到知识库」
的回答且没有任何提示，模型还可能声称「已查阅知识库」。
"""
import json
import logging
import queue
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.apps.research import api  # noqa: E402
from agent_harness.core.graph import graph_multi as gm  # noqa: E402
from agent_harness.core.tools import rag_store  # noqa: E402


def _stub_rag_failure(monkeypatch):
    monkeypatch.setattr(rag_store, "list_collections", lambda: ["kb_a", "kb_b"])

    def boom(*_a, **_k):
        raise ConnectionError("embedding service down")

    monkeypatch.setattr(rag_store, "query", boom)


def _drain(q):
    events = []
    while not q.empty():
        events.append(q.get())
    return events


def test_multi_path_reports_kb_failure(monkeypatch, caplog):
    _stub_rag_failure(monkeypatch)
    monkeypatch.setattr(gm, "run_multi_agent", lambda *a, **k: {"final_output": "ok"})
    monkeypatch.setattr(gm, "default_checkpointer", lambda: None)

    q: queue.Queue = queue.Queue()
    gm.set_progress_queue(q, key="sess-kb")
    try:
        with caplog.at_level(logging.WARNING):
            api._execute_multi("测试问题", "", session_id="sess-kb")
        events = _drain(q)
        text = json.dumps(events, ensure_ascii=False)
        assert "知识库不可用" in text, f"用户侧没有任何知识库失败提示: {text}"
        assert any("知识库检索失败" in r.message for r in caplog.records), "运维侧没有 warning 日志"
    finally:
        gm.clear_progress_queue("sess-kb")


def test_single_path_tells_model_not_to_claim_kb(monkeypatch, caplog):
    _stub_rag_failure(monkeypatch)
    captured = {}

    def fake_run(prompt, **_k):
        captured["prompt"] = prompt
        return "答案"

    import agent_harness.core.graph as graph_pkg
    monkeypatch.setattr(graph_pkg, "run", fake_run)

    with caplog.at_level(logging.WARNING):
        out = api._execute_single("测试问题", "")
    assert out == "答案"
    assert "知识库检索失败" in captured.get("prompt", ""), (
        f"单 Agent 路径没把失败告诉模型: {captured.get('prompt')!r}"
    )
    assert any("知识库检索失败" in r.message for r in caplog.records)
