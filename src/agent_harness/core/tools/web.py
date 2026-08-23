"""Web tools — search, fetch, scrape"""
import contextlib
import json
import os
import time

from ..pipeline.llm import HARNESS_DIR, _session, call_llama

_SEARCH_CACHE: dict[str, tuple[float, list]] = {}
_SEARCH_CACHE_TTL = 300
_SEARCH_DIAG: list[dict] = []

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) "
    "Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
]
_UA_INDEX = 0
_TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")


def _pick_user_agent() -> str:
    global _UA_INDEX
    ua = _USER_AGENTS[_UA_INDEX % len(_USER_AGENTS)]
    _UA_INDEX += 1
    return ua


def _normalize_url(url: str) -> str:
    import re as _re
    url = url.rstrip("/")
    url = _re.sub(r"://www\.", "://", url)
    url = _re.sub(
        r'[?&](utm_source|utm_medium|utm_campaign|utm_term|utm_content|fbclid|gclid|ref)=[^&]+',
        "", url,
    )
    url = _re.sub(r"\?&", "?", url)
    url = _re.sub(r"[&?]$", "", url)
    return url


def _dedup_results(new_results: list[str], seen: set[str]) -> list[str]:
    import re as _re
    out = []
    for r in new_results:
        m = _re.search(r'\[([^\]]+)\]$', r)
        url = _normalize_url(m.group(1)) if m else r
        if url not in seen:
            seen.add(url)
            out.append(r)
    return out


def _log_search_diag(query: str, engine: str, status: str, count: int,
                      detail: str = "", strategy: str = ""):
    entry = {
        "ts": time.strftime("%H:%M:%S"), "query": query[:60],
        "engine": engine, "status": status, "count": count,
        "detail": detail[:100],
    }
    if strategy:
        entry["strategy"] = strategy
    _SEARCH_DIAG.insert(0, entry)
    if len(_SEARCH_DIAG) > 20:
        _SEARCH_DIAG.pop()
    strat_tag = f" [{strategy}]" if strategy else ""
    print(f"[Search] {engine}{strat_tag} → {status} ({count} 结果) {detail[:60]}",
          file=__import__("sys").stderr)


def _warm_search_cache():
    import threading as _t
    def _warm():
        for url in [
            "http://127.0.0.1:4000/search?q=test&format=json",
            "https://html.duckduckgo.com/html/?q=test",
        ]:
            with contextlib.suppress(Exception):
                _session.get(url, timeout=5)
    _t.Thread(target=_warm, daemon=True).start()


# ─── Webgate L0→L3 auto-escalation fetch ───
def _webgate_fetch(url: str, timeout: int = 30) -> str | None:
    """Fetch via webgate.py L0→L3 chain. Returns text or None on fallback."""
    import sys as _sys, os as _os
    wg_path = _os.path.normpath(_os.path.join(
        _os.path.dirname(HARNESS_DIR), "..", "..", "tools", "webgate"))
    if wg_path not in _sys.path:
        _sys.path.insert(0, wg_path)
    try:
        import webgate as _wg
        result = _wg.fetch(url, want_text=True)
        if result.get("level") == "FAIL":
            return None
        text = result.get("text") or result.get("html") or ""
        via = "→".join(result.get("via", []))
        level = result.get("level", "?")
        _log_search_diag(url[:60], f"wg_{level}", "ok", len(text), via)
        import re as _re
        return _re.sub(r"\s+", " ", text).strip()
    except ImportError:
        return None
    except Exception as e:
        print(f"[webgate] {type(e).__name__}: {e}", file=__import__("sys").stderr)
        return None


def _fetch_fallback(url: str, max_chars: int = 8000) -> str:
    """Fallback: bare requests (no anti-scraping)."""
    try:
        import re as _re
        r = _session.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        if r.status_code != 200:
            return f"[fetch] HTTP {r.status_code}"
        text = r.text
        text = _re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=_re.DOTALL | _re.IGNORECASE)
        text = _re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=_re.DOTALL | _re.IGNORECASE)
        text = _re.sub(r"<[^>]+>", " ", text)
        text = _re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception as e:
        return f"[fetch] 抓取失败: {e}"


# ═══════════════════════════════════════
# Tavily
# ═══════════════════════════════════════

def _search_tavily(query: str, max_results: int = 5) -> list[str]:
    if not _TAVILY_API_KEY:
        _log_search_diag(query, "Tavily", "skip", 0, "no API key")
        return []
    try:
        import urllib.request as _ur
        body = json.dumps({
            "api_key": _TAVILY_API_KEY, "query": query,
            "max_results": max_results, "search_depth": "basic",
            "include_answer": False, "include_raw_content": False,
        }).encode()
        req = _ur.Request("https://api.tavily.com/search",
                          data=body, headers={"Content-Type": "application/json"})
        resp = _ur.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        results = data.get("results", [])
        if not results:
            _log_search_diag(query, "Tavily", "empty", 0)
            return []
        formatted = [
            f"{r.get('title', '')}: {r.get('content', '')} [{r.get('url', '')}]"
            for r in results[:max_results]
        ]
        _log_search_diag(query, "Tavily", "ok", len(formatted))
        return formatted
    except Exception as e:
        _log_search_diag(query, "Tavily", "error", 0, str(e)[:60])
        return []


