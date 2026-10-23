"""P0-2：progress queue / trace collector 必须是 **run 作用域**，不是进程单例。

缺陷实测（gpt6 评审 §2.1 结构性缺陷 1）：``_progress_queue`` 是模块级变量，
并发两个会话时：
  set A → set B（A 的事件从此丢失）
  A 结束 clear() → None（把 B 的流也置空）
修复后要求：按 key 存取，clear 只清自己的 key；dispatch 的进度事件投递到本 run 的队列。
"""
import queue
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core.graph import graph_multi as gm  # noqa: E402


def _stub_run_worker(monkeypatch):
    def fake(worker_name, task, max_retries=3, task_id=""):
        return {
            "worker_name": worker_name, "success": True, "output": "ok",
            "data": None, "trace_steps": [], "errors": [], "elapsed_s": 0.0,
        }
    monkeypatch.setattr(gm, "run_worker", fake)


def test_two_runs_get_their_own_queue():
    q1: queue.Queue = queue.Queue()
    q2: queue.Queue = queue.Queue()
    gm.set_progress_queue(q1, key="run-a")
    gm.set_progress_queue(q2, key="run-b")
    try:
        assert gm.get_progress_queue("run-a") is q1
        assert gm.get_progress_queue("run-b") is q2
    finally:
        gm.clear_progress_queue("run-a")
        gm.clear_progress_queue("run-b")


def test_clear_one_run_does_not_wipe_another():
    q1: queue.Queue = queue.Queue()
    q2: queue.Queue = queue.Queue()
    gm.set_progress_queue(q1, key="run-a")
    gm.set_progress_queue(q2, key="run-b")
    try:
        gm.clear_progress_queue("run-a")
        assert gm.get_progress_queue("run-a") is None
        assert gm.get_progress_queue("run-b") is q2, "A 清理把 B 的队列清掉了（原缺陷）"
    finally:
        gm.clear_progress_queue("run-b")


def test_dispatch_events_land_in_own_queue_only(monkeypatch):
    _stub_run_worker(monkeypatch)
    q1: queue.Queue = queue.Queue()
    q2: queue.Queue = queue.Queue()
    gm.set_progress_queue(q1, key="task-a")
    gm.set_progress_queue(q2, key="task-b")
    try:
        gm.supervisor_dispatch({
            "workers_assigned": ["search"],
            "worker_tasks": {"search": "查个东西"},
            "task_id": "task-a",
            "request": "测试请求",
            "trace_steps": [],
        })
        assert not q1.empty(), "本 run 的队列收不到进度事件"
        assert q2.empty(), "别的 run 的队列收到了本 run 的进度事件（串台）"
    finally:
        gm.clear_progress_queue("task-a")
        gm.clear_progress_queue("task-b")


def test_trace_collector_is_run_scoped():
    from agent_harness.core.pipeline.tracing import TraceCollector

    c1 = TraceCollector("trace-a")
    c2 = TraceCollector("trace-b")
    gm.set_trace_collector(c1, key="run-a")
    gm.set_trace_collector(c2, key="run-b")
    try:
        assert gm.get_trace_collector("run-a") is c1
        assert gm.get_trace_collector("run-b") is c2
        gm.clear_trace_collector("run-a")
        assert gm.get_trace_collector("run-a") is None
        assert gm.get_trace_collector("run-b") is c2
    finally:
        gm.clear_trace_collector("run-a")
        gm.clear_trace_collector("run-b")
