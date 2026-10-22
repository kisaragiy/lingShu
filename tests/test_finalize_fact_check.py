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


# ═══════════ ENG-29: provenance（引用溯源） ═══════════

def _prov_state(steps, round_=1, worker_out="模型自述的结论"):
    """worker_results[wn]["data"] 的形状对齐 workers.py 的 step 列表。"""
    return {
        "request": "调研某公司营收",
        "round": round_,
        "worker_results": {
            "search": {"success": True, "output": worker_out,
                       "data": steps, "errors": [], "elapsed_s": 1.0},
        },
        "worker_errors": {},
    }


def _search_step(lines, tool="search", fetched_at="2026-10-22T14:30:05+0800", success=True):
    return {"step": {"tool": tool},
            "result": {"success": success, "error": None, "data": lines},
            "fetched_at": fetched_at}


def test_collect_provenance_from_search_line():
    state = _prov_state([_search_step(
        ["公司年报2023: 全年营收 987 亿元 [https://example.com/ar2023]"])])
    prov = graph_multi._collect_provenance(state)
    assert len(prov) == 1
    p = prov[0]
    assert p["url"] == "https://example.com/ar2023"
    assert p["tool"] == "search"
    assert "987" in p["snippet"]
    # fetched_at 是 ISO8601 带时区（workers.py 写入格式）
    assert p["fetched_at"].startswith("2026-10-22T14:30:05")
    assert p["source_worker"] == "search"


def test_collect_provenance_empty_snippet_still_recorded():
    """DuckDuckGo 兜底行「标题: [url]」——空 snippet 也要记下来源（只是没法核内容）。"""
    prov = graph_multi._collect_provenance(
        _prov_state([_search_step(["某站首页: [https://example.com/home]"])]))
    assert len(prov) == 1
    assert prov[0]["snippet"] == ""
    assert prov[0]["url"] == "https://example.com/home"


def test_collect_provenance_web_scrape_json():
    import json as _json
    body = _json.dumps({"title": "年报", "body": "全年营收 987 亿元",
                        "url": "https://example.com/scrape"}, ensure_ascii=False)
    prov = graph_multi._collect_provenance(
        _prov_state([_search_step(body, tool="web_scrape")]))
    assert prov[0]["tool"] == "web_scrape"
    assert prov[0]["url"] == "https://example.com/scrape"
    assert "987" in prov[0]["snippet"]


def test_collect_provenance_web_scrape_truncated_json():
    """正文被 truncate_output 截成半截 JSON → 仍能用字段正则捞出 url/正文。"""
    body = ('{"title": "年报", "url": "https://example.com/scrape", '
            '"body": "全年营收 987 亿元，同比')
    prov = graph_multi._collect_provenance(
        _prov_state([_search_step(body, tool="web_scrape")]))
    assert len(prov) == 1
    assert prov[0]["url"] == "https://example.com/scrape"
    assert "987" in prov[0]["snippet"]


def test_collect_provenance_skips_failed_step():
    """失败步骤不算来源（不能拿失败/降级结果当证据）。"""
    prov = graph_multi._collect_provenance(_prov_state([
        _search_step(["标题: 摘要 [https://example.com/x]"], success=False),
    ]))
    assert prov == []


# ═══════════ ENG-34: 中文单位归一 ═══════════

def test_cn_unit_same_as_plain_number():
    assert graph_multi._normalize_number_tokens("市值 6489万") == \
           graph_multi._normalize_number_tokens("市值 64890000")
    assert graph_multi._normalize_number_tokens("市值 6489万") == {64890000.0}


def test_wanyi_composite_unit():
    # 复合单位「万亿」必须先匹配，否则 1.2万亿 会被吃成 1.2万
    assert graph_multi._normalize_number_tokens("1.2万亿") == {1.2e12}
    assert graph_multi._normalize_number_tokens("营收 987 亿元") == {987e8}


def test_report_unit_snippet_plain_no_false_flag():
    """报告写「987 亿」、snippet 写「98700000000」→ 同集合，不误报。"""
    report_nums = graph_multi._normalize_number_tokens("营收 987 亿元")
    snippet_nums = graph_multi._normalize_number_tokens("营收 98700000000 元")
    assert report_nums == snippet_nums == {987e8}


def test_cite_and_url_numbers_still_stripped():
    assert graph_multi._normalize_number_tokens(
        "结论见 [来源 12]，原文 https://example.com/2023/12345678 有说明") == set()


