"""Main entry point — FastAPI app with shared middleware + both app routers."""
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from agent_harness import __version__
from agent_harness.core.auth import auth_db as _auth_db
from agent_harness.core.auth.api_security import validate_token
from agent_harness.core.auth import auth_jwt as _auth_jwt
from agent_harness.core.config import require_config
from agent_harness.apps.research.api import router as research_router, HOST, PORT, _API_TOKEN, _check_rate_limit, _RATE_LIMIT_WINDOW, _RATE_LIMIT_MAX, _AUTH_EXEMPT_PREFIXES, _AUTH_EXEMPT_EXACT, _AUTH_EXEMPT_V1
from agent_harness.apps.cs_demo.api import router as cs_demo_router
from agent_harness.apps.knowledge_qa.api import router as knowledge_qa_router

# ─── FastAPI app ───

app = FastAPI(
    title="Agent Harness API",
    version="1.0.0",
    description="OpenAI-compatible API for Agent Harness — single & multi-agent modes",
    openapi_url=("/openapi.json" if os.environ.get("HARNESS_ENABLE_DOCS") else None),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://127.0.0.1:{PORT}",
        f"http://localhost:{PORT}",
        f"http://{HOST}:{PORT}",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── API Auth Middleware (JWT + API Key dual-mode) ───

@app.middleware("http")
async def _api_auth_middleware(request: Request, call_next):
    from agent_harness.core.config import DISABLE_AUTH
    if DISABLE_AUTH:
        # Demo mode: set a default guest user
        request.state.user = {"id": "guest", "username": "访客", "role": "admin"}
        return await call_next(request)

    client_ip = request.client.host if request.client else "127.0.0.1"
    if not _check_rate_limit(client_ip):
        return JSONResponse(
            {"error": f"请求过于频繁，请稍后重试。当前限制: {_RATE_LIMIT_MAX} 请求/分钟"},
            status_code=429,
            headers={"Retry-After": str(_RATE_LIMIT_WINDOW)},
        )

    path = request.url.path

    if any(path.startswith(p) for p in _AUTH_EXEMPT_PREFIXES):
        return await call_next(request)
    if path in _AUTH_EXEMPT_EXACT:
        return await call_next(request)
    if path.startswith("/static/"):
        return await call_next(request)
    if not path.startswith("/v1/"):
        return await call_next(request)
    if path in _AUTH_EXEMPT_V1:
        return await call_next(request)

    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        jwt_token = auth_header[7:]
        payload = _auth_jwt.verify_token(jwt_token)
        if payload is not None:
            user = _auth_db.get_user(payload["sub"])
            if user:
                request.state.user = user
                return await call_next(request)

    api_key = request.headers.get("x-api-key", "") or request.query_params.get("api_token", "")
    if api_key and validate_token(api_key, _API_TOKEN):
        request.state.user = {
            "id": "__api_key__",
            "username": "api",
            "role": "admin",
            "display_name": "API Client",
        }
        return await call_next(request)

    return JSONResponse(
        {"error": "认证失败。请登录 (POST /v1/auth/login) 或提供 API Key (X-API-Key header)。"},
        status_code=401,
    )


# ─── Security Headers Middleware (CSP + HSTS) ───

@app.middleware("http")
async def _security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    csp = (
        "default-src 'self';"
        "script-src 'self' 'unsafe-inline' https://api.github.com;"
        "style-src 'self' 'unsafe-inline';"
        "img-src 'self' data: https:;"
        "font-src 'self';"
        "connect-src 'self' https://api.github.com;"
        "frame-ancestors 'none';"
        "base-uri 'self';"
        "form-action 'self'"
    )
    response.headers["Content-Security-Policy"] = csp
    return response


# ─── Static files (灵枢 frontend) ───
RESEARCH_STATIC_DIR = Path(__file__).resolve().parent / "apps" / "research" / "static"
if RESEARCH_STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(RESEARCH_STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
@app.get("/setup", include_in_schema=False)
@app.get("/dashboard", include_in_schema=False)
async def serve_frontend():
    index = RESEARCH_STATIC_DIR / "index.html"
    if index.exists():
        html = index.read_text("utf-8")
        needs_admin = _auth_db.needs_initial_admin()
        # Skip admin requirement in demo mode (DISABLE_AUTH)
        from agent_harness.core.config import DISABLE_AUTH
        if DISABLE_AUTH:
            needs_admin = False
        token_script = (
            '<script>'
            'window.__API_TOKEN__="{}";'
            'window.__NEEDS_ADMIN__={};'
            'window.__VERSION__="{}";'
            '</script>'
        ).format(_API_TOKEN, "true" if needs_admin else "false", __version__)
        if "</head>" in html:
            html = html.replace("</head>", token_script + "</head>")
        else:
            html = html.replace("<head>", "<head>" + token_script)
        # Demo mode banner
        from agent_harness.core.config import DISABLE_AUTH
        if DISABLE_AUTH:
            banner = '<div style="background:#fef3c7;color:#92400e;text-align:center;padding:6px 12px;font-size:13px;border-bottom:1px solid #fbbf24">\uD83D\uDFE1 Demo \u6A21\u5F0F \u2014 \u56DE\u590D\u4E3A\u6A21\u677F\u793A\u4F8B\uFF0C\u975E\u771F\u5B9E LLM \u751F\u6210\u3002\u914D\u7F6E LLM \u540E\u7AEF\u540E\u53EF\u83B7\u5F97\u5B8C\u6574\u529F\u80FD\u3002</div>'
            if "<body>" in html:
                html = html.replace("<body>", "<body>" + banner)
        return HTMLResponse(html)
    return JSONResponse({"message": "灵枢 API 运行中"}, status_code=200)


# ─── Include both app routers ───
app.include_router(research_router)
app.include_router(cs_demo_router)
app.include_router(knowledge_qa_router)


# ─── Direct entry point ───

def main():
    import uvicorn

    from agent_harness.core.pipeline.session_store import init_store as _init_session_store
    from agent_harness.core.pipeline.session_store import session_count as _session_count

    _init_session_store()
    _auth_db._get_db()
    admin_needed = _auth_db.needs_initial_admin()
    user_count = _auth_db.user_count()
    _auth_db.cleanup_expired_sessions()

    count = _session_count()
    print("")
    print("  ⚡ 灵枢 — LingShu Agent")
    print("  " + ("-" * 40))
    print(f"  API:       http://{HOST}:{PORT}/v1")
    print(f"  Frontend:  http://{HOST}:{PORT}")
    if admin_needed:
        print("  ⚠️  首次启动 — 需创建管理员账号")
    else:
        print(f"  用户:     {user_count} 人")
    _max_agents = int(os.environ.get("HARNESS_MAX_CONCURRENT_AGENTS", "5"))
    if _max_agents > 0:
        print(f"  并发:     {_max_agents} 个 Agent 同时执行")
    print(f"  Token:     {_API_TOKEN[:8]}...{_API_TOKEN[-4:]}")
    print("  " + ("-" * 40))
    print("")

    # ── Auto-setup LLM backend if not configured ──
    from agent_harness.core.config import LLAMA_API, DISABLE_AUTH
    if not LLAMA_API and not DISABLE_AUTH:
        from agent_harness.auto_setup import auto_setup
        auto_setup()

    if not LLAMA_API and DISABLE_AUTH:
        # Demo mode: skip LLM check, server will use template fallbacks
        pass
    else:
        require_config()
    try:
        from agent_harness.core.config import print_config_warnings
        print_config_warnings()
    except ImportError:
        pass
    if count:
        print(f"  会话: {count} 个")
    print("")

    try:
        from agent_harness.core.agent_cron import get_scheduler
        sched = get_scheduler()
        sched.start()
        _scheduler_count = len(sched.list_tasks() if hasattr(sched, 'list_tasks') else [])
        print(f"  定时任务: {_scheduler_count} 个")
    except Exception:
        pass

    try:
        from agent_harness.plugin_loader import load_plugins
        plugins = load_plugins()
        _plugin_count = len(plugins)
        print(f"  插件:     {_plugin_count} 个（{sum(1 for p in plugins if p.get('success'))} 成功）")
    except Exception:
        pass

    _workers = int(os.environ.get("HARNESS_WORKERS", "1"))
    uvicorn.run(
        "agent_harness.main:app",
        host=HOST,
        port=PORT,
        log_level="info",
        reload=False,
        workers=_workers,
    )


if __name__ == "__main__":
    main()
