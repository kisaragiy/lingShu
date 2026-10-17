"""LWP17 接线验收 — `core/failure_attribution.py` 的决定性错误归因接到编排收尾主回路。

痛点（SOUL 点名复发模式「装了没通电」）：`failure_attribution` 模块 + 15 个单测齐备，
但 `grep -rn 'failure_attribution' src/ | grep -v core/failure_attribution.py` 在生产
调用链上 **0 命中** —— 归因报告永远没人看。观测到「失败」却看不到「根因」。

本文件验证 supervisor_finalize 的接线契约：
- 完成契约**未完成** → 调归因，trace 落 `failure_attribution`（纯确定性、零 LLM）
- 完成契约**完成** → 完全不调用（零开销，behavior 与接线前一致）
- 存在决定性根因 → final_output 追加「🔍 失败归因」小节
- 归因内部抛异常 → 捕获跳过，收尾照常完成（保守原则，绝不中断）
- 纯确定性：同一 state 两次调用产出同一 trace
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from agent_harness.core.graph import graph_multi as gm

_FAKE_REPORT = "调研报告正文内容，足够长以通过长度门禁。" * 3


def _state(**extra) -> dict:
    """w1 成功 / w2 失败 → 完成契约 incomplete → complete=False。"""
    st = {
        "request": "研究 X 主题",
        "workers_assigned": ["w1", "w2"],
        "worker_tasks": {"w1": "任务一", "w2": "任务二"},
        "worker_results": {
            "w1": {"success": True, "output": "有效结果内容足够长", "errors": [], "elapsed_s": 1.0},
            "w2": {"success": False, "output": "", "errors": ["boom"], "elapsed_s": 2.0},
        },
        "round": 1,
        "session_id": "test-session",
        "trace_steps": [],
    }
    st.update(extra)
    return st


def _all_ok_state(**extra) -> dict:
    st = _state(**extra)
    st["worker_results"] = {
        "w1": {"success": True, "output": "有效结果内容足够长", "errors": [], "elapsed_s": 1.0},
        "w2": {"success": True, "output": "另一个有效结果内容", "errors": [], "elapsed_s": 2.0},
    }
    return st


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """关掉无进展熔断 + 桩掉 LLM 报告生成（本测试只关心归因接线）。"""
    gm.set_no_progress_detection(False)

    def _fake_call_llm(messages, system_prompt="", max_tokens=None, **kw):
        return _FAKE_REPORT, "stop"

    monkeypatch.setattr("agent_harness.core.agents.supervisor._call_llm_full", _fake_call_llm)
    yield
    gm.set_no_progress_detection(False)
    gm.clear_progress_queue()


def _find_trace(out: dict, step: str):
    return [t for t in (out.get("trace_steps") or []) if t.get("step") == step]


# ═══════════ ① 契约未完成 → 归因被调用并落 trace ═══════════

def test_incomplete_triggers_attribution():
    out = gm.supervisor_finalize(_state())
    attrs = _find_trace(out, "failure_attribution")
    assert len(attrs) == 1, f"期望恰好一条归因 trace，得到 {attrs}"
    a = attrs[0]
    # AttributionReport.to_dict() 的关键字段
    for k in ("total_steps", "decisive_index", "decisive_agent", "decisive_action",
              "recovered", "confidence", "candidates"):
        assert k in a, f"归因 trace 缺字段 {k}"
    # 失败的是 w2 → 决定性错误应指向 w2（可单点救活）
    assert a["decisive_agent"] == "w2", a


# ═══════════ ② 契约完成 → 零调用（不误伤成功路径）═══════════

def test_complete_skips_attribution():
    out = gm.supervisor_finalize(_all_ok_state())
    assert _find_trace(out, "failure_attribution") == [], "契约完成时不应产生归因 trace"
    assert "失败归因" not in out.get("final_output", "")


# ═══════════ ③ 存在决定性根因 → final 追加小节 ═══════════

def test_decisive_section_in_final():
    out = gm.supervisor_finalize(_state())
    final = out.get("final_output", "")
    assert "## 🔍 失败归因" in final, "有决定性根因时应收尾追加失败归因小节"
    assert "w2" in final


# ═══════════ ④ 归因抛异常 → 捕获跳过，收尾照常 ═══════════

def test_attribution_failure_is_caught(monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("归因内部炸了")

    monkeypatch.setattr("agent_harness.core.failure_attribution.attribute_from_run_result", _boom)
    out = gm.supervisor_finalize(_state())  # 不应抛出
    assert _find_trace(out, "failure_attribution") == []
    assert out.get("final_output"), "收尾必须照常产出"
    # 完成契约小节仍在（说明收尾完整走完）
    assert "完成契约" in out["final_output"]


# ═══════════ ⑤ 纯确定性：同输入 → 同 trace ═══════════

def test_deterministic():
    a = _find_trace(gm.supervisor_finalize(_state()), "failure_attribution")
    b = _find_trace(gm.supervisor_finalize(_state()), "failure_attribution")
    assert a == b, "同输入两次归因 trace 必须一致"