# ═══════════ ENG-32: v2 —— 报告数字 must 在一手来源里核对得上 ═══════════

def test_wrong_number_flagged_not_found_via_provenance():
    """故意喂错数字：报告写 1.2 万亿，来源正文只写 987 亿 → 必须标「未找到依据」。"""
    state = _prov_state(
        [_search_step(["公司年报2023: 全年营收 987 亿元 [https://example.com/ar2023]"])],
        worker_out="模型自述：该公司营收 1.2 万亿（故意写错的源头）",
    )
    report = "结论：该公司 2023 年营收达 1.2 万亿元。[https://example.com/ar2023]"
    out = graph_multi.verify_report_facts_v2(report, state)
    assert out["checked"] is True
    assert out["provenance_count"] == 1
    not_found_nums = [x["num"] for x in out["not_found"]]
    assert 1.2e12 in not_found_nums          # 错数字被抓
    assert 987e8 not in not_found_nums       # 来源里真有的数字不误报
    # 渲染后报告正文必须显式出现「未找到依据」（不是抛异常、不是静默通过）
    rendered, info = graph_multi._apply_fact_check_v2(report, state)
    assert "未找到依据" in rendered
    assert "1200000000000" in rendered
    assert info["checked"] is True


def test_correct_number_not_flagged_via_provenance():
    """反向用例：数字与来源一致 → 不标注。"""
    state = _prov_state(
        [_search_step(["公司年报2023: 全年营收 987 亿元 [https://example.com/ar2023]"])])
    report = "结论：该公司 2023 年营收达 987 亿元。[https://example.com/ar2023]"
    out = graph_multi.verify_report_facts_v2(report, state)
    assert out["checked"] is True
    assert out["not_found"] == []
    rendered, _ = graph_multi._apply_fact_check_v2(report, state)
    assert "未找到依据" not in rendered
    assert "未核验" not in rendered


def test_v2_ignores_worker_self_report():
    """源头错：worker 自述里有 1.2 万亿，但一手来源没有 → 仍判未找到依据。"""
    state = _prov_state(
        [_search_step(["公司年报2023: 全年营收 987 亿元 [https://example.com/ar2023]"])],
        worker_out="该公司营收 1.2 万亿元",
    )
    out = graph_multi.verify_report_facts_v2("营收 1.2 万亿元。", state)
    assert [x["num"] for x in out["not_found"]] == [1.2e12]


def test_v2_no_provenance_skips_with_reason():
    out = graph_multi.verify_report_facts_v2("营收 1234 亿元。", _state("自述证据 1234 亿"))
    assert out["skipped"] is True
    assert out["checked"] is False
    assert out["reason"] == "无证据可核对"


def test_v2_round_cap_skips():
    state = _prov_state(
        [_search_step(["年报: 营收 987 亿 [https://example.com/a]"])],
        round_=SUPERVISOR_MAX_ROUNDS)
    out = graph_multi.verify_report_facts_v2("营收 1234 亿元。", state)
    assert out["skipped"] is True
    assert "round 触顶" in out["reason"]


# ═══════════ ENG-33: skipped 四态必须显式标注 ═══════════

def test_skip_reason_cn_maps_all_four():
    assert "最大轮次" in graph_multi._skip_reason_cn("round 触顶，跳过事实校验")
    assert "无可核对数字" in graph_multi._skip_reason_cn("报告无可核对数字")
    assert "provenance 为空" in graph_multi._skip_reason_cn("无证据可核对")
    assert "校验流程异常" in graph_multi._skip_reason_cn("校验异常，已跳过: boom")


def test_skipped_report_carries_explicit_reason():
    """无数字 → 报告末尾显式写「未经数字核验（原因：…）」，不静默通过。"""
    report, info = graph_multi._apply_fact_check("只有文字，没有任何数字。", _state("证据"))
    assert info["skipped"] is True
    assert "未核验" in report
    assert "未经数字核验（原因：" in report
    assert "无可核对数字" in report


def test_skipped_round_cap_report_carries_max_rounds():
    report, info = graph_multi._apply_fact_check(
        "营收 1234 亿元。", _state("别的证据", round_=SUPERVISOR_MAX_ROUNDS))
    assert info["skipped"] is True
    assert "未经数字核验（原因：" in report
    assert "最大轮次" in report


def test_v2_skipped_when_no_provenance_marks_reason():
    report, info = graph_multi._apply_fact_check_v2("营收 1234 亿元。", _state("自述证据"))
    assert info["skipped"] is True
    assert "未经数字核验（原因：" in report
    assert "未检索到可用来源正文" in report


