"""P0-6：SEC-2 的 RAG 作用域必须在 **worker 线程边界**仍然生效。

缺陷实测（gpt6 评审 P0-6）：``_run_with_trace`` 本身就跑在 ThreadPoolExecutor 的
worker 线程里，而 ``contextvars.copy_context()`` 写在它内部 → 拷到的是空上下文，
worker 里 ``current_scope() == ""``（= 全见），rag_query 可被诱导读别人的 collection。

修复后要求：copy 在**提交线程**执行（``pool.submit(ctx.run, fn, ...)``）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core.graph import graph_multi as gm  # noqa: E402
from agent_harness.core.tools import rag_store  # noqa: E402


def test_worker_thread_inherits_rag_scope(monkeypatch):
    def fake(worker_name, task, max_retries=3, task_id=""):
        return {
            "worker_name": worker_name, "success": True,
            "output": rag_store.current_scope(),  # 关键：在 worker 线程里读
            "data": None, "trace_steps": [], "errors": [], "elapsed_s": 0.0,
        }
    monkeypatch.setattr(gm, "run_worker", fake)

    tok = rag_store.set_scope("user_a_")
    try:
        out = gm.supervisor_dispatch({
            "workers_assigned": ["search"],
            "worker_tasks": {"search": "查资料"},
            "task_id": "scope-task",
            "request": "测试请求",
            "trace_steps": [],
        })
    finally:
        rag_store.reset_scope(tok)

    assert out["worker_results"]["search"]["output"] == "user_a_", (
        "worker 线程里 RAG scope 丢了 → SEC-2 知识库隔离在多 Agent 路径上失效"
    )


def test_serial_worker_thread_inherits_rag_scope(monkeypatch):
    """串行分支（副作用 worker）同样必须继承作用域。"""
    def fake(worker_name, task, max_retries=3, task_id=""):
        return {
            "worker_name": worker_name, "success": True,
            "output": rag_store.current_scope(),
            "data": None, "trace_steps": [], "errors": [], "elapsed_s": 0.0,
        }
    monkeypatch.setattr(gm, "run_worker", fake)

    tok = rag_store.set_scope("user_b_")
    try:
        out = gm.supervisor_dispatch({
            # 两个副作用 worker → 第一个并行、其余串行
            "workers_assigned": ["execute", "analyze"],
            "worker_tasks": {"execute": "跑", "analyze": "分析"},
            "task_id": "scope-task-2",
            "request": "测试请求",
            "trace_steps": [],
        })
    finally:
        rag_store.reset_scope(tok)

    for wname, res in out["worker_results"].items():
        assert res["output"] == "user_b_", f"{wname} 丢了 RAG scope"
