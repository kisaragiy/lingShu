"""LWP17 失败归因接线的验收门禁（graph_multi.supervisor_finalize）。

设计文档：docs/lwp17-19-wiring-design.md §1「行为契约（可机械验证）」。
每条测试对应契约里的一条，缺一条就等于那条契约没实现。

(a) 完成契约 complete → 完全不调用归因，final_output 与 trace 都不带归因痕迹
(b) 有未完成项 → trace 出现 failure_attribution 步骤，且 decisive 指向真正失败的 worker
(c) 非法 state（worker_results 不是 dict）→ 不抛异常、正常收尾、跳过归因
(d) 归因自身抛异常 → 捕获 + stderr 记录 + 收尾照常完成（保守原则）
"""
from __future__ import annotations

import pytest

from agent_harness.core.graph import graph_multi


@pytest.fixture()
def llm_stub(monkeypatch):
    """把收尾的报告 LLM 换成确定性桩，测试只关心归因接线。"""
    from agent_harness.core.agents import supervisor as sup_mod

    calls: list[dict] = []

    def fake_call(messages, system_prompt=None, max_tokens=None, **_):
        calls.append({"messages": messages, "system_prompt": system_prompt})
        return "报告正文：已根据所有 Worker 结果完成分析并生成完整调研报告。", "stop"

    monkeypatch.setattr(sup_mod, "_call_llm_full", fake_call, raising=True)
    return calls


def _ok(output: str = "这里有足够长的真实输出内容") -> dict:
    return {"success": True, "output": output, "errors": [], "elapsed_s": 1.5}


def _bad(output: str = "抓取失败", errors=("连接超时",)) -> dict:
    return {"success": False, "output": output, "errors": list(errors), "elapsed_s": 0.4}


def test_a_complete_contract_skips_attribution_entirely(llm_stub):
    """(a) 全部交付 → 不调归因：无小节、无 trace 步骤。"""
    state = {
        "session_id": "test-lwp17-a",
        "request": "调研 A",
        "round": 1,
        "worker_results": {"w_fetch": _ok()},
        "worker_tasks": {"w_fetch": "抓取 A 的数据"},
        "feasibility": "feasible",
    }

    out = graph_multi.supervisor_finalize(state)

    assert out["completion_contract"]["complete"] is True
    assert "失败归因" not in out["final_output"]
    steps = [s.get("step") for s in out["trace_steps"]]
    assert "failure_attribution" not in steps
    # 归因零成本：连 trace 都不用带
    assert "finalize" in steps


def test_b_incomplete_contract_attributes_to_failing_worker(llm_stub):
    """(b) 有未完成项 → trace 记归因，decisive 指向失败的那个 worker。"""
    state = {
        "session_id": "test-lwp17-b",
        "request": "调研 B",
        "round": 2,
        "worker_results": {
            "w_fetch": _ok(),
            "w_analyze": _bad(),
        },
        "worker_tasks": {"w_fetch": "抓取 B", "w_analyze": "分析 B"},
        "feasibility": "feasible",
    }

    out = graph_multi.supervisor_finalize(state)

    contract = out["completion_contract"]
    assert contract["complete"] is False
    assert [f["worker"] for f in contract["incomplete"]] == ["w_analyze"]

    attr_steps = [s for s in out["trace_steps"] if s.get("step") == "failure_attribution"]
    assert len(attr_steps) == 1, out["trace_steps"]
    assert attr_steps[0]["decisive_agent"] == "w_analyze"
    assert attr_steps[0]["decisive_action"] == "分析 B"
    assert "失败归因" in out["final_output"]
    assert "w_analyze" in out["final_output"]


def test_c_malformed_worker_results_does_not_raise(llm_stub):
    """(c) worker_results 是字符串 → 收尾不得抛异常（外部脏数据不该炸主链路）。"""
    state = {
        "session_id": "test-lwp17-c",
        "request": "调研 C",
        "round": 1,
        "worker_results": "这不是 dict",
        "feasibility": "feasible",
    }

    out = graph_multi.supervisor_finalize(state)

    assert isinstance(out["final_output"], str)
    assert "completion_contract" in out


def test_d_attribution_failure_is_swallowed(llm_stub, monkeypatch, capsys):
    """(d) 归因自己炸了 → 降级：stderr 有记录、收尾照常出报告。"""
    import agent_harness.core.failure_attribution as fa

    def boom(*_a, **_k):
        raise RuntimeError("归因模块故障")

    monkeypatch.setattr(fa, "attribute_from_run_result", boom, raising=True)

    state = {
        "session_id": "test-lwp17-d",
        "request": "调研 D",
        "round": 1,
        "worker_results": {"w_fetch": _ok(), "w_analyze": _bad()},
        "worker_tasks": {"w_fetch": "抓取 D", "w_analyze": "分析 D"},
        "feasibility": "feasible",
    }

    out = graph_multi.supervisor_finalize(state)

    assert "报告正文" in out["final_output"]
    assert "失败归因" not in out["final_output"]
    assert not [s for s in out["trace_steps"] if s.get("step") == "failure_attribution"]
    assert "失败归因跳过" in capsys.readouterr().err
