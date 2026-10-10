"""Worker 在 LLM 不可用时不得丢弃已拿到的工具结果。

实测踩到的真 bug（2026-09-11）：搜索工具已经成功返回 5 条结果，
但 Worker 的 `_worker_synthesize` 只把工具结果喂给 LLM 做总结；
LLM 一挂就走 `_fallback_worker_result` 模板 —— **整个 evidence 被丢掉**，
用户看到「由于 LLM 后端不可用，搜索结果不可用」，而数据其实早就到手了。

后果：LLM 抖动时可用性被无谓降低（明明能交付，却报"不可用"）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _state_with_result(data: str):
    return {
        "task": "查一下 langgraph 多智能体的资料",
        "worker_name": "search",
        "results": [
            {
                "tool": "search",
                "result": {"success": True, "error": None, "data": data},
                "validation": {"passed": True, "reason": "", "severity": "ok"},
            }
        ],
    }


EVIDENCE = ("[搜索结果] 1. LangGraph 官方文档 - langchain-ai.github.io\n"
            "2. 多智能体编排实践 - 知乎专栏")


def test_keeps_tool_evidence_when_llm_dead(monkeypatch):
    """LLM 全挂 → 必须交出工具已拿到的结果，而不是降级模板。"""
    from agent_harness.core.agents import workers

    monkeypatch.setattr(workers, "_call_llm", lambda *a, **kw: "")

    out = workers._worker_synthesize(_state_with_result(EVIDENCE))
    final = out["final_output"]

    assert "LangGraph" in final, (
        f"工具结果被降级模板覆盖丢弃了。实测输出：{final[:150]!r}"
    )
    assert "搜索结果不可用" not in final, "不应输出「结果不可用」——数据明明已经拿到了"


def test_falls_back_to_template_when_no_evidence(monkeypatch):
    """反向控制：确实没有任何工具结果时，才允许用降级模板。"""
    from agent_harness.core.agents import workers

    monkeypatch.setattr(workers, "_call_llm", lambda *a, **kw: "")

    state = _state_with_result(EVIDENCE)
    state["results"] = []          # 没有任何成功结果
    out = workers._worker_synthesize(state)
    assert out["final_output"], "无结果时不应返回空字符串（必须有可读提示）"


# ── Agent 调用指标接线（record_agent_call 曾是死代码）──

def test_worker_call_is_counted(monkeypatch):
    """Worker 跑完必须记一次 agent call —— 否则 harness_agent_calls_total 恒为 0。"""
    from agent_harness.core import metrics
    from agent_harness.core.agents import workers

    monkeypatch.setattr(workers, "_call_llm", lambda *a, **kw: "总结内容")

    def _before():
        try:
            return metrics.AGENT_CALLS_TOTAL.labels("search")._value.get()
        except Exception:
            return 0.0

    b = _before()
    workers._worker_synthesize(_state_with_result(EVIDENCE))
    assert _before() == b + 1, "worker 调用没进指标（record_agent_call 是死代码？）"
