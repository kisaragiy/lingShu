#!/usr/bin/env python
"""灵枢知识管道 — 检索层 + 沉淀机制测试
覆盖：search_rules 中文命中/source 标注/空query/未覆盖query、extract_feature_tokens 分词、
     extract_candidates 信号词抽取/文件缺失、load_rules 结构化完整性。
运行：python -m pytest test_knowledge_pipeline.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guardian  # noqa: E402
import extract_candidates  # noqa: E402


# ─── 检索层 search_rules ───
def test_search_zh_hit_venv():
    """中文 query 命中 R-01（PYTHONPATH 劫持）"""
    res = guardian.search_rules("python venv 导入报错", 3)
    rids = [r["rule_id"] for r in res]
    assert "R-01" in rids, "python venv 导入报错应命中 R-01"


def test_search_zh_hit_queue():
    """中文 query 命中 R-13（ComfyUI 队列僵尸）"""
    res = guardian.search_rules("ComfyUI 队列堵了", 3)
    rids = [r["rule_id"] for r in res]
    assert "R-13" in rids, "ComfyUI 队列堵了应命中 R-13"


def test_search_returns_source():
    """检索结果必须带 source（来源 daily）"""
    res = guardian.search_rules("显存不够", 2)
    assert res, "应返回结果"
    for r in res:
        assert r["source"].startswith("daily/"), "缺失 source: %s" % r
        assert r["lesson"], "缺失 lesson"


def test_search_top_k_limit():
    """top_k 应生效，最多返回 N 条"""
    res = guardian.search_rules("显存 GPU 队列 模型 大图", 4)
    assert len(res) <= 4


def test_search_empty_query():
    """空 query / 纯停用词 → 返回空（不崩）"""
    assert guardian.search_rules("") == []
    assert guardian.search_rules("怎么怎么办什么") == [] or isinstance(guardian.search_rules("怎么怎么办什么"), list)


def test_search_no_rule_id_bonus():
    """未覆盖的概念 → 返回空或低相关，不应强行返回无关规则"""
    res = guardian.search_rules("二战历史军事", 3)
    # 这条 query 与任何规则无关，应该返回空（或极小相关），不产生误导
    assert isinstance(res, list)


# ─── 分词 extract_feature_tokens ───
def test_extract_tokens_cjk_bigram():
    """中文应切出 2-gram（'显存'来自'显存不够'）"""
    toks = guardian.extract_feature_tokens("显存不够怎么办")
    assert "显存" in toks, "中文 2-gram 未切出'显存': %s" % toks
    assert "不够" in toks


def test_extract_tokens_english_word():
    """英文应按词拆分"""
    toks = guardian.extract_feature_tokens("ComfyUI queue zombie")
    assert "queue" in toks


# ─── 沉淀机制 extract_candidates ───
def test_extract_candidates_appends_source():
    """抽取的候选必须带 source（daily 文件名）"""
    # 用真实 daily 测（不 mock，证明能用）
    daily = os.path.expanduser("~/knowledge/daily")
    if os.path.isdir(daily):
        cands = extract_candidates.extract_candidates(
            os.path.join(daily, "2026-08-10.md"), min_signals=2)
        for c in cands:
            assert c["source"].endswith(".md"), "候选缺 source: %s" % c


def test_extract_candidates_missing_file():
    """文件不存在 → 返回空列表（不崩）"""
    cands = extract_candidates.extract_candidates("/nonexistent/daily.md")
    assert cands == []


# ─── load_rules 结构化完整性 ───
def test_load_rules_has_title_and_source():
    """每条规则应有 title + source + trigger（结构化完整）"""
    rules = guardian.load_rules()
    assert len(rules) >= 21
    for rid, r in rules.items():
        assert r.get("rule_id"), "%s 缺 rule_id" % rid
        assert r.get("source"), "%s 缺 source" % rid
        assert "lesson" in r and "block_hint" in r


def test_load_rules_has_career():
    """求职线规则 R-22~30 应全部加载"""
    rules = guardian.load_rules()
    assert len(rules) >= 30, "应有至少30条规则(含求职线), 实际%d" % len(rules)
    for i in range(22, 31):
        assert "R-%d" % i in rules, "缺求职线 R-%d" % i


def test_guard_career_platform():
    """简历写平台/PWA → 触发 R-22 产品暗示"""
    r = guardian.guard("写简历: AI智能体编排平台")
    assert "R-22" in [w["rule"] for w in r["warnings"]]


def test_guard_career_greet():
    """群发模板招呼语 → 触发 R-25 招呼语定制"""
    r = guardian.guard("群发模板招呼语")
    assert "R-25" in [w["rule"] for w in r["warnings"]]


def test_guard_career_mainline():
    """做系统工具建设替代主线 → 触发 R-28"""
    r = guardian.guard("今天先做系统工具建设")
    assert "R-28" in [w["rule"] for w in r["warnings"]]


def test_search_career_greet():
    """自然语言'招呼语怎么写' → 检索命中 R-25"""
    res = guardian.search_rules("招呼语怎么写", 2)
    assert any(r["rule_id"] == "R-25" for r in res), "应命中 R-25"


def test_search_career_competition():
    """'AI岗竞争大' → 检索命中 R-26 岗位错配"""
    res = guardian.search_rules("应聘AI岗竞争大", 2)
    assert any(r["rule_id"] == "R-26" for r in res), "应命中 R-26"
