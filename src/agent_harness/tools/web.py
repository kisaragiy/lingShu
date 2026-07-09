"""Web tools — search, fetch, scrape"""
import json
import os
import time

from ..pipeline.llm import _session, HARNESS_DIR, call_llama

_SEARCH_CACHE: dict[str, tuple[float, list]] = {}  # query_key → (timestamp, results)
_SEARCH_CACHE_TTL = 300  # 5 分钟


def _tool_search(query: str, max_results: int = 5) -> list:
    """搜索 — 优先 SearXNG，降级 DuckDuckGo，再降级 search_tool skill（5 分钟内存缓存）

    返回格式: ["title: snippet [url]", ...]
    搜索失败时返回 ["[搜索失败] 原因"] 以便 validate_result 识别。
    """
    import time as _time
    import re as _re
    import sys

    # 缓存命中
    now = _time.time()
    cache_key = f"{query}:{max_results}"
    if cache_key in _SEARCH_CACHE:
        ts, results = _SEARCH_CACHE[cache_key]
        if now - ts < _SEARCH_CACHE_TTL:
            return results

    results = []

    # 1. SearXNG（私有搜索引擎）
    try:
        r = _session.get(
            "http://127.0.0.1:4000/search",
            params={"q": query, "format": "json", "language": "zh-CN"},
            timeout=10,
        )
        if r.status_code == 200:
            sr = r.json().get("results", [])
            if sr:
                results = [
                    f"{item.get('title', '')}: {item.get('content', '')} [{item.get('url', '')}]"
                    for item in sr[:max_results]
                ]
    except Exception:
        pass

    # 2. DuckDuckGo HTML 搜索（无需 API Key）
    if not results:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            r = _session.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers=headers,
                timeout=15,
            )
            if r.status_code == 200:
                # Parse result links from DuckDuckGo HTML (multi-strategy)
                html = r.text
                seen_urls = set()
                parsed = []

                # Strategy 1: modern DDG (result__a + result__snippet)
                links1 = _re.findall(
                    r'<a rel="nofollow" class="result__a" href="([^"]+)"[^>]*>([^<]+)</a>',
                    html,
                )
                snippets1 = _re.findall(
                    r'<a class="result__snippet"[^>]*>([^<]+)</a>',
                    html,
                )
                for i, (url, title) in enumerate(links1[:max_results]):
                    snippet = snippets1[i] if i < len(snippets1) else ""
                    if url not in seen_urls:
                        seen_urls.add(url)
                        parsed.append("%s: %s [%s]" % (title, snippet, url))

                # Strategy 2: legacy DDG (result-link + snippet-text)
                if len(parsed) < max_results:
                    links2 = _re.findall(
                        r'<a[^>]+class="result-link"[^>]*href="([^"]+)"[^>]*>([^<]+)</a>',
                        html,
                    )
                    snippets2 = _re.findall(
                        r'<span[^>]+class="snippet-text"[^>]*>([^<]+)</span>',
                        html,
                    )
                    for i, (url, title) in enumerate(links2[:max_results]):
                        if url not in seen_urls:
                            seen_urls.add(url)
                            snippet = snippets2[i] if i < len(snippets2) else ""
                            parsed.append("%s: %s [%s]" % (title, snippet, url))

                # Strategy 3: generic article links (last resort)
                if len(parsed) == 0:
                    links3 = _re.findall(
                        r'<a[^>]+href="(https?://[^"]+)"[^>]*>([^<]+)</a>',
                        html,
                    )
                    for url, title in links3[:max_results * 2]:
                        if url not in seen_urls and not any(
                            skip in url for skip in ["duckduckgo.com", "//ads."]
                        ):
                            if _re.search(r'/(article|page|post|item|product|news|content|blog)', url):
                                seen_urls.add(url)
                                parsed.append("%s: [%s]" % (title, url))

                results = parsed[:max_results]
        except Exception:
            pass

    # 3. 降级：search_tool skill
    if not results:
        try:
            skills_dir = os.path.join(os.path.dirname(HARNESS_DIR), "skills")
            if skills_dir not in sys.path:
                sys.path.insert(0, skills_dir)
            from search_tool import web_search
            results = web_search(query, max_results)
        except Exception:
            pass

    # 兜底
    if not results:
        results = ["[搜索失败] 所有搜索引擎不可用，请检查 SearXNG 或网络连接"]

    # 写入缓存
    _SEARCH_CACHE[cache_key] = (_time.time(), results)
    return results


