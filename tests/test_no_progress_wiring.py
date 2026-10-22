"""P1-3 无进展熔断接线 — with_no_progress_guard 真正接到多轮 worker 主回路。

痛点：`core/retry.py` 里早就有 `NoProgressError` + `with_no_progress_guard`，
但只活在装饰器里，从没接到「supervisor dispatch → collect → replan → dispatch」
这条多轮 worker 主回路。于是主管反复派同一个 worker + 同一个子任务时，
没有任何熔断，白白烧 token 也拿不到新进展。

本文件验证：
- check_no_progress：连续 K 次同 (worker, task) → NoProgressError；换参归零、
  不同 tool 各自计数（多 worker 交错场景）
- key_fn 可注入（按 tool 粒度判重）
- supervisor_dispatch：重复派同一 worker + 同参 → 熔断那轮**不再真跑**该 worker，
  且 all_done=True（提前收尾）
- supervisor_route：熔断标记 → finalize
- 不误伤：任务变化 / 关闭检测时，正常多轮全部执行
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from agent_harness.core.graph import graph_multi as gm
from agent_harness.core.pipeline.state import WorkerResult
from agent_harness.core.retry import NoProgressError


@pytest.fixture(autouse=True)
def _reset_guard():
    """每个用例前后都关闭全局检测，避免跨用例串状态。"""
    gm.set_no_progress_detection(False)
    yield
    gm.set_no_progress_detection(False)


def _ok(wname: str, task: str) -> WorkerResult:
    return WorkerResult(worker_name=wname, success=True, output="ok-" + task,
                        data=None, trace_steps=[], errors=[], elapsed_s=0)


# ═══════════ check_no_progress 单元 ═══════════

def test_disabled_by_default_never_trips():
    gm.set_no_progress_detection(False)
    for _ in range(10):
        gm.check_no_progress("search", "same")  # 不抛


def test_trips_after_k_repeats():
    gm.set_no_progress_detection(True, max_repeats=3)
    gm.check_no_progress("search", "same")
    gm.check_no_progress("search", "same")
    with pytest.raises(NoProgressError):
        gm.check_no_progress("search", "same")   # 第 3 次连续同参 → 熔断


def test_changed_task_resets_count():
    gm.set_no_progress_detection(True, max_repeats=3)
    gm.check_no_progress("search", "a")
    gm.check_no_progress("search", "a")
    gm.check_no_progress("search", "b")   # 换参 → 计数归零
    gm.check_no_progress("search", "b")
    gm.check_no_progress("search", "c")   # 不熔断


def test_workers_counted_independently():
    """多 worker 交错时，各 tool 独立计数、互不归零（否则单槽计数器永不触发）。"""
    gm.set_no_progress_detection(True, max_repeats=3)
    gm.check_no_progress("search", "x")     # search 1
    gm.check_no_progress("analyze", "y")    # analyze 1
    gm.check_no_progress("search", "x")     # search 2
    gm.check_no_progress("analyze", "y")    # analyze 2
    with pytest.raises(NoProgressError):
        gm.check_no_progress("search", "x")  # search 3 → 熔断
    with pytest.raises(NoProgressError):
        gm.check_no_progress("analyze", "y")  # analyze 3 → 熔断


def test_key_fn_injectable_by_tool():
    """注入 key_fn：只按 worker 名判重（同 tool 不同参也视为无进展）。"""
    gm.set_no_progress_detection(True, max_repeats=2, key_fn=lambda wname, task: wname)
    gm.check_no_progress("search", "a")
    with pytest.raises(NoProgressError):
        gm.check_no_progress("search", "b")


# ═══════════ supervisor_dispatch 集成 ═══════════

def _disp_state(round_: int = 1, tasks=None) -> dict:
    return {
        "request": "研究 X",
        "workers_assigned": ["search"],
        "worker_tasks": tasks or {"search": "同一子任务"},
        "worker_results": {}, "worker_errors": {}, "round": round_,
        "all_done": False, "trace_steps": [],
    }


def test_dispatch_trips_on_repeated_rounds(monkeypatch):
    calls = []

    def fake_run_worker(wname, task, task_id=""):
        calls.append((wname, task))
        return _ok(wname, task)

    monkeypatch.setattr(gm, "run_worker", fake_run_worker)
    gm.set_no_progress_detection(True, max_repeats=3)

    for _ in range(2):                     # 第 1、2 轮：真正执行
        out = gm.supervisor_dispatch(_disp_state())
        assert out["worker_results"]["search"]["success"] is True

    out = gm.supervisor_dispatch(_disp_state())   # 第 3 轮：连续同参 → 熔断
    assert calls == [("search", "同一子任务"), ("search", "同一子任务")], \
        "熔断轮不应真正执行 worker"
    assert out["no_progress_tripped"] is True
    assert out["all_done"] is True
    assert out["worker_results"]["search"]["success"] is False
    assert any(s.get("step") == "supervisor_no_progress_fuse" for s in out["trace_steps"])


def test_dispatch_no_misfire_when_tasks_change(monkeypatch):
    calls = []

    def fake_run_worker(wname, task, task_id=""):
        calls.append((wname, task))
        return _ok(wname, task)

    monkeypatch.setattr(gm, "run_worker", fake_run_worker)
    gm.set_no_progress_detection(True, max_repeats=2)

    for t in ("任务A", "任务B", "任务C"):
        out = gm.supervisor_dispatch(_disp_state(tasks={"search": t}))
        assert out["worker_results"]["search"]["success"] is True
    assert len(calls) == 3, "任务变化的正常多轮不应被熔断"


def test_dispatch_untouched_when_disabled(monkeypatch):
    calls = []
    monkeypatch.setattr(gm, "run_worker",
                        lambda w, t, task_id="": (calls.append((w, t)), _ok(w, t))[1])
    gm.set_no_progress_detection(False)
    for _ in range(5):
        out = gm.supervisor_dispatch(_disp_state())
        assert out["worker_results"]["search"]["success"] is True
    assert len(calls) == 5


def test_route_finalizes_on_no_progress():
    assert gm.supervisor_route(
        {"no_progress_tripped": True, "all_done": False, "round": 0}
    ) == "finalize"
