"""P-预算接线 — budget.py 的 TokenBudget 三层软限制（WARN/ASK/STOP）接到编排主回路。

痛点：`core/budget.py` 的 `TokenBudget`（WARN/ASK/STOP + kill switch + cache 指标）
原语齐备，但 `grep -rn 'TokenBudget' src/ | grep -v core/budget.py` 在**生产调用链上
0 命中** —— 有芯未接线。成本兜底此前只有 `CircuitBreaker` 的**硬阈值**一撞即停，
没有「先提醒 / 再请求确认」的中间档。

本文件验证（四场景 + 防误伤）：
- WARN（≥70% 软限）：记日志提醒，**不**打断本轮（all_done 不被改写）
- ASK（≥100% 软限）：发告警（stderr + progress 队列），提前收尾，route → finalize
- STOP（≥200% 软限）：kill switch 置位 + 急停，route → finalize
- 正常流程（用量远低于软限）：零触发，多轮照常派发
- 未提供 token_budget 的旧 state：行为与接线前一致（不误伤既有调用方）
- token 增量确实从 pipeline.llm 台账喂进预算（与硬熔断共用同一来源）
"""
from __future__ import annotations

import queue as _queue
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from agent_harness.core.budget import TokenBudget
from agent_harness.core.graph import graph_multi as gm
from agent_harness.core.pipeline.state import WorkerResult

SOFT = 100_000


def _ok(wname: str, task: str) -> WorkerResult:
    return WorkerResult(worker_name=wname, success=True, output="ok-" + task,
                        data=None, trace_steps=[], errors=[], elapsed_s=0)


@pytest.fixture(autouse=True)
def _isolate():
    """关掉无进展熔断 + 清干净 progress 队列，避免跨用例串状态。"""
    gm.set_no_progress_detection(False)
    yield
    gm.set_no_progress_detection(False)
    gm.clear_progress_queue()


def _budget(used: int, soft: int = SOFT, **kw) -> TokenBudget:
    """造一个「已用 used」的预算；水位线对齐当前台账 → 喂 0 delta，
    于是调用点只做一次 check()，级别完全由 used 决定（确定性，不依赖真实 token）。"""
    b = TokenBudget(soft_limit=soft, **kw)
    b.used = used
    b._ledger_total = gm.ledger_total()  # type: ignore[attr-defined]
    return b


def _state(used=None, round_: int = 1, tasks=None, **extra) -> dict:
    st = {
        "request": "研究 X",
        "workers_assigned": ["search"],
        "worker_tasks": tasks or {"search": "子任务"},
        "worker_results": {}, "worker_errors": {}, "round": round_,
        "all_done": False, "trace_steps": [],
    }
    if used is not None:
        st["token_budget"] = _budget(used)
    st.update(extra)
    return st


# ═══════════ ① WARN：记日志，不打断 ═══════════

def test_warn_logs_and_does_not_block(monkeypatch, capsys):
    monkeypatch.setattr(gm, "run_worker", lambda w, t: _ok(w, t))
    st = _state(used=int(SOFT * 0.75))          # 75% → WARN
    out = gm.supervisor_dispatch(st)

    err = capsys.readouterr().err
    assert "预算" in err and "提醒" in err, f"WARN 必须记日志，stderr={err!r}"
    assert out["budget_level"] == "warn"
    assert out["all_done"] is False, "WARN 只是提醒，不得提前收尾"
    assert [e["level"] for e in out["budget_events"]] == ["warn"]
    assert any(s.get("step") == "supervisor_budget" for s in out["trace_steps"])
    # 路由：WARN 之后仍应继续 replan（软提醒 ≠ 中止）
    assert gm.supervisor_route({**st, **out}) == "replan"


def test_warn_fires_only_once(monkeypatch, capsys):
    """warned 标志由 budget.py 维护 → 不重复刷屏。"""
    monkeypatch.setattr(gm, "run_worker", lambda w, t: _ok(w, t))
    st = _state(used=int(SOFT * 0.75))
    first = gm.supervisor_dispatch(st)
    capsys.readouterr()
    second = gm.supervisor_dispatch(st)
    assert first["budget_level"] == "warn"
    assert second["budget_level"] == "ok"
    assert "预算" not in capsys.readouterr().err


# ═══════════ ② ASK：发告警 + 提前收尾 ═══════════

