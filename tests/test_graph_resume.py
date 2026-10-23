"""P0-3 — 多智能体图无 checkpointer：长任务崩一次全白烧。

痛点（评审 P0-3）：`build_multi_agent()` 直接 `graph.compile()`，没挂 checkpointer；
`run_multi_agent` 的 `graph.invoke(initial_state, config={"recursion_limit": 100})`
不传 thread_id。后果：任何节点崩溃后，已完成的 supervisor 分析、已派发并返回的
worker 结果全部丢弃，重跑从零开始 —— 600s 的任务白烧一遍 LLM 调用。

本文件验证：
- 带 checkpointer 时崩溃后续跑**不重跑已完成节点**（analyze 只跑一次）
- 崩溃点那个节点会重跑（LangGraph 契约，所以节点必须幂等）
- `run_multi_agent` 不传 checkpointer 时 config 里**没有** thread_id（行为不变）
- 传 checkpointer 时 thread_id 复用 task_id（同一会话可续）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent_harness.core.graph import graph_multi as gm


def _initial(**over) -> dict:
    st = {
        "request": "测试请求",
        "goal": "测试目标",
        "task_type": "",
        "round": 0,
        "workers_assigned": [],
        "worker_tasks": {},
        "worker_results": {},
        "worker_errors": {},
        "all_done": False,
        "no_progress_tripped": False,
        "no_progress_events": [],
        "feasibility": "",
        "feasibility_reason": "",
        "final_output": "",
        "errors": [],
        "trace_id": "trace-x",
        "task_id": "task-x",
        "trace_steps": [],
        "circuit_breaker": gm._new_circuit_breaker(),
        "token_budget": gm._new_token_budget(),
        "budget_level": "ok",
        "budget_events": [],
    }
    st.update(over)
    return st


@pytest.fixture(autouse=True)
def _isolate():
    """关无进展熔断 + 清 progress 队列，避免跨用例串状态。"""
    gm.set_no_progress_detection(False)
    yield
    gm.set_no_progress_detection(False)
    gm.clear_progress_queue()


def _install_fake_nodes(monkeypatch, calls: dict, fail_collect_times: int = 0):
    """只换节点函数，图结构、边、路由全用真的 —— 断点续行为才是真实验证。"""
    fail = {"left": fail_collect_times}

    def fake_analyze(state):
        calls["analyze"] += 1
        return {
            "task_type": "test",
            "feasibility": "ok",
            "feasibility_reason": "ok",
            "workers_assigned": [],
        }

    def fake_dispatch(state):
        calls["dispatch"] += 1
        return {"worker_tasks": {}}

    def fake_collect(state):
        calls["collect"] += 1
        if fail["left"] > 0:
            fail["left"] -= 1
            raise RuntimeError("模拟崩在 collect")
        return {"worker_results": {}, "all_done": True}

    def fake_finalize(state):
        calls["finalize"] += 1
        return {"final_output": "resumed-ok"}

    monkeypatch.setattr(gm, "supervisor_analyze", fake_analyze)
    monkeypatch.setattr(gm, "supervisor_dispatch", fake_dispatch)
    monkeypatch.setattr(gm, "supervisor_collect", fake_collect)
    monkeypatch.setattr(gm, "supervisor_finalize", fake_finalize)


def test_resume_skips_completed_nodes(monkeypatch):
    """崩溃后续跑：已完成节点不重算，崩溃点节点重跑。"""
    calls = {"analyze": 0, "dispatch": 0, "collect": 0, "finalize": 0}
    _install_fake_nodes(monkeypatch, calls, fail_collect_times=1)

    graph = gm.build_multi_agent(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "t-resume"}, "recursion_limit": 100}

    with pytest.raises(RuntimeError):
        graph.invoke(_initial(), config=cfg)

    # 第一轮：analyze/dispatch 完成并落 checkpoint，collect 崩
    assert calls["analyze"] == 1
    assert calls["dispatch"] == 1
    assert calls["collect"] == 1
    assert calls["finalize"] == 0

    final = graph.invoke(None, config=cfg)

    assert final["final_output"] == "resumed-ok"
    assert calls["analyze"] == 1, "已完成节点被重跑 —— checkpoint 没起作用（白烧 LLM）"
    assert calls["dispatch"] == 1
    assert calls["collect"] == 2, "崩溃点节点应重跑（LangGraph 契约）"
    assert calls["finalize"] == 1


def test_no_checkpointer_keeps_old_behavior(monkeypatch):
    """不传 checkpointer：config 里不得出现 thread_id，状态不带旧快照。"""
    captured: dict = {}

    class FakeGraph:
        def invoke(self, state, config):
            captured["state"] = state
            captured["config"] = config
            return {"final_output": "ok", "token_budget": state["token_budget"]}

    monkeypatch.setattr(gm, "build_multi_agent", lambda checkpointer=None: FakeGraph())

    r = gm.run_multi_agent("req", task_id="t9")

    assert "configurable" not in captured["config"]
    assert captured["state"]["task_id"] == "t9"
    assert r["final_output"] == "ok"


def test_checkpointer_uses_task_id_as_thread(monkeypatch):
    """带 checkpointer：thread_id 复用 task_id，同会话崩溃后能续。"""
    captured: dict = {}

    class FakeGraph:
        def invoke(self, state, config):
            captured["config"] = config
            return {"final_output": "ok", "token_budget": state["token_budget"]}

    monkeypatch.setattr(gm, "build_multi_agent", lambda checkpointer=None: FakeGraph())

    gm.run_multi_agent("req", task_id="sess-42", checkpointer=InMemorySaver())

    assert captured["config"]["configurable"]["thread_id"] == "sess-42"
    assert captured["config"]["recursion_limit"] == 100


def test_checkpointer_falls_back_to_trace_id(monkeypatch):
    """task_id 为空（CLI 场景）时用 trace_id 兜底，不能没有 thread_id。"""
    captured: dict = {}

    class FakeGraph:
        def invoke(self, state, config):
            captured["config"] = config
            return {"final_output": "ok", "token_budget": state["token_budget"]}

    monkeypatch.setattr(gm, "build_multi_agent", lambda checkpointer=None: FakeGraph())

    gm.run_multi_agent("req", checkpointer=InMemorySaver())

    assert captured["config"]["configurable"]["thread_id"], "空 thread_id = 所有会话共用一条状态，串台"


def test_resume_without_checkpointer_is_ignored(monkeypatch):
    """resume=True 但没带 checkpointer：退回正常 invoke，不传 None（否则崩）。"""
    captured: dict = {}

    class FakeGraph:
        def invoke(self, state, config):
            captured["state"] = state
            captured["config"] = config
            return {"final_output": "ok", "token_budget": (state or {}).get("token_budget", gm._new_token_budget())}

    monkeypatch.setattr(gm, "build_multi_agent", lambda checkpointer=None: FakeGraph())

    r = gm.run_multi_agent("req", task_id="t1", resume=True)

    assert captured["state"] is not None, "无 checkpointer 时不能把 None 当输入喂给图"
    assert r["final_output"] == "ok"


def test_resume_true_passes_none_input(monkeypatch):
    """resume=True + 有 checkpointer：输入必须是 None。

    传完整 state 会被 LangGraph 当作新输入合并，等于放弃快照（还白跑一遍已完成节点）。
    """
    captured: dict = {}

    class FakeGraph:
        def invoke(self, state, config):
            captured["state"] = state
            return {"final_output": "resumed", "token_budget": gm._new_token_budget()}

    monkeypatch.setattr(gm, "build_multi_agent", lambda checkpointer=None: FakeGraph())

    r = gm.run_multi_agent("req", task_id="t1", checkpointer=InMemorySaver(), resume=True)

    assert captured["state"] is None, "resume 时必须传 None，否则覆盖 checkpoint"
    assert r["final_output"] == "resumed"


def test_sqlite_checkpointer_survives_new_instance(tmp_path, monkeypatch):
    """真·崩溃场景：换全新 saver 实例（等价新进程）仍能续跑。

    同时实测 state 里自定义对象（CircuitBreaker / TokenBudget）的序列化 ——
    SQLite saver 接入最大的风险点就在这里：对象不可序列化会直接让整条链路崩。
    """
    db = str(tmp_path / "cp.sqlite")
    calls = {"analyze": 0, "dispatch": 0, "collect": 0, "finalize": 0}
    _install_fake_nodes(monkeypatch, calls, fail_collect_times=1)

    cfg = {"configurable": {"thread_id": "t-disk"}, "recursion_limit": 100}

    g1 = gm.build_multi_agent(checkpointer=gm.make_sqlite_checkpointer(db))
    with pytest.raises(RuntimeError):
        g1.invoke(_initial(), config=cfg)
    assert calls["analyze"] == 1

    # 等价于进程重启：全新 saver + 全新图，只靠磁盘上的 checkpoint
    g2 = gm.build_multi_agent(checkpointer=gm.make_sqlite_checkpointer(db))
    final = g2.invoke(None, config=cfg)

    assert final["final_output"] == "resumed-ok"
    assert calls["analyze"] == 1, "跨实例续跑失败：已完成节点被重跑（= 白烧 LLM）"
    assert calls["collect"] == 2
    assert Path(db).exists() and Path(db).stat().st_size > 0, "checkpoint 没落盘"


def test_circuit_breaker_pickle_roundtrip():
    """checkpoint 必须能存 CircuitBreaker —— 状态完整往返，锁/时钟可重建。

    没有 __getstate__/__setstate__ 时 pickle 直接失败（_lock + lambda 时钟），
    SQLite checkpointer 一条 checkpoint 都写不进。
    """
    import pickle

    from agent_harness.core.pipeline.circuit_breaker import CircuitBreaker

    cb = CircuitBreaker(max_tokens=1000, max_time_s=10)
    cb.add_tokens(123)
    cb.record_output("w1", "输出 A")
    cb._state = CircuitBreaker.OPEN
    cb._open_hard = True

    back = pickle.loads(pickle.dumps(cb))

    assert back.tokens_used == 123
    assert back.max_tokens == 1000
    assert back.max_no_progress == cb.max_no_progress
    assert back._state == CircuitBreaker.OPEN
    assert back._open_hard is True
    assert back._worker_fingerprints == cb._worker_fingerprints
    assert back._recent_fps == cb._recent_fps
    # 锁与时钟要能重建，否则反序列化后一用就崩
    back.add_tokens(1)
    assert back.tokens_used == 124
    assert back.check()["tripped"] is True


def test_make_checkpointer_degrades_on_bad_path(tmp_path):
    """checkpointer 造不出来时降级为 None，不能让主流程起不来。"""
    bogus = str(tmp_path / "a.sqlite" / "b.sqlite" / "c.sqlite")  # 父路径是文件
    Path(tmp_path / "a.sqlite").write_text("not a dir", encoding="utf-8")
    assert gm.make_sqlite_checkpointer(bogus) is None