# ═══════════════════════════════════════
# Main search
# ═══════════════════════════════════════

def _tool_search(query: str, max_results: int = 5) -> list:
    now = time.time()
    cache_key = f"{query}:{max_results}"
    if cache_key in _SEARCH_CACHE:
        ts, results = _SEARCH_CACHE[cache_key]
        if now - ts < _SEARCH_CACHE_TTL:
            return results

    all_results: list[str] = []
    seen_urls: set[str] = set()

    # 1. SearXNG
    try:
        r = _session.get("http://127.0.0.1:4000/search",
                         params={"q": query, "format": "json", "language": "zh-CN"},
                         timeout=10)
        if r.status_code == 200:
            sr = r.json().get("results", [])
            if sr:
                raw = [f"{i.get('title','')}: {i.get('content','')} [{i.get('url','')}]"
                       for i in sr[:max_results]]
                all_results = _dedup_results(raw, seen_urls)
                _log_search_diag(query, "SearXNG", "ok", len(all_results))
            else:
                _log_search_diag(query, "SearXNG", "empty", 0)
    except Exception as e:
        _log_search_diag(query, "SearXNG", "error", 0, str(e)[:60])

    # 2. DuckDuckGo
    if not all_results:
        try:
            import re as _re
            headers = {"User-Agent": _pick_user_agent()}
            r = _session.get("https://html.duckduckgo.com/html/",
                             params={"q": query}, headers=headers, timeout=15)
            if r.status_code != 200:
                _log_search_diag(query, "DuckDuckGo", "retry", 0, f"HTTP {r.status_code}")
                r = _session.get("https://html.duckduckgo.com/html/",
                                 params={"q": query},
                                 headers={"User-Agent": _pick_user_agent()},
                                 timeout=15)
            if r.status_code == 200:
                html = r.text
                ddg_results: list[str] = []
                l1 = _re.findall(
                    r'<a rel="nofollow" class="result__a" href="([^"]+)"[^>]*>([^<]+)</a>', html)
                s1 = _re.findall(r'<a class="result__snippet"[^>]*>([^<]+)</a>', html)
                for i, (url, title) in enumerate(l1[:max_results]):
                    sn = s1[i] if i < len(s1) else ""
                    nu = _normalize_url(url)
                    if nu not in seen_urls:
                        seen_urls.add(nu)
                        ddg_results.append(f"{title}: {sn} [{url}]")
                if len(ddg_results) < max_results:
                    l2 = _re.findall(
                        r'<a[^>]+class="result-link"[^>]*href="([^"]+)"[^>]*>([^<]+)</a>', html)
                    s2 = _re.findall(r'<span[^>]+class="snippet-text"[^>]*>([^<]+)</span>', html)
                    for i, (url, title) in enumerate(l2[:max_results]):
                        nu = _normalize_url(url)
                        if nu not in seen_urls:
                            seen_urls.add(nu)
                            sn = s2[i] if i < len(s2) else ""
                            ddg_results.append(f"{title}: {sn} [{url}]")
                if len(ddg_results) < max_results:
                    l3 = _re.findall(
                        r'<a[^>]+href="(https?://[^"]+)"[^>]*>([^<]+)</a>', html)
                    for url, title in l3[:max_results * 2]:
                        nu = _normalize_url(url)
                        if nu not in seen_urls and "duckduckgo.com" not in url and "//ads." not in url:
                            seen_urls.add(nu)
                            ddg_results.append(f"{title}: [{url}]")
                if len(ddg_results) < max_results:
                    divs = _re.findall(
                        r'<div[^>]*class="[^"]*result[^"]*"[^>]*>.*?</div>', html)
                    for d in divs:
                        inner = _re.findall(r'<a[^>]+href="(https?://[^"]+)"[^>]*>([^<]+)</a>', d)
                        for url, title in inner:
                            nu = _normalize_url(url)
                            if nu not in seen_urls and "duckduckgo.com" not in url:
                                seen_urls.add(nu)
                                ddg_results.append(f"{title}: [{url}]")
                                if len(ddg_results) >= max_results:
                                    break
                        if len(ddg_results) >= max_results:
                            break
                all_results = ddg_results[:max_results]
                _log_search_diag(query, "DuckDuckGo", "ok", len(all_results))
        except Exception as e:
            _log_search_diag(query, "DuckDuckGo", "error", 0, str(e)[:60])

    # 3. Tavily
    if not all_results:
        tavily_results = _search_tavily(query, max_results)
        if tavily_results:
            all_results = _dedup_results(tavily_results, seen_urls)

    if not all_results:
        _log_search_diag(query, "all", "failed", 0, "所有引擎不可用")
        all_results = ["[搜索失败] 所有搜索引擎均不可用。"]
    else:
        _log_search_diag(query, "final", "ok", len(all_results))

    _SEARCH_CACHE[cache_key] = (time.time(), all_results)
    return all_results


# ═══════════════════════════════════════
# Query decomposition
# ═══════════════════════════════════════

