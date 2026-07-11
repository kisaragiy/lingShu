"""Web tools — search, fetch, scrape"""
import json
import os
import time

from ..pipeline.llm import _session, HARNESS_DIR, call_llama

_SEARCH_CACHE: dict[str, tuple[float, list]] = {}  # query_key → (timestamp, results)
_SEARCH_CACHE_TTL = 300  # 5 分钟
_SEARCH_DIAG: list[dict] = []  # diagnostic log, last 20 entries

# Rotating User-Agent list to avoid DDG rate limiting
_USER_AGENTS = [
    # Chrome 120 Windows (primary)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    # Firefox 120 Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) "
    "Gecko/20100101 Firefox/120.0",
    # Safari 17.2 macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    # Edge 120 Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
]
_UA_INDEX = 0


def _pick_user_agent() -> str:
    """Rotate through available User-Agent strings."""
    global _UA_INDEX
    ua = _USER_AGENTS[_UA_INDEX % len(_USER_AGENTS)]
    _UA_INDEX += 1
    return ua


def _normalize_url(url: str) -> str:
    """Normalize URL for dedup: strip trailing slash, www. prefix, and UTM params."""
    import re as _re
    url = url.rstrip("/")
    url = _re.sub(r"://www\.", "://", url)
    # Strip common tracking query params
    url = _re.sub(
        r'[?&](utm_source|utm_medium|utm_campaign|utm_term|utm_content|fbclid|gclid|ref)=[^&]+',
        "",
        url,
    )
    # Clean up leftover ?& or trailing &
    url = _re.sub(r"\?&", "?", url)
    url = _re.sub(r"[&?]$", "", url)
    return url


