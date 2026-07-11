"""Tests for _format_citations in report_formatter.py — pure function tests."""

import pytest

from agent_harness.pipeline.report_formatter import (
    _format_citations,
    _is_valid_url,
    _extract_sources_from_text,
    _link_sources,
)


class TestFormatCitations:
    """Test [url] → [N] conversion logic."""

    def test_basic_url_conversion(self):
        """[https://example.com] should become [1] with source_map."""
        text = "See [https://example.com] for details."
        result, source_map = _format_citations(text)
        assert result == "See [1] for details."
        assert source_map == {1: "https://example.com"}

    def test_multiple_urls(self):
        """Multiple distinct URLs get sequential numbers."""
        text = "A [https://a.com] and B [https://b.com]"
        result, source_map = _format_citations(text)
        assert result == "A [1] and B [2]"
        assert source_map[1] == "https://a.com"
        assert source_map[2] == "https://b.com"

    def test_deduplication(self):
        """Same URL appearing twice gets the same [N] number."""
        text = "First [https://example.com] and again [https://example.com]"
        result, source_map = _format_citations(text)
        assert result == "First [1] and again [1]"
        assert source_map == {1: "https://example.com"}

    def test_no_urls_passes_through(self):
        """Text without [url] patterns is unchanged with empty source_map."""
        text = "Regular text with [some marker] but not a URL."
        result, source_map = _format_citations(text)
        assert result == text
        assert source_map == {}

    def test_trailing_punctuation_stripped(self):
        """Trailing punctuation after URL in brackets is stripped."""
        text = "Link [https://example.com]. More text."
        result, source_map = _format_citations(text)
        assert result == "Link [1]. More text."
        assert source_map == {1: "https://example.com"}

    def test_source_map_ordering(self):
        """source_map preserves insertion order (1-indexed)."""
        text = "Last [https://z.com] then first [https://a.com]"
        result, source_map = _format_citations(text)
        keys = list(source_map.keys())
        assert keys == [1, 2]  # Order of appearance, not alphabetical
        assert source_map[1] == "https://z.com"
        assert source_map[2] == "https://a.com"

    def test_search_tool_format(self):
        """Text in the format produced by the search tool."""
        text = "DeepSeek releases new model: enhanced reasoning [https://deepseek.com/blog]"
        result, source_map = _format_citations(text)
        assert "[1]" in result
        assert "[https://" not in result  # URL should be replaced
        assert source_map[1] == "https://deepseek.com/blog"

    def test_mixed_url_and_source_markers(self):
        """[url] and [来源 N] markers coexist without interference."""
        text = "Discovery [https://site1.com] according to [来源 2] and [https://site2.com]"
        result, source_map = _format_citations(text)
        assert "[1]" in result
        assert "[来源 2]" in result  # [来源 N] should be left intact
        assert "[2]" in result
        assert 1 in source_map
        assert 2 in source_map


class TestIsValidUrl:
    def test_valid_http(self):
        assert _is_valid_url("http://example.com") is True

    def test_valid_https(self):
        assert _is_valid_url("https://example.com/page") is True

    def test_too_short(self):
        assert _is_valid_url("http://a") is False  # len <= 15


class TestExtractSources:
    def test_extract_urls_from_text(self):
        text = "Check https://example.com and https://test.org/page for info."
        sources = _extract_sources_from_text(text)
        urls = [s["url"] for s in sources]
        assert "https://example.com" in urls
        assert "https://test.org/page" in urls

    def test_no_urls(self):
        assert _extract_sources_from_text("Just plain text.") == []


class TestLinkSources:
    def test_link_converts_source_markers(self):
        text = "See [来源 1] for reference."
        result = _link_sources(text, [{"url": "https://x.com"}])
        assert "href=\"#source-1\"" in result
        assert "[来源 1]" not in result  # transformed

    def test_no_sources_returns_unchanged(self):
        text = "Some content."
        assert _link_sources(text, []) == text
