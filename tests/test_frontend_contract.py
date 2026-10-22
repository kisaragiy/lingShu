"""前端契约测试（FRONT-1 / FRONT-2 / FRONT-3）— 静态站三个真 bug 的回归锁。

没有浏览器测试框架，所以这里锁的是**可静态判定的不变量**，每条都对应一个已修的真实故障：

  FRONT-1  sw.js 预缓存的是客服 demo 的 /cs-demo（主应用 / 从未注册 SW，API 还会被
           缓存优先读脏）；scope 缺 Service-Worker-Allowed 头时注册 scope:'/' 会被拒。
  FRONT-2  会话恢复路径把助手消息 slice(0, 500)，长报告恢复后 DOM 里只剩残句。
  FRONT-3  index.html 7 个脚本同步加载，且内联 init() 直接依赖它们（加 defer 必须
           把 init 挪到 DOMContentLoaded，否则白屏）。

运行：.venv/Scripts/python.exe -m pytest tests/test_frontend_contract.py -q
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

STATIC = SRC / "agent_harness" / "apps" / "research" / "static"
JS_FILES = [
    "api.js",
    "ui.js",
    "chat.js",
    "sidebar.js",
    "tabs.js",
    "safety-ui.js",
    "orchestrator.js",
]


def _sw() -> str:
    return (STATIC / "sw.js").read_text(encoding="utf-8")


def _precache_urls(sw: str) -> list[str]:
    block = sw.split("PRECACHE_URLS = [")[1].split("];")[0]
    return re.findall(r"'(/[^']*)'", block)


# ─── FRONT-1：Service Worker 只服务本应用，且绝不缓存 API ───

def test_sw_precache_targets_research_app_not_cs_demo():
    sw = _sw()
    urls = _precache_urls(sw)
    assert "/cs-demo" not in urls, f"sw.js 仍在预缓存客服 demo 页面: {urls}"
    assert not any(u.startswith("/cs-") for u in urls), f"预缓存里混入客服路径: {urls}"
    cache_name = re.search(r"CACHE_NAME = '([^']+)'", sw).group(1)
    assert cache_name != "lingShu-cs-v1", "缓存名未 bump，已装旧 SW 的客户端不会更新"
    assert "/" in urls and "/static/manifest.json" in urls


def test_sw_never_caches_api_and_navigation_is_network_first():
    sw = _sw()
    assert "NEVER_CACHE_PREFIXES" in sw and "'/v1/'" in sw, "API 路径必须直连，不进 SW 缓存"
    # 导航网络优先：否则用户会拿到旧版 HTML（每次请求注入的 __VERSION__ 被冻住）
    assert "req.mode === 'navigate'" in sw
    nav = sw.split("req.mode === 'navigate'")[1].split("// ② ")[0]
    assert "await fetch(req)" in nav and "cache.match('/')" in nav


def test_precache_urls_all_exist_on_disk():
    """install 里任何一个 URL 404 都会让 SW 安装整单失败 —— 名单必须对应真实文件。"""
    urls = _precache_urls(_sw())
    assert urls, "解析不到 PRECACHE_URLS"
    for url in urls:
        target = STATIC / "index.html" if url == "/" else STATIC / url.removeprefix("/static/")
        assert target.exists(), f"PRECACHE_URLS 里的 {url} 在 static/ 下不存在"


def test_manifest_is_valid_json_with_declared_icons():
    manifest = json.loads((STATIC / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["start_url"] == "/"
    assert manifest["display"] == "standalone"
    for icon in manifest["icons"]:
        path = STATIC / icon["src"].removeprefix("/static/")
        assert path.exists(), f"manifest 声明的图标不存在: {icon['src']}"


def test_static_serves_sw_with_scope_header():
    """没有 Service-Worker-Allowed: / 时，register(scope:'/') 会被浏览器拒绝。"""
    from fastapi.testclient import TestClient

    from agent_harness.main import app

    client = TestClient(app)
    r = client.get("/static/sw.js")
    assert r.status_code == 200, r.text[:200]
    assert r.headers.get("service-worker-allowed") == "/"
    assert "no-cache" in r.headers.get("cache-control", "")
    assert "/cs-demo" not in _precache_urls(r.text)


# ─── FRONT-2：会话恢复不得截断助手正文 ───

def test_session_restore_renders_full_assistant_content():
    chat = (STATIC / "js" / "chat.js").read_text(encoding="utf-8")
    body = chat.split("function renderMessages(msgs)")[1].split("async function sendChatMessage")[0]
    assert ".slice(0, 500)" not in body, "恢复路径仍在截断助手消息（长报告被腰斩）"
    assert "addChatBubble('assistant', m.content || '')" in body


def test_streaming_path_still_untouched():
    """实时 SSE 路径本来就不截断，别在修历史恢复时误伤它。"""
    chat = (STATIC / "js" / "chat.js").read_text(encoding="utf-8")
    assert "addChatBubble('assistant', '<span class=\"spinner\"></span>', assistantId)" in chat


# ─── FRONT-3：defer + 内联 init 不得在 defer 脚本之前跑 ───

def test_index_defers_all_scripts():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for name in JS_FILES:
        assert f'<script defer src="/static/js/{name}"></script>' in html, f"{name} 未加 defer"


def test_inline_init_waits_for_defer_scripts():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "<script>init();</script>" not in html, "内联 init() 会早于 defer 脚本执行 → ReferenceError"
    assert "document.addEventListener('DOMContentLoaded'" in html


def test_sw_registration_uses_root_scope():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "navigator.serviceWorker.register('/static/sw.js', { scope: '/' })" in html
