"""P0-2 反编造校验 — supervisor_finalize 生成报告后核对报告中的数字是否有据可依。

覆盖：
- verify_report_facts：报告数字能在 state 证据里找到 → 通过；找不到 → 列入 unverified
- 保守判据：round 触顶 / 无数字 / 无证据 / 解析失败 → 一律跳过，不误判
- 引用标记 [来源 N]/[N] 与 URL 中的数字不算事实断言
- supervisor_finalize 集成：编造数字 → 报告被标「未核实」，且**不追加报告 LLM 调用**（降级）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from agent_harness.core.graph import graph_multi
from agent_harness.core.agents import supervisor
from agent_harness.core.config import SUPERVISOR_MAX_ROUNDS


def _state(report_evidence="", round_=1, worker_out=None):
    out = worker_out if worker_out is not None else report_evidence
    return {
        "request": "调研 X",
        "worker_results": {
            "search": {"success": True, "output": out, "data": None, "errors": [],
                       "elapsed_s": 1.0},
        },
        "worker_errors": {},
        "round": round_,
        "session_id": "test-sess",
        "trace_steps": [],
    }


# ═══════════ verify_report_facts 单元 ═══════════

def test_fabricated_number_flagged():
    report = "该公司 2023 年营收为 1234 亿元。"
    state = _state("公开财报显示营收约 987 亿元")
    out = graph_multi.verify_report_facts(report, state)
    assert out["checked"] is True
    assert 1234.0 in out["unverified"]
    assert 987.0 not in out["unverified"]


def test_supported_number_passes():
    report = "营收 987 亿元，同比增长 12%。"
    state = _state("营收 987 亿元，增长率 12%")
    out = graph_multi.verify_report_facts(report, state)
    assert out["checked"] is True
    assert out["unverified"] == []


def test_comma_formatted_number_matches():
    report = "用户数达到 1,234 万。"
    state = _state("用户数 1234 万")
    out = graph_multi.verify_report_facts(report, state)
    assert out["unverified"] == []


def test_no_numbers_skips():
    out = graph_multi.verify_report_facts("只有文字，没有任何数字。", _state("证据"))
    assert out["skipped"] is True
    assert out["checked"] is False


def test_empty_evidence_skips():
    """无证据可核对 → 保守跳过，绝不把整篇判为编造。"""
    out = graph_multi.verify_report_facts("营收 1234 亿元。", _state(""))
    assert out["skipped"] is True
    assert out["checked"] is False


def test_round_cap_skips():
    """round 触顶（强制收尾）不算编造 → 跳过校验。"""
    out = graph_multi.verify_report_facts(
        "营收 1234 亿元。", _state("别的证据", round_=SUPERVISOR_MAX_ROUNDS)
    )
    assert out["skipped"] is True
    assert out["unverified"] == []


def test_citation_and_url_numbers_ignored():
    """[来源 12] / [15] / URL 里的数字不是事实断言，不参与核对。"""
    report = "结论见 [来源 12] 与 [15]，原文 https://example.com/2023/12345678 有说明。"
    state = _state("毫无数字的证据文本")
    out = graph_multi.verify_report_facts(report, state)
    # 报告里没有「可核对」的数字 → 跳过
    assert out["skipped"] is True


def test_single_digits_not_flagged():
    """0-9 的单位数在正文太常见 → 不计入核对（保守）。"""
    report = "主要有 3 个方面，共 5 点。"
    state = _state("证据里没有这些数字")
    out = graph_multi.verify_report_facts(report, state)
    assert out["skipped"] is True


def test_parse_failure_never_misfires(monkeypatch):
    """内部异常 → 保守跳过，不误判。"""
    def boom(*a, **k):
        raise RuntimeError("解析炸了")
    monkeypatch.setattr(graph_multi, "_extract_checkable_numbers", boom)
    out = graph_multi.verify_report_facts("营收 1234 亿", _state("证据"))
    assert out["skipped"] is True
    assert out["checked"] is False


# ═══════════ supervisor_finalize 集成 ═══════════

def test_finalize_marks_fabricated_numbers(monkeypatch):
    """报告含无据数字 → final_output 带「未核实」标记。"""
    llm_calls = {"n": 0}

    def fake_full(*a, **k):
        llm_calls["n"] += 1
        return ("## 📋 执行摘要\n公司 2023 年营收 4321 亿元，市占率 88%。", "stop")

    monkeypatch.setattr(supervisor, "_call_llm_full", fake_full)
    monkeypatch.setattr(supervisor, "_call_llm", fake_full)

    state = _state("搜索结果：2023 年度行业观察，未披露具体营收数字")
    out = graph_multi.supervisor_finalize(state)
    assert "未核实" in out["final_output"]
    assert "4321" in out["final_output"] or "4321.0" in out["final_output"]
    # 降级路径不再调报告 LLM
    assert llm_calls["n"] == 1, "反编造校验不应追加 LLM 调用"
    # trace 记录了事实校验
    assert any(s.get("step") == "finalize_fact_check" for s in out["trace_steps"])


def test_finalize_keeps_verified_report_intact(monkeypatch):
    """所有数字都有据 → 报告不被标记，内容保持原样。"""
    def fake_full(*a, **k):
        return ("## 📋 执行摘要\n营收 987 亿元，增长 12%。", "stop")

    monkeypatch.setattr(supervisor, "_call_llm_full", fake_full)
    monkeypatch.setattr(supervisor, "_call_llm", fake_full)

    state = _state("营收 987 亿元，增长率 12%")
    out = graph_multi.supervisor_finalize(state)
    assert "未核实" not in out["final_output"]
    assert "987" in out["final_output"]