def _log_search_diag(
    query: str,
    engine: str,
    status: str,
    count: int,
    detail: str = "",
    strategy: str = "",
):
    """Log a search diagnostic entry (in-memory, last 20)."""
    entry = {
        "ts": time.strftime("%H:%M:%S"),
        "query": query[:60],
        "engine": engine,
        "status": status,
        "count": count,
        "detail": detail[:100],
    }
    if strategy:
        entry["strategy"] = strategy
    _SEARCH_DIAG.insert(0, entry)
    if len(_SEARCH_DIAG) > 20:
        _SEARCH_DIAG.pop()
    # Also print to stderr for server-side debugging
    strategy_tag = " [%s]" % strategy if strategy else ""
    print(
        "[Search] %s%s → %s (%d 结果) %s"
        % (engine, strategy_tag, status, count, detail[:60]),
        file=__import__("sys").stderr,
    )


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
                _log_search_diag(query, "SearXNG", "ok", len(results))
            else:
                _log_search_diag(query, "SearXNG", "empty", 0)
    except Exception as e:
        _log_search_diag(query, "SearXNG", "error", 0, str(e)[:60])
        pass

    # 2. DuckDuckGo HTML 搜索（无需 API Key）
    if not results:
        try:
            headers = {
                "User-Agent": _pick_user_agent(),
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
                seen_urls: set[str] = set()
                parsed = []
                strategy_counts: dict[str, int] = {}

                # Strategy 1: modern DDG (result__a + result__snippet)
                links1 = _re.findall(
                    r'<a rel="nofollow" class="result__a" href="([^"]+)"[^>]*>([^<]+)</a>',
                    html,
                )
                snippets1 = _re.findall(
                    r'<a class="result__snippet"[^>]*>([^<]+)</a>',
                    html,
                )
                s1_count = 0
                for i, (url, title) in enumerate(links1[:max_results]):
                    snippet = snippets1[i] if i < len(snippets1) else ""
                    nu = _normalize_url(url)
                    if nu not in seen_urls:
                        seen_urls.add(nu)
                        parsed.append("%s: %s [%s]" % (title, snippet, url))
                        s1_count += 1
                strategy_counts["s1_result__a"] = s1_count

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
                    s2_count = 0
                    for i, (url, title) in enumerate(links2[:max_results]):
                        nu = _normalize_url(url)
                        if nu not in seen_urls:
                            seen_urls.add(nu)
                            snippet = snippets2[i] if i < len(snippets2) else ""
                            parsed.append("%s: %s [%s]" % (title, snippet, url))
                            s2_count += 1
                    strategy_counts["s2_result-link"] = s2_count

                # Strategy 3: generic article links (backfill when short)
                if len(parsed) < max_results:
                    links3 = _re.findall(
                        r'<a[^>]+href="(https?://[^"]+)"[^>]*>([^<]+)</a>',
                        html,
                    )
                    s3_count = 0
                    for url, title in links3[:max_results * 2]:
                        nu = _normalize_url(url)
                        if nu not in seen_urls and not any(
                            skip in url for skip in ["duckduckgo.com", "//ads."]
                        ):
                            if _re.search(r'/(article|page|post|item|product|news|content|blog)', url):
                                seen_urls.add(nu)
                                parsed.append("%s: [%s]" % (title, url))
                                s3_count += 1
                    strategy_counts["s3_generic_article"] = s3_count

                # Strategy 4: h2.result__title containers and generic result containers
                if len(parsed) < max_results:
                    # 4a: <h2 class="result__title"><a href="...">...</a></h2>
                    links4a = _re.findall(
                        r'<h2[^>]+class="result__title"[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>([^<]+)</a>.*?</h2>',
                        html,
                    )
                    s4a_count = 0
                    for url, title in links4a[:max_results]:
                        nu = _normalize_url(url)
                        if nu not in seen_urls:
                            seen_urls.add(nu)
                            parsed.append("%s: [%s]" % (title.strip(), url))
                            s4a_count += 1

                    # 4b: any <a href="http..."> inside a result-like container div
                    if len(parsed) < max_results:
                        result_containers = _re.findall(
                            r'<div[^>]*class="[^"]*result[^"]*"[^>]*>.*?</div>',
                            html,
                        )
                        s4b_count = 0
                        for container in result_containers:
                            inner_links = _re.findall(
                                r'<a[^>]+href="(https?://[^"]+)"[^>]*>([^<]+)</a>',
                                container,
                            )
                            for url, title in inner_links:
                                nu = _normalize_url(url)
                                if nu not in seen_urls and not any(
                                    skip in url for skip in ["duckduckgo.com", "//ads."]
                                ):
                                    seen_urls.add(nu)
                                    parsed.append("%s: [%s]" % (title.strip(), url))
                                    s4b_count += 1
                                    if len(parsed) >= max_results:
                                        break
                            if len(parsed) >= max_results:
                                break
                    strategy_counts["s4_title_containers"] = s4a_count + s4b_count

                results = parsed[:max_results]
                # Build detailed diagnostic with per-strategy counts
                strat_detail = "; ".join(
                    "%s=%d" % (k, v) for k, v in strategy_counts.items() if v > 0
                )
                _log_search_diag(
                    query, "DuckDuckGo", "ok", len(results),
                    detail=strat_detail, strategy="multi",
                )
        except Exception as e:
            _log_search_diag(query, "DuckDuckGo", "error", 0, str(e)[:60])
            pass

    # 3. 降级：search_tool skill
    if not results:
        try:
            skills_dir = os.path.join(os.path.dirname(HARNESS_DIR), "skills")
            if skills_dir not in sys.path:
                sys.path.insert(0, skills_dir)
            from search_tool import web_search
            results = web_search(query, max_results)
            _log_search_diag(query, "skill_fallback", "ok", len(results) if results else 0)
        except Exception as e:
            _log_search_diag(query, "skill_fallback", "error", 0, str(e)[:60])
            pass

    # 兜底
    if not results:
        _log_search_diag(query, "all", "failed", 0, "全部引擎+SearXNG+skill均不可用或返回空")
        results = ["[搜索失败] 所有搜索引擎不可用。SearXNG未运行？DDG屏蔽？请检查网络或稍后重试。"]
    else:
        _log_search_diag(query, "final", "ok", len(results))

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