def test_ask_alerts_and_halts(monkeypatch, capsys):
    q: _queue.Queue = _queue.Queue()
    gm.set_progress_queue(q)
    monkeypatch.setattr(gm, "run_worker", lambda w, t: _ok(w, t))
    st = _state(used=SOFT)                       # 100% → ASK

    out = gm.supervisor_dispatch(st)

    err = capsys.readouterr().err
    assert "需用户确认" in err, f"ASK 必须发告警，stderr={err!r}"
    items = []
    while not q.empty():
        items.append(q.get()["content"])
    assert any("确认" in c for c in items), f"ASK 必须同时推 progress 告警: {items}"
    assert out["budget_level"] == "ask"
    assert out["all_done"] is True, "ASK 未确认 → 提前收尾，不再 replan"
    assert gm.supervisor_route({**st, **out}) == "finalize"


def test_ask_after_user_confirm_continues(monkeypatch):
    monkeypatch.setattr(gm, "run_worker", lambda w, t: _ok(w, t))
    st = _state(used=SOFT)
    st["token_budget"].asked = True              # 用户已确认
    out = gm.supervisor_dispatch(st)
    assert out["budget_level"] == "ask"
    assert out["all_done"] is False, "已确认后不得再拦"
    assert gm.supervisor_route({**st, **out}) == "replan"


# ═══════════ ③ STOP：kill switch + 中止 ═══════════

def test_stop_kills_and_halts(monkeypatch, capsys):
    q: _queue.Queue = _queue.Queue()
    gm.set_progress_queue(q)
    monkeypatch.setattr(gm, "run_worker", lambda w, t: _ok(w, t))
    st = _state(used=SOFT * 2)                   # 200% → STOP（跑飞）

    out = gm.supervisor_dispatch(st)

    b = st["token_budget"]
    assert b.killed is True, "STOP 必须触发 session kill switch"
    assert b.kill_reason, "kill 必须带原因"
    assert out["budget_level"] == "stop"
    assert out["all_done"] is True
    assert "急停" in capsys.readouterr().err
    assert any(e["level"] == "stop" for e in out["budget_events"])
    route_state = {**st, **out}
    assert gm.supervisor_route(route_state) == "finalize"
    # kill switch 生效后，即使级别回落也不许继续
    assert b.can_continue(gm.BudgetLevel.OK) is False


# ═══════════ ④ 正常流程：零触发 ═══════════

def test_normal_flow_is_untouched(monkeypatch, capsys):
    calls: list[str] = []
    monkeypatch.setattr(gm, "run_worker",
                        lambda w, t: (calls.append(w), _ok(w, t))[1])
    st = _state(used=1_000)                      # 1% —— 远低于所有阈值
    for _ in range(3):
        out = gm.supervisor_dispatch(st)
        assert out["budget_level"] == "ok"
        assert out["budget_events"] == []
        assert out["all_done"] is False
    assert len(calls) == 3, "正常多轮不得被预算误伤"
    assert "预算" not in capsys.readouterr().err
    assert gm.supervisor_route({**st, **out}) == "replan"


def test_state_without_budget_behaves_as_before(monkeypatch):
    """旧调用方（state 里没有 token_budget）→ 与接线前完全一致。"""
    calls: list[str] = []
    monkeypatch.setattr(gm, "run_worker",
                        lambda w, t: (calls.append(w), _ok(w, t))[1])
    st = _state()                                # 不带 token_budget
    for _ in range(2):
        out = gm.supervisor_dispatch(st)
        assert out["budget_level"] == "ok"
        assert out["budget_events"] == []
    assert len(calls) == 2
    assert gm.supervisor_route({**st, **out}) == "replan"


# ═══════════ token 增量确实从台账喂进来 ═══════════

def test_ledger_delta_is_fed_into_budget(monkeypatch):
    """本轮 token 增量（含非 worker 路径的消耗）必须累加进预算，且不重复计数。"""
    fake = {"n": 0}
    monkeypatch.setattr(gm, "ledger_total", lambda: fake["n"])

    def fake_worker(w, t):
        fake["n"] += 30_000                      # 模拟本轮真实 LLM 消耗
        return _ok(w, t)

    monkeypatch.setattr(gm, "run_worker", fake_worker)
    st = _state(used=0)

    out = gm.supervisor_dispatch(st)
    assert st["token_budget"].used == 30_000, "本轮 token 增量必须记进预算"
    assert out["budget_level"] == "ok"

    fake["n"] += 5_000                           # 别的路径又烧了 5K
    out2 = gm.supervisor_dispatch(st)
    assert st["token_budget"].used == 65_000, "累加且不重复计数"
    assert out2["budget_level"] == "ok"


