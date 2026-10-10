"""WP16 — abstention（该放弃时放弃）。

对标 HERA (arXiv:2610.06563)：不可行任务应被**识别并明确放弃**，而不是硬编一份
看似完整的报告。灵枢此前的所有路径最终都汇到 `supervisor_finalize` 的「1000-2000 字
调研报告」prompt（样板房风险）——证据不足时它照样产出一份结构完整的报告。

本文件验证三态验收 + 放弃路径：
① 信息不足/不可行任务 → feasibility="infeasible"（且走 finalize）；
② 正常可解任务 → 不误判（保守：只有显式 infeasible 标记才放弃）；
③ infeasible 时 final_output 声明「无法完成 + 理由 + 已尝试手段」，
   且**不含**编造的报告结构（执行摘要/主要发现/参考来源），也不调用报告 LLM。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core.agents import supervisor
from agent_harness.core.graph import graph_multi


def _collect_state(round_: int = 0) -> dict:
    """A state where every worker succeeded but the info may still be insufficient."""
    return {
        "request": "找出 XYZ 公司 2019 年未公开的内部财务明细",
        "workers_assigned": ["search", "analyze"],
        "worker_tasks": {"search": "检索公开来源", "analyze": "汇总结果"},
        "worker_results": {
            "search": {"success": True, "output": "未找到任何公开来源", "elapsed_s": 1.0},
            "analyze": {"success": True, "output": "缺少输入，无法汇总", "elapsed_s": 1.0},
        },
        "worker_errors": {},
        "round": round_,
        "trace_steps": [],
    }


# ── ① 信息不足 → infeasible ──

def test_infeasible_task_marked(monkeypatch):
    """验收员明确报 infeasible → feasibility="infeasible" 且收尾。"""
    monkeypatch.setattr(
        supervisor, "_call_llm",
        lambda *a, **k: '{"done": false, "infeasible": true, "reason": "目标实体不存在，任何公开数据源都查不到"}',
    )
    out = supervisor.supervisor_collect(_collect_state())
    assert out["feasibility"] == "infeasible", f"应判不可行，得到 {out.get('feasibility')!r}"
    assert out["all_done"] is True, "不可行应直接进入收尾，不再 replan"
    assert "不存在" in out["feasibility_reason"]


# ── ② 保守：可解任务不误判 ──

def test_solvable_task_not_marked_infeasible(monkeypatch):
    monkeypatch.setattr(supervisor, "_call_llm",
                        lambda *a, **k: '{"done": true, "reason": "信息充分，任务完成"}')
    out = supervisor.supervisor_collect(_collect_state())
    assert out["feasibility"] != "infeasible", "可解任务绝不能被判不可行"


def test_need_more_info_is_not_abstention(monkeypatch):
    """done=false 但**没有** infeasible 标记 → 属于「还缺信息」，不是放弃。"""
    monkeypatch.setattr(supervisor, "_call_llm",
                        lambda *a, **k: '{"done": false, "reason": "还缺 X 维度的数据"}')
    out = supervisor.supervisor_collect(_collect_state())
    assert out["feasibility"] != "infeasible", "缺信息 ≠ 不可行（保守原则）"


def test_unparseable_verify_response_is_not_abstention(monkeypatch):
    """验收 LLM 返回非 JSON → 保守处理，不判不可行。"""
    monkeypatch.setattr(supervisor, "_call_llm", lambda *a, **k: "抱歉，我无法判断。")
    out = supervisor.supervisor_collect(_collect_state())
    assert out["feasibility"] != "infeasible"


def test_route_finalizes_on_infeasible():
    """不可行时即使 all_done=False / round 未到上限，也必须 finalize 而非 replan。"""
    r = graph_multi.supervisor_route(
        {"feasibility": "infeasible", "all_done": False, "round": 0}
    )
    assert r == "finalize", f"不可行必须收尾，得到 {r!r}"


# ── ③ infeasible 的最终输出：声明无法完成，不编造报告 ──

def test_infeasible_finalize_declares_cannot_complete(monkeypatch):
    monkeypatch.setattr(
        supervisor, "_call_llm",
        lambda *a, **k: '{"done": false, "infeasible": true, "reason": "数据源 A 与 B 均不可达"}',
    )
    state = _collect_state()
    merged = {**state, **supervisor.supervisor_collect(state)}

    # finalize 阶段绝不能再调报告 LLM
    def _boom(*a, **k):
        raise AssertionError("infeasible 路径不应调用报告生成 LLM（会硬编报告）")
    monkeypatch.setattr(supervisor, "_call_llm_full", _boom)
    monkeypatch.setattr(supervisor, "_call_llm", _boom)

    out = graph_multi.supervisor_finalize(merged)
    final = out["final_output"]

    assert "无法完成" in final, "应明确声明无法完成"
    assert "不可达" in final, "应带上不可行理由"
    assert "search" in final and "analyze" in final, "应列出已尝试的手段"
    # 不得出现编造的调研报告结构
    for marker in ("执行摘要", "主要发现", "参考来源", "📋"):
        assert marker not in final, f"放弃路径不应含报告结构 {marker!r}"
    assert out["feasibility"] == "infeasible"
    assert out["trace_steps"][0]["step"] == "finalize_abstention"
