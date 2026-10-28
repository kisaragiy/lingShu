"""Regressions from the real DRAM report: citation identity and honest review status."""
from datetime import date
from html.parser import HTMLParser

import pytest

from agent_harness.apps.research.pipeline.report_checks import review_warnings
from agent_harness.apps.research.pipeline.report_formatter import generate_report_html
from agent_harness.core.graph import graph_multi as gm


class SourceLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = None
        self.links = {}
        self.anchors = set()
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.add(attrs['id'])
        if tag == 'a' and attrs.get('href', '').startswith('#source-'):
            self.anchors.add(attrs['href'][1:])
        if tag == 'li' and attrs.get('id', '').startswith('source-'):
            self.active = attrs['id']
        if tag == 'a' and self.active and attrs.get('href', '').startswith('http'):
            self.links[self.active] = attrs['href']

    def handle_endtag(self, tag):
        if tag == 'li':
            self.active = None


def test_named_institution_is_not_bound_to_first_search_result():
    html = generate_report_html('Report', '中国市场2300亿元[来源 1]。\n[来源 1] 咨询机构 — 市场规模',
                                sources=[{'url': 'https://example.org/unrelated-hbm', 'title': 'HBM'}])
    assert '[来源 1：未解析]' in html
    assert 'href="#source-1"' not in html
    assert 'data-review-code="unresolved_citations"' in html
    parsed = SourceLinks()
    parsed.feed(html)
    assert 'source-1' not in parsed.links
    assert parsed.links['source-2'] == 'https://example.org/unrelated-hbm'


def test_missing_number_is_reserved_when_inline_urls_are_added():
    html = generate_report_html('Report', 'A[来源 1]，B[来源 2]。\n'
        '[来源 1] Primary - https://primary.example/report\n'
        '[来源 2] Institution without URL\n'
        'Extra [https://additional.example/report]')
    parsed = SourceLinks()
    parsed.feed(html)
    assert parsed.links['source-1'] == 'https://primary.example/report'
    assert 'source-2' not in parsed.links
    assert parsed.links['source-3'] == 'https://additional.example/report'


def test_conflicting_definitions_remain_unresolved():
    html = generate_report_html('Report', 'Claim[来源 1]。\n'
        '[来源 1] A - https://first.example/report\n'
        '[来源 1] B - https://second.example/report')
    assert '[来源 1：未解析]' in html
    assert 'href="#source-1"' not in html


def test_two_declared_numbers_can_refer_to_the_same_url():
    html = generate_report_html('Report', 'A[来源 1] B[来源 2]\n'
        '[来源 1] A - https://primary.example/report\n'
        '[来源 2] B - https://primary.example/report')
    parsed = SourceLinks()
    parsed.feed(html)
    assert parsed.links['source-1'] == parsed.links['source-2']
    assert '未解析]' not in html


@pytest.mark.parametrize('separator', ['\n', '\n\n', '\r\n', '\r\n\r\n'])
def test_missing_url_cannot_consume_the_next_source_definition(separator):
    html = generate_report_html('Report', 'A[来源 1] B[来源 2]\n'
        '[来源 1]' + separator + '[来源 2] B - https://second.example/report')
    parsed = SourceLinks()
    parsed.feed(html)
    assert 'source-1' not in parsed.links
    assert 'source-1' not in parsed.anchors
    assert parsed.links['source-2'] == 'https://second.example/report'
    assert '[来源 1：未解析]' in html


def test_adjacent_source_markers_do_not_borrow_each_others_url():
    html = generate_report_html('Report', 'A[来源 1] B[来源 2]\n'
        '[来源 1] [来源 2] B - https://second.example/report')
    parsed = SourceLinks()
    parsed.feed(html)
    assert not parsed.links
    assert '[来源 1：未解析]' in html and '[来源 2：未解析]' in html


def test_leading_zero_source_number_has_a_working_normalized_anchor():
    html = generate_report_html('Report', 'A[来源 01]\n'
        '[来源 01] A - https://first.example/report')
    parsed = SourceLinks()
    parsed.feed(html)
    assert parsed.links['source-1'] == 'https://first.example/report'
    assert parsed.anchors == {'source-1'}
    assert parsed.anchors <= parsed.ids


@pytest.mark.parametrize('definition', [
    '[来源 1] A - https://first.example/paper_(v2)',
    '[来源 1] [A](https://first.example/paper_(v2))',
    '[来源 1] A - https://first.example/paper_(v2)。',
    'Extra [https://first.example/paper_(v2)]',
])
def test_balanced_parentheses_in_source_urls_are_preserved(definition):
    html = generate_report_html('Report', 'A[来源 1]\n' + definition)
    parsed = SourceLinks()
    parsed.feed(html)
    assert 'https://first.example/paper_(v2)' in parsed.links.values()
    assert 'https://first.example/paper_' not in parsed.links.values()


