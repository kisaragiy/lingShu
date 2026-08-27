"""Agent 成本纪律测试（dsh P0：空轮短路 / finish_reason sticky / phase-abort）

覆盖：
- C1.1 空决策短路：空/纯空白请求 → 0 次 LLM 调用，直接 completed
  - supervisor_analyze 空请求不调 _call_llm
  - supervisor_collect 空 workers 不调 _call_llm（verify 环节）
  - supervisor_finalize 空 worker_results 不调 _call_llm
- C1.2 max-tokens sticky：finish_reason=length 截断 → 保留已生成内容，不误判"空"而降级重试
- C1.3 phase-abort：取消信号 → worker 不再执行工具/LLM，尽早终止
"""
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import pytest

from agent_harness.core import agents
from agent_harness.core.pipeline import cancel
from agent_harness.core.agents import supervisor, workers
from agent_harness.core.graph import graph_multi


# ─── C1.1 空决策短路 ───

def test_empty_request_analyze_no_llm(monkeypatch):
    """纯空白请求 → supervisor_analyze 不调用模型，直接 completed。"""
    calls = {"n": 0}
    def fake_llm(*a, **k):
        calls["n"] += 1
        return '{"task_type":"analyze","workers":[{"name":"analyze","task":"x"}]}'
    monkeypatch.setattr(supervisor, "_call_llm", fake_llm)
    # 空请求 + 纯空白 + None 边界
    for req in ["", "   ", "\n\t", None]:
        state = {"request": req, "round": 0}
        out = supervisor.supervisor_analyze(state)
        assert calls["n"] == 0, f"空请求不应调 LLM (req={req!r})"
        assert out["all_done"] is True
        assert out.get("workers_assigned") == []


def test_empty_workers_collect_no_llm(monkeypatch):
    """空 workers_assigned → supervisor_collect 不调 verify LLM。"""
    calls = {"n": 0}
    def fake_llm(*a, **k):
        calls["n"] += 1
        return '{"done": true}'
    monkeypatch.setattr(supervisor, "_call_llm", fake_llm)
    state = {"request": "", "workers_assigned": [], "worker_results": {},
             "worker_errors": {}, "round": 0}
    out = supervisor.supervisor_collect(state)
    assert calls["n"] == 0, "空 workers 不应触发 verify LLM 调用"
    assert out["all_done"] is True


def test_empty_results_finalize_no_llm(monkeypatch):
    """空 worker_results → supervisor_finalize 不调 LLM，直接输出提示。"""
    calls = {"n": 0}
    def fake_llm(*a, **k):
        calls["n"] += 1
        return "xxx"
    monkeypatch.setattr(supervisor, "_call_llm", fake_llm)
    monkeypatch.setattr(supervisor, "_call_llm_full", fake_llm)
    state = {"request": "", "worker_results": {}, "worker_errors": {},
             "round": 1, "session_id": "test-sess", "trace_steps": []}
    out = graph_multi.supervisor_finalize(state)
    assert calls["n"] == 0, "空结果不应调 LLM 生成报告"
    assert out["final_output"], "应返回一个可读提示"


# ─── C1.2 max-tokens sticky ───

def test_truncated_answer_kept_not_downgraded(monkeypatch):
    """finish_reason=length 但有实质内容 → 保留回答，不进入降级重试。"""
    retried = {"n": 0}
    def fake_full(*a, **k):
        # 主调用：截断但内容足够
        return ("### 📋 执行摘要\n这是一个被 max_tokens 截断但仍有实质内容的回答。" * 2, "length")
    def fake_llm(*a, **k):
        retried["n"] += 1
        return "降级重试产物不应被用上"
    monkeypatch.setattr(supervisor, "_call_llm_full", fake_full)
    monkeypatch.setattr(supervisor, "_call_llm", fake_llm)
    state = {"request": "test", "worker_results": {
        "search": {"success": True, "output": "搜索结果", "elapsed_s": 1.0}},
        "worker_errors": {}, "round": 1, "session_id": "test-sess", "trace_steps": []}
    out = graph_multi.supervisor_finalize(state)
    assert retried["n"] == 0, "截断但实质内容不应触发降级重试"
    assert "执行摘要" in out["final_output"], "应保留截断回答内容"


# ─── C1.3 phase-abort ───

def _set_cancelled():
    ev = threading.Event()
    ev.set()
    cancel.set_cancel_event(ev)


def test_worker_executor_aborts_on_cancel(monkeypatch):
    """取消信号置位 → _worker_executor 不执行任何工具，尽早终止。"""
    _set_cancelled()
    try:
        tool_calls = {"n": 0}
        def fake_call_tool(*a, **k):
            tool_calls["n"] += 1
            return {"success": True, "data": "x"}
        monkeypatch.setattr(workers, "call_tool", fake_call_tool)
        state = {"worker_name": "search", "task": "task",
                 "plan": [{"name": "s", "tool": "search", "args": {"query": "q"}}],
                 "current_step": 0, "results": [], "errors": [], "trace_steps": []}
        out = workers._worker_executor(state)
        assert tool_calls["n"] == 0, "取消后不应执行任何工具"
        assert out.get("aborted") is True or out.get("errors"), "应返回终止标记"
    finally:
        cancel.clear_cancel_event()


def test_run_worker_aborts_on_cancel(monkeypatch):
    """取消信号置位 → run_worker 不构建 worker / 不调 LLM，返回取消结果。"""
    _set_cancelled()
    try:
        worker_calls = {"n": 0}
        def fake_build(*a, **k):
            worker_calls["n"] += 1
            raise AssertionError("取消后不应构建 worker")
        monkeypatch.setattr(workers, "build_worker", fake_build)
        res = workers.run_worker("search", "task")
        assert worker_calls["n"] == 0, "取消后不应构建/运行 worker"
        assert res["success"] is False
        assert "取消" in res["output"] or res["success"] is False
    finally:
        cancel.clear_cancel_event()