# ═══════════ 预算构造 / env 覆盖 ═══════════

def test_env_limits_override(monkeypatch):
    monkeypatch.setenv("HARNESS_TOKEN_SOFT_LIMIT", "1234")
    monkeypatch.setenv("HARNESS_TOKEN_HARD_LIMIT", "5678")
    b = gm._new_token_budget()
    assert b.soft_limit == 1234 and b.hard_limit == 5678


def test_env_invalid_value_ignored(monkeypatch, capsys):
    monkeypatch.setenv("HARNESS_TOKEN_SOFT_LIMIT", "abc")
    b = gm._new_token_budget()
    assert b.soft_limit == TokenBudget().soft_limit
    assert "忽略" in capsys.readouterr().err


# ═══════════ run_multi_agent 端到端接线 ═══════════

def test_run_multi_agent_creates_and_reports_budget(monkeypatch):
    seen = {}

    class _FakeGraph:
        def invoke(self, state, config=None):
            seen["budget"] = state.get("token_budget")
            return {**state, "final_output": "ok", "budget_level": "ok",
                    "budget_events": []}

    monkeypatch.setattr(gm, "build_multi_agent", lambda: _FakeGraph())
    res = gm.run_multi_agent("研究 X")

    assert isinstance(seen["budget"], TokenBudget), "run 必须注入会话级 TokenBudget"
    assert res["budget"]["level"] == "ok"
    assert res["budget"]["used"] == 0
    assert res["budget_level"] == "ok"
    assert res["budget_events"] == []


def test_run_multi_agent_honours_env_limit(monkeypatch):
    monkeypatch.setenv("HARNESS_TOKEN_SOFT_LIMIT", "7777")

    class _FakeGraph:
        def invoke(self, state, config=None):
            return {**state, "final_output": "ok"}

    monkeypatch.setattr(gm, "build_multi_agent", lambda: _FakeGraph())
    res = gm.run_multi_agent("研究 X")
    assert res["budget"]["limit"] == 7777
    assert res["budget_level"] == "ok"


# ═══════════ 真 graph 端到端：接线确实落在 LangGraph 执行路径上 ═══════════
# 用真实 supervisor 图 + mock LLM（conftest 的 mock_session）跑完整一条链，
# 只把「token 台账」换成一个固定读数的假台账：首次结算 delta = n，
# 之后 delta 恒为 0（同一轮里 dispatch / route 各结算一次也不会重复累加）。
# 阈值刻意选 1000 << 熔断器硬阈值 100000，保证是**预算**（非硬熔断）在起作用。

def _fixed_ledger(n: int):
    return lambda: n


def test_e2e_real_graph_stops_on_budget(mock_session, monkeypatch, capsys):
    monkeypatch.setenv("HARNESS_TOKEN_SOFT_LIMIT", "1000")
    monkeypatch.setattr(gm, "ledger_total", _fixed_ledger(2000))  # 2× 软限

    res = gm.run_multi_agent("研究 X")

    err = capsys.readouterr().err
    assert res["budget_level"] == "stop", res["budget_level"]
    assert res["budget"]["killed"] is True, "真执行路径上 kill switch 必须置位"
    assert res["budget"]["used"] == 2000
    assert "急停" in err
    assert [e["level"] for e in res["budget_events"]] == ["stop"]


def test_e2e_real_graph_asks_on_budget(mock_session, monkeypatch):
    monkeypatch.setenv("HARNESS_TOKEN_SOFT_LIMIT", "1000")
    monkeypatch.setattr(gm, "ledger_total", _fixed_ledger(1000))  # 正好 1× 软限

    res = gm.run_multi_agent("研究 X")

    assert res["budget_level"] == "ask", res["budget_level"]
    assert res["budget"]["killed"] is False, "ASK 是告警不是急停"
    assert res["budget_events"], "ASK 必须留下预算事件"


def test_e2e_real_graph_normal_budget_untouched(mock_session, monkeypatch):
    """真路径 + 远低于软限的用量 → 预算零事件，图照常跑完（不误伤）。"""
    monkeypatch.setattr(gm, "ledger_total", _fixed_ledger(10))

    res = gm.run_multi_agent("研究 X")

    assert res["budget_level"] == "ok"
    assert res["budget_events"] == []
    assert res["budget"]["killed"] is False
    assert res["errors"] == []
