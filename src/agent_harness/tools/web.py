"""Web tools — search, fetch, scrape"""
import json
import os
import time

from ..pipeline.llm import _session, HARNESS_DIR, call_llama

_SEARCH_CACHE: dict[str, tuple[float, list]] = {}  # query_key → (timestamp, results)
_SEARCH_CACHE_TTL = 300  # 5 分钟


def _tool_search(query: str, max_results: int = 5) -> list:
    """搜索 — 优先 SearXNG，降级 Bing skill（5 分钟内存缓存）"""
    import time as _time

    # 缓存命中
    now = _time.time()
    cache_key = f"{query}:{max_results}"
    if cache_key in _SEARCH_CACHE:
        ts, results = _SEARCH_CACHE[cache_key]
        if now - ts < _SEARCH_CACHE_TTL:
            return results

    results = []
    try:
        r = _session.get(
            "http://127.0.0.1:4000/search",
            params={"q": query, "format": "json", "language": "zh-CN"},
            timeout=15,
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

    if not results:
        try:
            import sys
            skills_dir = os.path.join(os.path.dirname(HARNESS_DIR), "skills")
            if skills_dir not in sys.path:
                sys.path.insert(0, skills_dir)
            from search_tool import web_search
            results = web_search(query, max_results)
        except Exception as e:
            results = [f"[搜索失败] {e}"]

    # 写入缓存（过滤失败结果）
    if results and not results[0].startswith("[搜索失败]"):
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
