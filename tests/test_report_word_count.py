"""报告字数统计口径（ENG-31）。

旧实现 `word_count = len(content.replace("\\n", ""))` 把 markdown 标记、标点、
表格分隔符全算进字数，中文报告页脚明显虚高。新口径：
    中文每字计 1；英文/数字连续串每串计 1 词；markdown 语法符号、纯标点、
    空白、URL 均不计。
"""
import re

from agent_harness.apps.research.pipeline.report_formatter import (
    _count_words,
    generate_report_html,
)


def test_pure_chinese_counts_characters_without_punctuation():
    """① 纯中文段落：字数 == 汉字数（标点/空白不计）。"""
    text = "调研结论表明，市场需求仍在持续增长。"
    expected = len(re.findall(r"[\u4e00-\u9fff]", text))
    assert expected == 16
    assert _count_words(text) == expected


def test_markdown_syntax_is_not_counted():
    """② markdown 符号（## ** |---|）不计入字数。"""
    text = (
        "## 核心结论\n"
        "\n"
        "**市场**在扩张。\n"
        "|---|---|\n"
        "| 指标 | 值 |\n"
    )
    # 核心结论(4) + 市场(2) + 在扩张(3) + 指标(2) + 值(1)
    assert _count_words(text) == 12

    # 旧口径（去掉换行后的字符数）会把这些符号一起算进去 —— 必然更大
    assert _count_words(text) < len(text.replace("\n", ""))


def test_mixed_chinese_english_counts_english_per_word():
    """③ 中英混排：英文/数字连续串按词计，不按字母计。"""
    text = "我们使用 OpenAI API 做调研"
    # 我们使用(4) + 做调研(3) + OpenAI(1 词) + API(1 词)
    assert _count_words(text) == 4 + 3 + 2


def test_url_and_link_targets_are_not_counted():
    """链接只算锚文本，URL 本身不算字数。"""
    text = "参见[官方文档](https://example.com/a/b?c=1)说明 https://example.com/x"
    # 参见(2) + 官方文档(4) + 说明(2)
    assert _count_words(text) == 8


def test_report_footer_uses_word_count_and_read_time():
    """页脚展示的字数 == _count_words(content)，阅读时长仍按 300 字/分钟。"""
    content = "人工智能技术正在改变调研行业"
    n = _count_words(content)
    assert n == 14

    html = generate_report_html("测试报告", content, sources=[], tags=[])
    assert f"📄 {n} 字 · 约 {max(1, n // 300)} 分钟阅读" in html