def test_unbalanced_url_is_unresolved_instead_of_silently_truncated():
    html = generate_report_html('Report', 'A[来源 1]\n'
        '[来源 1] A - https://first.example/paper_(v2')
    parsed = SourceLinks()
    parsed.feed(html)
    assert 'source-1' not in parsed.links
    assert '[来源 1：未解析]' in html


def test_inline_markdown_formats_lists_without_executing_html():
    html = generate_report_html('Report', '- **指标**：`**literal**` <img src=x onerror=alert(1)>\n\n'
        '1. **第一步**\n2. 第二步\n## 下一节\n正文 **重点**')
    assert '<strong>指标</strong>' in html
    assert '<code>**literal**</code>' in html
    assert '<strong>第一步</strong>' in html and '<strong>重点</strong>' in html
    assert '<img src=x' not in html
    assert '&lt;img' in html
    assert '</ol>\n<h2 ' in html


@pytest.mark.parametrize('report,evidence', [
    ('949亿美元', 'USD 94.9 billion'),
    ('1.2万亿美元', 'USD 1.2 trillion'),
    ('1230万美元', 'USD 12.3 million'),
    ('12000美元', 'USD 12 thousand'),
])
def test_equivalent_chinese_english_scales_match(report, evidence):
    assert gm._normalize_number_tokens(report) == gm._normalize_number_tokens(evidence)


def test_english_words_and_transfer_rates_are_not_scale_abbreviations():
    assert gm._normalize_number_tokens('9200 MT/s and 120 kilometers') == {9200, 120}


def test_different_entities_matching_number_never_certifies_semantics(monkeypatch):
    provenance = [{'url': 'https://primary.example/report', 'tool': 'search',
                   'snippet': '金士顿在第三方模组收入中的份额为62%。'}]
    monkeypatch.setattr(gm, '_collect_provenance', lambda state: provenance)
    claim = '中国内存条市场同比增长62%。'
    result = gm.verify_report_facts_v2(claim, {'round': 1})
    assert result['checked'] and not result['not_found']
    assert result['verification_scope'] == 'numeric_presence_only'
    assert result['semantic_verified'] is False
    html = generate_report_html('Report', claim, provenance=provenance, factcheck=result)
    assert '数值匹配 1 项' in html
    assert '内容状态：待人工核查' in html
    assert '已核验' not in html


def test_legacy_claim_counts_and_success_footer_do_not_certify_content():
    html = generate_report_html('Report', '正文\n## ✅ 完成契约\n'
        '- [✅ 通过] 搜集资料\n未完成项：无（全部 feature 均已验证通过）',
        factcheck={'verified': 19, 'not_found': 0})
    assert '已核验' not in html and '全部 feature 均已验证通过' not in html
    assert '内容状态：待人工核查' in html
    assert '[✅ 有产物]' in html


@pytest.mark.parametrize('period', ['2026Q2', '2026年第二季度', '2026年2季度'])
def test_forecast_for_a_past_quarter_is_flagged(period):
    warnings = review_warnings(f'{period}一般型DRAM合约价预计上涨58%—63%。', date(2026, 10, 28))
    assert any(item['code'] == 'past_forecast_period' for item in warnings)


@pytest.mark.parametrize('text', [
    '2027Q2预计增长20%。',
    '2026Q2营收实际增长20%。',
    '当时预计2026Q2增长20%，此为历史预测。',
])
def test_current_facts_future_and_explicit_historical_forecasts_are_distinguished(text):
    assert not any(item['code'] == 'past_forecast_period'
                   for item in review_warnings(text, date(2026, 10, 28)))


def test_multiple_memory_market_scopes_require_review_without_declaring_falsehood():
    warnings = review_warnings('内存条模组市场为949亿美元，HBM占DRAM原厂收入33%。', date(2026, 10, 28))
    assert any(item['code'] == 'mixed_market_scopes' for item in warnings)
    assert not review_warnings('仅介绍HBM技术。', date(2026, 10, 28))


def test_execution_contract_marks_output_not_factual_acceptance():
    text = gm.render_completion_contract({'features': [{'feature': 'Search', 'passes': True}],
                                         'incomplete': []})
    assert '有产物' in text and '尚待核查' in text
    assert '全部 feature 均已验证通过' not in text


def test_reexport_recomputes_legacy_numeric_annotation_without_rewriting_claim():
    content = ('该市场为949亿美元。\n'
               '❌ 未找到依据：以下数字未能在任何一手来源正文里核对上，请谨慎采信 —— 94900000000\n')
    html = generate_report_html('Report', content, provenance=[{
        'url': 'https://primary.example/report', 'snippet': 'Market size: USD94.9 billion.'}])
    assert '该市场为949亿美元。' in html
    assert '数值匹配 1 项' in html
    assert '数值未匹配 0 项' in html
    assert '以下数字未能在任何一手来源正文里核对上' not in html
