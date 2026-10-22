"""ENG-40：eval 评分必须有"事实性"维度，不能只测关键词。

判据可机械复现（无 LLM judge、无网络）：中文单位同口径数字集合 + 来源域名 +
未核验标注存在性。核心用例是 test_wrong_number_now_fails —— 那条在旧评分下是 PASS。
"""
import pytest

from agent_harness.apps.research.eval.scorer import EvalScore, score_task
from agent_harness.apps.research.eval.dataset import EVAL_DATASET


def _task(**kw):
    base = {"id": "t-1", "expected_keywords": ["营收"], "expected_tool_used": "search"}
    base.update(kw)
    return base


def test_no_factual_spec_keeps_full_score_and_is_marked_unasserted():
    """没定义断言的任务给满分，但必须标 asserted=False —— 不假装测了。"""
    s = score_task(_task(), "营收 987 亿", ["search"])
    assert s.factual_score == 20.0
    assert s.details["factual"]["asserted"] is False


def test_must_have_number_hit_full_score():
    task = _task(factual={"must_have_numbers": ["987亿"]})
    s = score_task(task, "全年营收 987 亿元", ["search"])
    assert s.factual_score == 20.0
    assert s.details["factual"]["failed"] == []


def test_must_have_number_missed_deducts():
    task = _task(factual={"must_have_numbers": ["987亿"]})
    s = score_task(task, "全年营收表现良好", ["search"])
    assert s.factual_score == 0.0
    assert "must_have:987亿" in s.details["factual"]["failed"]


def test_wrong_number_now_fails():
    """核心用例：含全部关键词但数字写错 —— 旧评分 PASS，新评分必须 FAIL。

    旧：keyword 40 满分 + tool 30 + latency 15 + cost 15 = 100 PASS
    新：keyword 20 + factual 0 + tool 30 + latency 15 + cost 15 = 80 …总分仍过 60，
    所以真正卡住的是「该任务权重下的实际结论」——此处断言 factual 归零且 details 报错项。
    """
    task = _task(
        expected_keywords=["营收", "987"],
        factual={"must_have_numbers": ["987亿"], "must_not_have_numbers": ["1.2万亿"]},
    )
    s = score_task(task, "公司营收 1.2 万亿元，同比增长 12%（关键词：营收 987）", ["search"])
    assert s.factual_score == 0.0, "编造的数字没有扣分"
    failed = s.details["factual"]["failed"]
    assert "must_have:987亿" in failed
    assert "must_not_have:1.2万亿" in failed


def test_chinese_unit_normalized_same_scope():
    """1.2万亿 与 1200000000000 是同一口径 —— 单位写法不同不该误判。"""
    task = _task(factual={"must_have_numbers": ["1.2万亿"]})
    s = score_task(task, "总市值 1200000000000 元", ["search"])
    assert s.factual_score == 20.0, s.details["factual"]


def test_must_cite_domain():
    task = _task(factual={"must_cite_domains": ["stats.gov.cn"]})
    assert score_task(task, "数据来自 https://stats.gov.cn/a", ["search"]).factual_score == 20.0
    assert score_task(task, "数据显示增长", ["search"]).factual_score == 0.0


def test_requires_fact_check_note():
    """无来源裸答必须自标未核验 —— 否则视为静默通过（ENG-33）。"""
    task = _task(factual={"requires_fact_check_note": True})
    s_bad = score_task(task, "营收 987 亿，确定无疑。", ["search"])
    assert s_bad.factual_score == 0.0
    s_ok = score_task(task, "营收 987 亿。\n\n⚠️ 未核验：无可用来源正文", ["search"])
    assert s_ok.factual_score == 20.0


def test_partial_checks_proportional():
    task = _task(factual={"must_have_numbers": ["987亿"], "must_cite_domains": ["stats.gov.cn"]})
    s = score_task(task, "营收 987 亿元（来源未知）", ["search"])
    assert s.factual_score == 10.0
    assert s.details["factual"]["passed_count"] == 1


def test_total_score_capped_at_100():
    task = {
        "id": "t-full",
        "expected_keywords": ["456831"],
        "expected_tool_used": "search",
        "factual": {"must_have_numbers": ["456831"]},
    }
    s = score_task(task, "456831", ["search"], latency_s=1, total_tokens=10)
    assert s.total_score == pytest.approx(100.0)
    assert s.passed is True


def test_insignificant_number_assertion_is_skipped_not_silently_failed():
    """断言写了 0-9 的单位数：数字提取器不放它出来 → 显式跳过并记录，不静默判 fail。"""
    task = _task(factual={"must_have_numbers": ["3"]})
    s = score_task(task, "增长了 3 倍", ["search"])
    d = s.details["factual"]
    assert d["asserted"] is False
    assert d["skipped_insignificant"] == ["must_have:3"]
    assert s.factual_score == 20.0


def test_dataset_eval002_has_factual_assertions():
    """eval-002 是确定性算术 —— 必须带上数字断言，否则评测仍测不出算错。"""
    task = next(t for t in EVAL_DATASET if t["id"] == "eval-002")
    assert task["factual"]["must_have_numbers"] == ["456831"]
    s = score_task(task, "结果是 456831", ["code_execute"])
    assert s.factual_score == 20.0
    s_wrong = score_task(task, "结果是 456832", ["code_execute"])
    assert s_wrong.factual_score < 20.0


def test_summary_shows_factual():
    s = score_task(_task(), "营收 987 亿", ["search"])
    assert isinstance(s, EvalScore)
    assert "factual:" in s.summary()
