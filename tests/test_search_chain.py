"""Search chain unit tests — mock HTTP responses to test DDG parsing strategies."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch, MagicMock

# Clear search cache before each test to avoid cross-test pollution
@pytest.fixture(autouse=True)
def clear_search_cache():
    from src.agent_harness.tools.web import _SEARCH_CACHE
    _SEARCH_CACHE.clear()


class MockResponse:
    def __init__(self, text="", status_code=200):
        self.text = text
        self.status_code = status_code
        self.ok = status_code == 200

    def json(self):
        return {"results": []}


# DDG HTML for strategy 1 (result__a + result__snippet)
DDG_S1 = """<html><body>
<a rel="nofollow" class="result__a" href="https://example.com/1">Result One</a>
<a class="result__snippet">摘要1</a>
</body></html>"""

# DDG HTML for strategy 2 (result-link + snippet-text)
DDG_S2 = """<html><body>
<a class="result-link" href="https://legacy.com/page">Legacy Result</a>
<span class="snippet-text">旧版摘要</span>
</body></html>"""

# DDG HTML — only generic links
DDG_GENERIC = """<html><body>
<a href="https://blog.example.com/post">Blog Post</a>
<a href="https://example.com/article/123">Article</a>
<a href="https://duckduckgo.com/redirect">DDG</a>
</body></html>"""


def _make_side_effect(searxng_resp, ddg_resp):
    """Return a side_effect function for mock _session.get."""
    def side_effect(url, **kwargs):
        url_str = str(url)
        if "127.0.0.1:4000" in url_str:
            return searxng_resp
        return ddg_resp
    return side_effect


class TestSearchChain:

    @patch("src.agent_harness.tools.web._session")
    def test_ddg_strategy1(self, mock_session):
        from src.agent_harness.tools.web import _tool_search
        mock_session.get.side_effect = _make_side_effect(
            MockResponse(status_code=500), MockResponse(text=DDG_S1))
        results = _tool_search("test", max_results=3)
        assert len(results) >= 1
        assert any("Result One" in r for r in results)

    @patch("src.agent_harness.tools.web._session")
    def test_ddg_strategy2(self, mock_session):
        from src.agent_harness.tools.web import _tool_search
        mock_session.get.side_effect = _make_side_effect(
            MockResponse(status_code=500), MockResponse(text=DDG_S2))
        results = _tool_search("test", max_results=3)
        assert len(results) >= 1

    @patch("src.agent_harness.tools.web._session")
    def test_generic_links(self, mock_session):
        from src.agent_harness.tools.web import _tool_search
        mock_session.get.side_effect = _make_side_effect(
            MockResponse(status_code=500), MockResponse(text=DDG_GENERIC))
        results = _tool_search("test", max_results=3)
        assert len(results) >= 1

    @patch("src.agent_harness.tools.web._session")
    def test_all_fail(self, mock_session):
        from src.agent_harness.tools.web import _tool_search
        mock_session.get.return_value = MockResponse(status_code=500)
        results = _tool_search("test", max_results=3)
        assert len(results) >= 1
        assert "搜索失败" in results[0]

    @patch("src.agent_harness.tools.web._session")
    def test_searxng_success_skips_ddg(self, mock_session):
        from src.agent_harness.tools.web import _tool_search
        srx_resp = MockResponse(status_code=200)
        srx_resp.json = lambda: {
            "results": [{"title": "SearXNG Result", "content": "内容", "url": "https://srx.com/1"}]
        }
        mock_session.get.side_effect = _make_side_effect(srx_resp, AssertionError("DDG不应该被调用"))
        results = _tool_search("test", max_results=3)
        assert len(results) >= 1
        assert any("SearXNG Result" in r for r in results)