def _tool_fetch(url: str, max_chars: int = 8000) -> str:
    """网页抓取 — 简单 HTML 去标签返回文本"""
    try:
        import re as _re
        r = _session.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        if r.status_code != 200:
            return f"[fetch] HTTP {r.status_code}"
        text = r.text
        # 简单剥 HTML 标签
        text = _re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=_re.DOTALL | _re.IGNORECASE)
        text = _re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=_re.DOTALL | _re.IGNORECASE)
        text = _re.sub(r"<[^>]+>", " ", text)
        text = _re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception as e:
        return f"[fetch] 抓取失败: {e}"


def _tool_web_scrape(url: str, extract_links: bool = False) -> str:
    """强化版网页爬取 — 提取标题 + 正文 + 可选链接列表"""
    try:
        import re as _re
        r = _session.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        if r.status_code != 200:
            return json.dumps({"error": f"HTTP {r.status_code}", "url": url}, ensure_ascii=False)
        html = r.text
        # 提取 title
        title_m = _re.search(r"<title[^>]*>(.*?)</title>", html, _re.IGNORECASE)
        title = _re.sub(r"<[^>]+>", "", title_m.group(1)).strip() if title_m else ""
        # 剥标签获取正文
        body = html
        for tag in ("script", "style", "nav", "footer", "header"):
            body = _re.sub(rf"<{tag}[^>]*>.*?</{tag}>", " ", body, flags=_re.DOTALL | _re.IGNORECASE)
        body = _re.sub(r"<[^>]+>", " ", body)
        body = _re.sub(r"\s+", " ", body).strip()[:6000]
        result = {"title": title, "body": body[:5000], "url": url}
        if extract_links:
            links = []
            for m in _re.finditer(r'<a[^>]+href=["\']([^"\']+)["\']', html, _re.IGNORECASE):
                href = m.group(1)
                if href.startswith("http"):
                    links.append(href)
            result["links"] = links[:20]
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e), "url": url}, ensure_ascii=False)


def _tool_agent_browser(url: str, instruction: str) -> str:
    """智能浏览器 — 按指令提取关键信息，不返回全页文本（节省 token）"""
    import re as _re
    try:
        r = _session.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        if r.status_code != 200:
            return f"[browser] HTTP {r.status_code}"
        text = r.text
        for tag in ("script", "style", "nav", "footer", "header"):
            text = _re.sub(rf"<{tag}[^>]*>.*?</{tag}>", " ", text, flags=_re.DOTALL | _re.IGNORECASE)
        text = _re.sub(r"<[^>]+>", " ", text)
        text = _re.sub(r"\s+", " ", text).strip()[:4000]
        prompt = f"根据以下指令从网页文本提取信息:\n指令: {instruction}\n\n网页文本:\n{text[:4000]}\n\n只输出提取结果，不加解释。"
        result, _ = call_llama([{"role": "user", "content": prompt}], system_prompt="你是信息提取器，只输出结果。")
        return result.strip()[:1500]
    except Exception as e:
        return f"[browser] 抓取失败: {e}"


from .registry import register_tool
register_tool("search", _tool_search, {
    "description": "搜索网络获取最新信息",
    "properties": {"query": "string", "max_results": "integer"},
}, privilege="read-only")
register_tool("fetch", _tool_fetch, {
    "description": "抓取网页内容为纯文本",
    "properties": {"url": "string", "max_chars": "integer"},
}, privilege="read-only")
register_tool("web_browse", _tool_fetch, {
    "description": "浏览网页获取内容（与 fetch 相同实现）",
    "properties": {"url": "string", "max_chars": "integer"},
}, privilege="read-only")
register_tool("web_scrape", _tool_web_scrape, {
    "description": "强化版网页爬取，提取标题+正文+链接",
    "properties": {"url": "string", "extract_links": "boolean"},
}, privilege="read-only")
register_tool("agent_browser", _tool_agent_browser, {
    "description": "智能浏览器 — 按指令从网页提取关键信息（token高效）",
    "properties": {"url": "string", "instruction": "string"},
}, privilege="read-only")