# ═══════════ ENG-29(c/d): HTML 三态徽章与汇总块 ═══════════

def _html(*a, **k):
    from agent_harness.apps.research.pipeline.report_formatter import generate_report_html
    return generate_report_html(*a, **k)


def test_report_html_factcheck_summary_three_states():
    html = _html("t", "正文", provenance=[
        {"url": "https://example.com/a", "tool": "search",
         "fetched_at": "2026-10-22T14:30:05+0800", "title": "年报",
         "snippet": "全年营收 987 亿元"}], factcheck={
            "verified": 2, "not_found": 1, "unknown": 1,
            "unknown_reasons": ["未检索到可用来源正文"], "provenance_count": 3})
    assert '<div class="section factcheck-summary">' in html
    for cls in ("v-ok", "v-miss", "v-unknown"):
        assert cls in html
    assert "未找到依据" in html


def test_report_html_provenance_wrong_number_shows_miss():
    """HTML 侧同样显式标「未找到依据」——报告 1.2 万亿 vs 来源 987 亿。"""
    content = "结论：营收达 1.2 万亿元。[https://example.com/ar2023]"
    html = _html("t", content, provenance=[
        {"url": "https://example.com/ar2023", "tool": "search",
         "fetched_at": "2026-10-22T14:30:05+0800", "title": "年报",
         "snippet": "全年营收 987 亿元"}])
    assert '<div class="section factcheck-summary">' in html
    assert "未找到依据" in html
    assert "v-miss" in html


def test_report_html_no_provenance_marks_sources_unverified():
    html = _html("t", "正文见 https://example.com/x", sources=[
        {"url": "https://example.com/x", "title": "x"}])
    assert "未独立核验" in html
    assert '<div class="section factcheck-summary">' not in html


def test_report_html_backward_compatible():
    """不传 factcheck/provenance → 旧行为（无汇总块）。"""
    html = _html("t", "正文 123 亿元")
    assert '<div class="section factcheck-summary">' not in html
    assert "🔍 事实核验说明" not in html


def test_year_tokens_treated_as_time_not_data():
    """年份是时间限定，不是待核验数据：来源是 2023 年报，报告写 2024 年不该报「未找到依据」。"""
    state = _prov_state([
        _search_step(["公司年报: 全年营收 987 亿元 [https://example.com/ar2023]"])
    ])
    report = "2024 年，公司营收 987 亿元。"
    out = graph_multi.verify_report_facts_v2(report, state)
    assert out["checked"] is True
    assert [x["num"] for x in out["not_found"]] == [], out["not_found"]


def test_year_excluded_but_real_number_still_caught():
    """排除年份不能把真数字一起放走（负向控制）。"""
    state = _prov_state([
        _search_step(["公司年报: 全年营收 987 亿元 [https://example.com/ar2023]"])
    ])
    report = "2024 年，公司营收 1.2 万亿元。"
    nums = [x["num"] for x in graph_multi.verify_report_facts_v2(report, state)["not_found"]]
    assert nums == [1.2e12], nums          # 只抓错数字，不抓 2024


def test_provenance_parses_real_search_tool_format():
    """对齐 web.py 真实输出格式 `标题: 摘要 [url]` —— 格式漂移会让 v2 静默退化成 skip。"""
    state = _prov_state([
        _search_step(["某公司 2023 年报: 全年营收 987 亿元，同比增 12% [https://example.com/ar2023]"])
    ])
    prov = graph_multi._collect_provenance(state)
    assert len(prov) == 1
    p = prov[0]
    assert p["url"] == "https://example.com/ar2023"
    assert p["title"] == "某公司 2023 年报"
    assert "987 亿元" in p["snippet"]
    assert p["fetched_at"]                   # 抓取时间必填（ENG-29 的 provenance 三要素）
    assert p["tool"] == "search"            # 与本仓库 search 工具注册名一致


def test_provenance_parses_real_web_scrape_json():
    """对齐 web.py web_scrape 的 {"title","body","url"} JSON str。"""
    state = _prov_state([{
        "step": {"tool": "web_scrape"},
        "result": {"success": True, "data": '{"title": "年报", "body": "营收 987 亿元", "url": "https://example.com/ar"}'},
        "fetched_at": "2026-10-22T10:00:00+08:00",
    }])
    prov = graph_multi._collect_provenance(state)
    assert len(prov) == 1 and prov[0]["snippet"] == "营收 987 亿元"
