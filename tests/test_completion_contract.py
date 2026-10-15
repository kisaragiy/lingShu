"""P1-6 显式完成契约 — feature 列表 + 每项 passes 布尔 + finalize 未完成项列表。

痛点：agent 倾向「声称完成」，半成品被当完成交付。本文件验证收尾产物带一个
**显式完成契约**：
- feature 列表 + 每项 `passes` 布尔（保守：未经验证的 feature 一律 false）
- `supervisor_finalize` 输出必带「未完成项」显式列表（无未完成则明确声明「无」）
- 不可行（infeasible）时所有 feature 强制 incomplete
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core.agents import supervisor
from agent_harness.core.graph import graph_multi as gm


def _state(worker_results: dict, **extra) -> dict:
    base = {
        "request": "做一件事",
        "workers_assigned": list(worker_results.keys()),
        "worker_tasks": {k: f"{k} 子任务" for k in worker_results},
        "worker_results": worker_results,
        "worker_errors": {},
        "round": 1,
        "trace_steps": [],
    }
    base.update(extra)
    return base


def _ok(out: str = "完成内容足够长") -> dict:
    return {"success": True, "output": out, "errors": [], "elapsed_s": 1.0}


def _fail(out: str = "", errors=None) -> dict:
    return {"success": False, "output": out, "errors": errors or ["boom"], "elapsed_s": 1.0}


# ═══════════ build_completion_contract ═══════════

def test_verified_worker_gets_passes_true():
    c = gm.build_completion_contract(_state({"search": _ok()}))
    assert c["features"][0]["passes"] is True
    assert c["complete"] is True
    assert c["incomplete"] == []


def test_failed_worker_passes_false():
    c = gm.build_completion_contract(_state({"search": _fail()}))
    assert c["features"][0]["passes"] is False
    assert c["complete"] is False
    assert len(c["incomplete"]) == 1


def test_empty_output_passes_false():
    """半成品（输出为空）不得置 passes=true。"""
    c = gm.build_completion_contract(_state({"search": _ok(out="")}))
    assert c["features"][0]["passes"] is False


def test_worker_with_errors_passes_false():
    c = gm.build_completion_contract(
        _state({"analyze": {"success": True, "output": "内容够长",
                            "errors": ["部分步骤失败"], "elapsed_s": 1.0}})
    )
    assert c["features"][0]["passes"] is False


def test_infeasible_forces_all_incomplete():
    c = gm.build_completion_contract(_state({"search": _ok()}, feasibility="infeasible"))
    assert c["complete"] is False
    assert c["features"][0]["passes"] is False


def test_explicit_features_respected():
    state = _state({"search": _ok()})
    state["features"] = [
        {"feature": "搜集资料", "worker": "search"},
        {"feature": "写出结论", "worker": "analyze"},
    ]
    c = gm.build_completion_contract(state)
    by = {f["feature"]: f["passes"] for f in c["features"]}
    assert by["搜集资料"] is True
    assert by["写出结论"] is False   # 没有 analyze 结果 → 未验证
    assert c["complete"] is False


# ═══════════ render_completion_contract ═══════════

def test_render_lists_incomplete():
    c = gm.build_completion_contract(_state({"search": _ok(), "analyze": _fail()}))
    text = gm.render_completion_contract(c)
    assert "未完成项" in text
    assert "analyze" in text


def test_render_declares_none_when_all_pass():
    c = gm.build_completion_contract(_state({"search": _ok()}))
    text = gm.render_completion_contract(c)
    assert "未完成项" in text
    assert "无" in text


# ═══════════ supervisor_finalize 集成 ═══════════

def test_finalize_attaches_contract_and_incomplete_list(monkeypatch):
    monkeypatch.setattr(supervisor, "_call_llm_full",
                        lambda *a, **k: ("正常报告内容，足够长。", "stop"))
    monkeypatch.setattr(supervisor, "_call_llm",
                        lambda *a, **k: "正常报告内容，足够长。")
    state = _state({"search": _ok(), "analyze": _fail()})
    out = gm.supervisor_finalize(state)
    assert "完成契约" in out["final_output"]
    assert "未完成项" in out["final_output"]
    assert out["completion_contract"]["complete"] is False
    assert any(s.get("step") == "finalize_completion_contract"
               for s in out["trace_steps"])


def test_finalize_abstention_has_contract_no_forbidden_markers():
    """不可行路径：带完成契约（全未完成），且不含编造报告结构。"""
    state = _state({"search": _ok(), "analyze": _ok()},
                   feasibility="infeasible", feasibility_reason="数据源不可达")
    out = gm.supervisor_finalize(state)
    final = out["final_output"]
    assert "无法完成" in final
    assert "完成契约" in final
    for marker in ("执行摘要", "主要发现", "参考来源", "📋"):
        assert marker not in final, f"放弃路径不应含报告结构 {marker!r}"
    assert out["trace_steps"][0]["step"] == "finalize_abstention"
    assert out["completion_contract"]["complete"] is False


def test_finalize_empty_results_has_contract(monkeypatch):
    """无 worker 结果：仍带显式完成契约，且不调 LLM。"""
    calls = {"n": 0}
    monkeypatch.setattr(supervisor, "_call_llm_full",
                        lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), "x")[1])
    monkeypatch.setattr(supervisor, "_call_llm",
                        lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), "x")[1])
    state = {"request": "", "worker_results": {}, "worker_errors": {},
             "round": 1, "trace_steps": []}
    out = gm.supervisor_finalize(state)
    assert calls["n"] == 0
    assert "完成契约" in out["final_output"]
    assert out["completion_contract"]["features"] == []
