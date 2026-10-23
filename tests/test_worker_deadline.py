"""P0-8：worker 并发等待必须是**真 deadline**（原 ``future.result(timeout=120)`` 是死参数）。

缺陷实测（gpt6 评审 P0-8）::
    as_completed(futures) 之后 future 已完成 →
    future.result(timeout=0.001) 耗时 0.081s 且不抛 TimeoutError

修复后要求：dispatch 用 ``concurrent.futures.wait`` 分片等待 + 总 deadline，
超期的 worker 记为失败并 cancel，dispatch 在 deadline 附近返回。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core.graph import graph_multi as gm  # noqa: E402


def _slow_worker(seconds):
    def fake(worker_name, task, max_retries=3, task_id=""):
        time.sleep(seconds)
        return {
            "worker_name": worker_name, "success": True, "output": "done-late",
            "data": None, "trace_steps": [], "errors": [], "elapsed_s": seconds,
        }
    return fake


def test_dispatch_returns_at_deadline(monkeypatch):
    monkeypatch.setattr(gm, "run_worker", _slow_worker(5))
    monkeypatch.setattr(gm, "WORKER_DEADLINE_S", 0.3)

    t0 = time.time()
    out = gm.supervisor_dispatch({
        "workers_assigned": ["search"],
        "worker_tasks": {"search": "慢任务"},
        "task_id": "deadline-task",
        "request": "测试请求",
        "trace_steps": [],
    })
    elapsed = time.time() - t0

    assert elapsed < 2.0, f"dispatch 被卡住的 worker 拖了 {elapsed:.1f}s（deadline 未生效）"
    assert out["worker_errors"].get("search"), "超期 worker 没有错误记录"
    assert out["worker_results"]["search"]["success"] is False


def test_fast_workers_still_complete(monkeypatch):
    monkeypatch.setattr(gm, "run_worker", _slow_worker(0.01))
    monkeypatch.setattr(gm, "WORKER_DEADLINE_S", 5.0)

    out = gm.supervisor_dispatch({
        "workers_assigned": ["search"],
        "worker_tasks": {"search": "快任务"},
        "task_id": "fast-task",
        "request": "测试请求",
        "trace_steps": [],
    })
    assert out["worker_results"]["search"]["success"] is True
    assert not out["worker_errors"]