_QUERY_DECOMPOSE_PROMPT = """你是一个搜索策略师。用户的问题可能涉及多个角度，请拆解为 3 条独立的搜索查询（中英文混合），覆盖不同维度。
直接输出 JSON 数组如 ["q1","q2","q3"]，不加解释。

用户问题: {query}"""


def _tool_query_decompose(query: str) -> str:
    if not query or len(query.strip()) < 10:
        return json.dumps([query], ensure_ascii=False)
    try:
        prompt = _QUERY_DECOMPOSE_PROMPT.format(query=query[:500])
        raw, _ = call_llama([{"role": "user", "content": prompt}],
                            system_prompt="你只输出 JSON 数组，不加解释。")
        import re as _re
        m = _re.search(r'\[.*?\]', raw, re.DOTALL)
        if m:
            queries = json.loads(m.group(0))
            if isinstance(queries, list) and len(queries) >= 1:
                return json.dumps(queries[:5], ensure_ascii=False)
    except Exception:
        pass
    return json.dumps([query], ensure_ascii=False)


# ═══════════════════════════════════════
# Fetch / scrape / browser — backed by webgate L0→L3
# ═══════════════════════════════════════

def _tool_fetch(url: str, max_chars: int = 8000) -> str:
    """网页抓取 — webgate L0→L3 反爬链自动降级"""
    text = _webgate_fetch(url)
    if text is not None:
        return text[:max_chars]
    return _fetch_fallback(url, max_chars)


def _try_playwright_fetch(url: str) -> str | None:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, timeout=15000)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(3000)
            text = page.inner_text("body") or ""
            browser.close()
            return text[:5000]
    except Exception:
        return None


def _tool_web_scrape(url: str, extract_links: bool = False) -> str:
    """强化版网页爬取 — webgate L0→L3 + 提取标题/正文/链接"""
    import re as _re
    html = _webgate_fetch(url)
    if html is None:
        try:
            r = _session.get(url, timeout=15, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            })
            if r.status_code != 200:
                return json.dumps({"error": f"HTTP {r.status_code}", "url": url}, ensure_ascii=False)
            html = r.text
        except Exception as e:
            return json.dumps({"error": str(e), "url": url}, ensure_ascii=False)

    title_m = _re.search(r"<title[^>]*>(.*?)</title>", html, _re.IGNORECASE)
    title = _re.sub(r"<[^>]+>", "", title_m.group(1)).strip() if title_m else ""
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


def _tool_agent_browser(url: str, instruction: str) -> str:
    """智能浏览器 — webgate L0→L3 + 按指令提取信息"""
    import re as _re
    text = _webgate_fetch(url)
    if text is None:
        try:
            r = _session.get(url, timeout=15, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            })
            if r.status_code == 200:
                text = r.text
        except Exception:
            pass
        if text is None:
            pw_text = _try_playwright_fetch(url)
            if pw_text:
                text = pw_text
    if text is None:
        return "[browser] 抓取失败"
    try:
        for tag in ("script", "style", "nav", "footer", "header"):
            text = _re.sub(rf"<{tag}[^>]*>.*?</{tag}>", " ", text, flags=_re.DOTALL | _re.IGNORECASE)
        text = _re.sub(r"<[^>]+>", " ", text)
        text = _re.sub(r"\s+", " ", text).strip()[:4000]
        prompt = f"根据指令从网页提取信息:\n指令: {instruction}\n\n{text[:4000]}\n\n只输出结果。"
        result, _ = call_llama([{"role": "user", "content": prompt}],
                               system_prompt="你是信息提取器，只输出结果。")
        return result.strip()[:1500]
    except Exception as e:
        return f"[browser] 处理失败: {e}"


# ═══════════════════════════════════════
# Registration
# ═══════════════════════════════════════

_warm_search_cache()

from .registry import register_tool

register_tool("search", _tool_search, {
    "description": "🌐 搜索（SearXNG → DuckDuckGo → Tavily API）",
    "properties": {"query": "string", "max_results": "integer"},
}, privilege="read-only")
register_tool("fetch", _tool_fetch, {
    "description": "抓取网页 — webgate 反爬链(L0→L3)自动降级",
    "properties": {"url": "string", "max_chars": "integer"},
}, privilege="read-only")
register_tool("web_browse", _tool_fetch, {
    "description": "浏览网页（同 fetch，webgate 反爬链自动降级）",
    "properties": {"url": "string", "max_chars": "integer"},
}, privilege="read-only")
register_tool("web_scrape", _tool_web_scrape, {
    "description": "强化版网页爬取 — webgate 反爬链，提取标题+正文+链接",
    "properties": {"url": "string", "extract_links": "boolean"},
}, privilege="read-only")
register_tool("agent_browser", _tool_agent_browser, {
    "description": "智能浏览器 — webgate 反爬链，按指令提取信息",
    "properties": {"url": "string", "instruction": "string"},
}, privilege="read-only")
register_tool("query_decompose", _tool_query_decompose, {
    "description": "🔍 查询分解 — 复杂问题拆成 3 条并行搜索子查询",
    "properties": {"query": "string"},
}, privilege="read-only")