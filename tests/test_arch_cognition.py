"""架构对照检查测试（2026-09-07，Cognition P1/P2 视角）

覆盖:
- C1 能力表 side_effect 声明 (search 只读 / analyze+execute 有副作用)
- C2 _partition_workers 分组 (safe 并行, risky 串行)
- C3 _build_replan_context 含失败原因 (P1: replan 可见 errors, 不重派同失败任务)
- C4 _failed_step_summary 部分失败标注 (success 反映计划完成度)
- C5 _build_judge_context 失败/部分失败 worker 附原始证据 (P1: judge 不只看 self-report)
- C6 supervisor_dispatch 副作用 worker 串行化 (P2: analyze+execute 不并行)
"""
import sys
import time
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core.agents import supervisor, workers
from agent_harness.core.agents.supervisor import WORKER_CAPABILITIES
from agent_harness.core.graph import graph_multi


# ─── C1: 能力表 side_effect 声明 ───

def test_capabilities_declare_side_effect():
    assert WORKER_CAPABILITIES["search"]["side_effect"] is False
    assert WORKER_CAPABILITIES["analyze"]["side_effect"] is True
    assert WORKER_CAPABILITIES["execute"]["side_effect"] is True


# ─── C2: 分区 ───

def test_partition_workers():
    safe, risky = graph_multi._partition_workers(
        ["search", "analyze", "execute"], WORKER_CAPABILITIES)
    assert safe == ["search"]
    assert set(risky) == {"analyze", "execute"}

    safe, risky = graph_multi._partition_workers(["search"], WORKER_CAPABILITIES)
    assert safe == ["search"] and risky == []

    safe, risky = graph_multi._partition_workers(["execute"], WORKER_CAPABILITIES)
    assert safe == [] and risky == ["execute"]


# ─── C3: replan 上下文含失败原因 ───

def test_replan_context_includes_failure_reasons():
    results = {
        "search": {
            "success": False, "output": "[搜索] LLM 后端不可用...",
            "errors": ["连接超时 (attempt 3/3)"], "data": None,
        },
        "analyze": {
            "success": True, "output": "✅ 分析完成: 结论是 X",
            "errors": [], "data": None,
        },
    }
    ctx = supervisor._build_replan_context(results, {})
    assert "❌失败" in ctx
    assert "连接超时" in ctx, "失败原因必须进入 replan 上下文"
    assert "分析完成" in ctx

    # worker_errors 兜底通道 (dispatch 级异常)
    ctx2 = supervisor._build_replan_context({}, {"execute": ["boom"]})
    assert "boom" in ctx2


def test_replan_context_flags_partial_failure():
    results = {
        "analyze": {
            "success": True,
            "output": "⚠️ 部分步骤失败 (1/3): 搜价格",
            "errors": [], "data": None,
        },
    }
    ctx = supervisor._build_replan_context(results, {})
    assert "部分步骤失败" in ctx
    assert "只补缺失部分" in ctx


# ─── C4: 失败步骤统计 ───

def _mk_step(passed: bool, name: str = "step") -> dict:
    return {"step": {"name": name}, "validation": {"passed": passed}}

def test_failed_step_summary():
    assert workers._failed_step_summary([]) == ""
    assert workers._failed_step_summary([_mk_step(True)]) == ""
    s = workers._failed_step_summary([_mk_step(True), _mk_step(False, "搜价格"),
                                       _mk_step(False, "搜评测")])
    assert "部分步骤失败 (2/3)" in s
    assert "搜价格" in s and "搜评测" in s


# ─── C5: judge 上下文附原始证据 ───

def test_judge_context_appends_raw_evidence_on_failure():
    results = {
        "search": {
            "success": True, "output": "找到 3 个来源", "data": None,
        },
        "analyze": {
            "success": False, "output": "❌ 失败: 后端不可用",
            "data": [
                {"step": {"name": "跑代码"}, "result": {"data": "Traceback: 除零"},
                 "validation": {"passed": False}},
            ],
        },
    }
    ctx = supervisor._build_judge_context(results)
    assert "Traceback" in ctx, "失败 worker 必须附原始证据供 judge 核对"
    assert "找到 3 个来源" in ctx


def test_judge_context_marks_partial_failure_raw():
    results = {
        "search": {
            "success": True,
            "output": "⚠️ 部分步骤失败 (1/2): 搜价格\n其余正常",
            "data": [{"step": {"name": "搜价格"},
                      "result": {"data": "请求 429"}, "validation": {"passed": False}}],
        },
    }
    ctx = supervisor._build_judge_context(results)
    assert "请求 429" in ctx


# ─── C6: dispatch 副作用串行化 ───

def test_dispatch_serializes_side_effect_workers(monkeypatch):
    """search 与一个副作用 worker 并行；第二个副作用 worker 必须等前者完成。"""
    timeline = {}   # name -> [start, end]
    lock = threading.Lock()
    active = {"n": 0, "max": 0}

    def fake_run_worker(wname: str, task: str):
        with lock:
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
        timeline.setdefault(wname, [None, None])[0] = time.time()
        time.sleep(0.08)
        with lock:
            active["n"] -= 1
        timeline[wname][1] = time.time()
        from agent_harness.core.pipeline.state import WorkerResult
        return WorkerResult(worker_name=wname, success=True, output="ok",
                            data=None, trace_steps=[], errors=[], elapsed_s=0.08)

    monkeypatch.setattr(graph_multi, "run_worker", fake_run_worker)

    state = {
        "request": "测试任务",
        "workers_assigned": ["search", "analyze", "execute"],
        "worker_tasks": {"search": "s", "analyze": "a", "execute": "e"},
        "worker_results": {}, "worker_errors": {}, "round": 1,
        "all_done": False, "trace_steps": [],
    }
    out = graph_multi.supervisor_dispatch(state)

    # 三个 worker 都跑完
    assert set(out["worker_results"].keys()) == {"search", "analyze", "execute"}
    # 并行度峰值 ≤2 (search + 最多 1 个副作用 worker)
    assert active["max"] <= 2, f"副作用 worker 并行度超标: {active['max']}"
    # analyze 与 execute 区间不重叠 (串行)
    a_s, a_e = timeline["analyze"]
    e_s, e_e = timeline["execute"]
    assert a_e <= e_s or e_e <= a_s, "analyze/execute 区间重叠 = 未串行化"
