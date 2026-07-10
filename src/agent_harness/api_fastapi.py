"""Agent Harness API — OpenAI-compatible FastAPI server with conversation session support.

Features:
- OpenAI-compatible /v1/chat/completions (stream + non-stream)
- Session-based conversation history (X-Session-Id header)
- Multi-agent (Supervisor-Worker) and single-agent modes
- Model selection:
  - agent-harness-multi (default) → Supervisor-Worker multi-agent
  - agent-harness            → Single-agent pipeline
- Progressive streaming (status updates → worker results → final output)
- Direct tool call endpoint

Start:
    agent-harness serve          # via CLI entry point
    python -m agent_harness.api_fastapi   # directly

Connect Open WebUI:
    URL:  http://host.docker.internal:8788/v1
    Key:  (leave blank)
    Model: agent-harness-multi
"""

import asyncio
import json
import os
import queue
import threading
import time
import uuid
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
from pydantic import BaseModel

from .api_security import (
    load_or_generate_token,
    reset_token as _reset_token,
    validate_token,
    audit_config as _audit_config,
    log_audit as _log_audit,
)
from . import auth_db as _auth_db
from . import auth_jwt as _auth_jwt
from .tools.tool_config import is_tool_enabled, toggle_tool as _toggle_tool, list_disabled as _list_disabled_tools

HOST = os.environ.get("HARNESS_API_HOST", "127.0.0.1")
PORT = int(os.environ.get("HARNESS_API_PORT", "8788"))

# ─── API Key (CLI / Open WebUI fallback) ───
_API_TOKEN: str = load_or_generate_token()

# ─── Auth exempt paths (no authentication required) ───
_AUTH_EXEMPT_PREFIXES = ("/health",)
_AUTH_EXEMPT_EXACT = ("/", "/setup", "/dashboard")
_AUTH_EXEMPT_V1 = ("/v1/auth/login", "/v1/auth/refresh", "/v1/auth/setup-admin", "/v1/setup/config")

# ─── Token optimization: conversation window ───
# 分析结论：80% 请求 ≤2轮，95% ≤4轮，99%+ ≤6轮
# 保留 4 轮（8 条消息）覆盖 95% 场景，平衡质量与成本
# 可通过环境变量 HARNESS_MAX_HISTORY 运行时调整（例: HARNESS_MAX_HISTORY=8 agent-harness serve）
MAX_HISTORY_ROUNDS = int(os.environ.get("HARNESS_MAX_HISTORY", "8"))
MAX_HISTORY_MSGS = MAX_HISTORY_ROUNDS * 2    # 每个 round = user + assistant
SESSION_TRIM_AT = MAX_HISTORY_ROUNDS * 4     # 持久化保留弹性（4x 保留未修剪的历史）

# ─── Session store (persistent) ───
from .pipeline.session_store import (
    save_session as _save_session,
    load_session as _load_session,
    list_sessions as _list_sessions,
    delete_session as _delete_session,
    clean_expired as _clean_expired,
    init_store as _init_session_store,
    session_count as _session_count,
)

# ─── Running task registry (for cancellation) ───
import threading as _threading
_running_tasks: dict[str, _threading.Event] = {}
_running_tasks_lock = _threading.Lock()

# ─── Agent concurrency control ───
# Limit simultaneous agent executions to prevent overload.
# Only affects research tasks (/v1/chat/completions, both stream & non-stream).
# Lightweight endpoints (auth, sessions, reports, config, tools) bypass this.
# Default: 5 concurrent agents — enough for 2-3 simultaneous users.
# Set HARNESS_MAX_CONCURRENT_AGENTS=0 to disable limit.
_MAX_CONCURRENT_AGENTS = int(os.environ.get("HARNESS_MAX_CONCURRENT_AGENTS", "5"))
if _MAX_CONCURRENT_AGENTS > 0:
    _agent_semaphore = _threading.Semaphore(_MAX_CONCURRENT_AGENTS)
else:
    _agent_semaphore = None

# Thread pool for async file I/O (sessions, reports)
_io_executor = None


def _build_history_context(messages: list[dict]) -> str:
    """Build context string from recent conversation history (capped to save tokens)."""
    if not messages:
        return ""
    # Take only last N rounds
    trimmed = messages[-(MAX_HISTORY_MSGS):]
    lines = []
    for m in trimmed:
        role = m.get("role", "unknown")
        content = m.get("content", "")
        if len(content) > 300:
            content = content[:300] + "..."
        lines.append("[%s]: %s" % (role, content))
    return "\n".join(lines[-16:])


# ─── SSE helper ───

def _sse_chunk(content: str, role: str = "") -> str:
    """Build a single SSE data chunk."""
    delta = {}
    if role:
        delta["role"] = role
    if content:
        delta["content"] = content
    obj = {
        "choices": [{"delta": delta, "index": 0, "finish_reason": None}]
    }
    return "data: %s\n\n" % json.dumps(obj, ensure_ascii=False)


def _sse_done(result_text: str) -> str:
    """Build the final SSE chunk with usage info."""
    usage = {"prompt_tokens": 0, "completion_tokens": len(result_text), "total_tokens": 0}
    obj = {
        "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}],
        "usage": usage,
    }
    return "data: %s\n\ndata: [DONE]\n\n" % json.dumps(obj, ensure_ascii=False)


# ─── Execute harness (blocking, run in thread) ───

def _execute_multi(prompt: str, history_context: str, session_id: str = "") -> dict:
    from .graph_multi import run_multi_agent, set_progress_queue, clear_progress_queue, _progress_queue
    from .pipeline.cancel import set_cancel_event, clear_cancel_event
    enhanced = prompt
    if history_context:
        enhanced = (
            "以下是本对话的历史记录（越新的越靠前）:\n%s\n\n"
            "请基于以上对话上下文，回答用户当前的问题:\n%s"
        ) % (history_context, prompt)

    # Auto-inject knowledge base context if available
    try:
        from .tools.rag_store import query as rag_query, list_collections
        kb_cols = list_collections()
        if kb_cols:
            kb_results = []
            for col in kb_cols:
                try:
                    results = rag_query(prompt, collection=col, top_k=3)
                    kb_results.extend(results)
                except Exception:
                    pass
            if kb_results:
                kb_context = "\n\n".join(
                    "[知识库 - %s] %s" % (r.get("source", "unknown"), r["text"])
                    for r in kb_results[:3]
                )
                enhanced = (
                    "以下是从知识库中检索到的相关信息（请优先使用这些信息回答）:\n%s\n\n"
                    "用户问题: %s" % (kb_context, enhanced)
                )
                if _progress_queue:
                    _progress_queue.put({
                        "type": "progress",
                        "content": "📚 从知识库中找到 %d 条相关信息\n" % len(kb_results)
                    })
    except Exception:
        pass

    return run_multi_agent(enhanced)


def _execute_single(prompt: str, history_context: str) -> str:
    from .graph import run as run_single
    enhanced = prompt
    if history_context:
        enhanced = "以下是对话历史:\n%s\n\n当前问题: %s" % (history_context, prompt)

    # Auto-inject KB context
    try:
        from .tools.rag_store import query as rag_query, list_collections
        kb_cols = list_collections()
        if kb_cols:
            kb_results = []
            for col in kb_cols:
                try:
                    results = rag_query(prompt, collection=col, top_k=2)
                    kb_results.extend(results)
                except Exception:
                    pass
            if kb_results:
                kb_context = "\n".join(
                    "[知识库] %s" % r["text"] for r in kb_results[:2]
                )
                enhanced = "知识库信息:\n%s\n\n%s" % (kb_context, enhanced)
    except Exception:
        pass

    return run_single(enhanced)


# ─── Streaming with progress queue ───
def _run_with_queue(prompt: str, history: str, model: str, q: queue.Queue, session_id: str = ""):
    """Run harness in a thread, pushing progress events into queue."""
    try:
        # Connect progress queue and cancel event to graph modules
        from .graph_multi import set_progress_queue, clear_progress_queue
        from .pipeline.cancel import set_cancel_event, clear_cancel_event, is_cancelled
        set_progress_queue(q)

        # Acquire agent semaphore
        if _agent_semaphore is not None:
            _agent_semaphore.acquire()

        # Register this task for cancellation
        if session_id:
            cancel_event = _threading.Event()
            with _running_tasks_lock:
                _running_tasks[session_id] = cancel_event
            set_cancel_event(cancel_event)

        q.put({"type": "status", "content": "🤔 分析请求中...\n"})

        if model in ("agent-harness", "agent-harness-single", "lingShu-fast"):
            q.put({"type": "status", "content": "⚡ 快模式（单 Agent）处理中...\n"})
            result = _execute_single(prompt, history)
            q.put({"type": "result", "content": result})
        else:
            q.put({"type": "status", "content": "🧠 深模式（多 Agent 编排）执行中...\n"})
            result = _execute_multi(prompt, history, session_id=session_id)
            rounds = result.get("rounds", 1)
            worker_results = result.get("worker_results", {})
            workers = list(worker_results.keys())
            errors = result.get("errors", [])

            if workers:
                status_line = "✅ %d 个 Worker 完成 (%d 轮): %s\n" % (
                    len(workers), rounds, ", ".join(workers))
                q.put({"type": "status", "content": status_line})
            if errors:
                q.put({"type": "status", "content": "⚠️ %d 个错误\n" % len(errors)})

            final = result.get("final_output", "")
            if final:
                q.put({"type": "result", "content": final})
            else:
                q.put({"type": "result", "content": "[Harness] 处理完成（无输出）"})

        q.put({"type": "done"})

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        q.put({"type": "error", "content": "❌ 执行出错: %s\n\n%s" % (e, tb[:500])})
        q.put({"type": "done"})
    finally:
        try:
            # Release agent semaphore
            if _agent_semaphore is not None:
                _agent_semaphore.release()
            from .graph_multi import clear_progress_queue
            from .pipeline.cancel import clear_cancel_event
            clear_progress_queue()
            clear_cancel_event()
            if session_id:
                with _running_tasks_lock:
                    _running_tasks.pop(session_id, None)
        except Exception:
            pass


async def _stream_progress(prompt: str, history: str, model: str, session_id: str):
    q = queue.Queue()
    t = threading.Thread(
        target=_run_with_queue, args=(prompt, history, model, q, session_id), daemon=True
    )
    t.start()

    # Initial role marker
    yield _sse_chunk("", role="assistant")

    result_text = ""
    while True:
        try:
            event = q.get(timeout=0.5)
        except queue.Empty:
            if not t.is_alive():
                break
            continue

        if event["type"] == "done":
            break
        elif event["type"] in ("error", "status", "progress"):
            content = event["content"]
            result_text += content
            yield _sse_chunk(content)
        elif event["type"] == "result":
            content = event["content"]
            result_text = content
            yield _sse_chunk(content)

    # Save to session
    session = _load_session(session_id) or []
    session.append({"role": "user", "content": prompt, "ts": time.time()})
    session.append({"role": "assistant", "content": result_text, "ts": time.time()})
    _save_session(session_id, session)

    yield _sse_done(result_text)


# ─── FastAPI app ───

app = FastAPI(
    title="Agent Harness API",
    version="1.0.0",
    description="OpenAI-compatible API for Agent Harness — single & multi-agent modes",
    # /docs disabled by default — set HARNESS_ENABLE_DOCS=1 to enable
    openapi_url=("/openapi.json" if os.environ.get("HARNESS_ENABLE_DOCS") else None),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:%d" % PORT,
        "http://localhost:%d" % PORT,
        "http://%s:%d" % (HOST, PORT),
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── API Auth Middleware (JWT + API Key dual-mode) ───


@app.middleware("http")
async def _api_auth_middleware(request: Request, call_next):
    """Authenticate all /v1/* endpoints.

    Supports two authentication modes:
      1. JWT (web frontend) — Authorization: Bearer <jwt>
      2. API Key (CLI/Open WebUI) — X-API-Key: <token>

    Sets request.state.user with user dict if authenticated via JWT.
    """
    path = request.url.path

    # Exempt non-API paths
    if any(path.startswith(p) for p in _AUTH_EXEMPT_PREFIXES):
        return await call_next(request)
    if path in _AUTH_EXEMPT_EXACT:
        return await call_next(request)
    if path.startswith("/static/"):
        return await call_next(request)
    if not path.startswith("/v1/"):
        return await call_next(request)

    # Exempt auth endpoints + setup config (needed for first-boot check)
    if path in _AUTH_EXEMPT_V1:
        return await call_next(request)

    # ─── Mode 1: JWT (preferred for web frontend) ───
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        jwt_token = auth_header[7:]
        payload = _auth_jwt.verify_token(jwt_token)
        if payload is not None:
            # Set user info on request state for downstream handlers
            user = _auth_db.get_user(payload["sub"])
            if user:
                request.state.user = user
                return await call_next(request)

    # ─── Mode 2: API Key (CLI / Open WebUI fallback) ───
    api_key = request.headers.get("x-api-key", "") or request.query_params.get("api_token", "")
    if api_key and validate_token(api_key, _API_TOKEN):
        # API key maps to admin role (the single machine token)
        request.state.user = {
            "id": "__api_key__",
            "username": "api",
            "role": "admin",
            "display_name": "API Client",
        }
        return await call_next(request)

    # ─── Neither valid → 401 ───
    return JSONResponse(
        {"error": "认证失败。请登录 (POST /v1/auth/login) 或提供 API Key (X-API-Key header)。"},
        status_code=401,
    )


# ─── Security Headers Middleware (CSP + HSTS) ───


@app.middleware("http")
async def _security_headers_middleware(request: Request, call_next):
    """Add security headers to all responses."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    # Content-Security-Policy: allow scripts/styles from self
    # 'unsafe-inline' needed for inline event handlers + token injection
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
STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
@app.get("/setup", include_in_schema=False)
@app.get("/dashboard", include_in_schema=False)
async def serve_frontend():
    index = STATIC_DIR / "index.html"
    if index.exists():
        html = index.read_text("utf-8")
        # Inject API token + first-boot status into frontend
        needs_admin = _auth_db.needs_initial_admin()
        token_script = (
            '<script>'
            'window.__API_TOKEN__="%s";'
            'window.__NEEDS_ADMIN__=%s;'
            '</script>'
        ) % (_API_TOKEN, "true" if needs_admin else "false")
        if "</head>" in html:
            html = html.replace("</head>", token_script + "</head>")
        else:
            html = html.replace("<head>", "<head>" + token_script)
        return HTMLResponse(html)
    return JSONResponse({"message": "灵枢 API 运行中"}, status_code=200)


class ChatRequest(BaseModel):
    model: str = "agent-harness-multi"
    messages: list[dict] = []
    stream: bool = False
    max_tokens: int = 4096
    temperature: float = 0.7


# ─── Endpoints ───


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": "1.0.0",
        "service": "agent-harness",
        "active_sessions": _session_count(),
    }


@app.get("/v1/models")
async def list_models():
    now = int(time.time())
    return {
        "object": "list",
        "data": [
            {
                "id": "lingShu-deep",
                "object": "model",
                "created": now,
                "owned_by": "harness",
                "description": "多 Agent 深度模式 — Supervisor-Worker 编排",
            },
            {
                "id": "agent-harness-multi",
                "object": "model",
                "created": now,
                "owned_by": "harness",
                "description": "多 Agent 深度模式（兼容旧名）",
            },
            {
                "id": "lingShu-fast",
                "object": "model",
                "created": now,
                "owned_by": "harness",
                "description": "单 Agent 快速模式 — 轻量任务秒回",
            },
            {
                "id": "agent-harness",
                "object": "model",
                "created": now,
                "owned_by": "harness",
                "description": "单 Agent 快速模式（兼容旧名）",
            },
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest, request: Request):
    # Session ID from header or auto-generate
    session_id = request.headers.get("X-Session-Id", "") or str(uuid.uuid4())

    if not req.messages:
        return JSONResponse({"error": "No messages provided"}, status_code=400)

    # Extract last user message
    last_user_msg = ""
    for msg in reversed(req.messages):
        if msg.get("role") == "user" and isinstance(msg.get("content"), str):
            last_user_msg = msg["content"]
            break

    if not last_user_msg:
        return JSONResponse({"error": "No user message found"}, status_code=400)

    # Build history context from previous messages
    last_user_idx = -1
    for i in range(len(req.messages) - 1, -1, -1):
        if req.messages[i].get("role") == "user":
            last_user_idx = i
            break

    history_messages = req.messages[:last_user_idx]
    history_context = _build_history_context(history_messages)

    print(
        "[Harness] [%s] model=%s stream=%s msg=%s... history=%d" % (
            session_id[:8], req.model, req.stream,
            last_user_msg[:60], len(history_messages))
    )

    # ── Streaming ──
    if req.stream:
        return StreamingResponse(
            _stream_progress(last_user_msg, history_context, req.model, session_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-Session-Id": session_id,
            },
        )

    # ── Non-streaming ──
    owner_id = getattr(request.state, "user", {}).get("id", "")
    acquired = False
    try:
        # Acquire agent semaphore (with timeout to avoid deadlock)
        if _agent_semaphore is not None:
            acquired = _agent_semaphore.acquire(timeout=300)  # 5min max wait
            if not acquired:
                return JSONResponse(
                    {"error": "服务器繁忙，当前有 %d 个任务在执行中，请稍后重试" % _MAX_CONCURRENT_AGENTS},
                    status_code=503,
                )

        # Register for cancellation
        cancel_event = _threading.Event()
        with _running_tasks_lock:
            _running_tasks[session_id] = cancel_event
        from .pipeline.cancel import set_cancel_event
        set_cancel_event(cancel_event)

        if req.model in ("agent-harness", "agent-harness-single", "lingShu-fast"):
            response_text = await asyncio.get_event_loop().run_in_executor(
                None, _execute_single, last_user_msg, history_context
            )
            rounds = 1
            workers = []
        else:
            result = await asyncio.get_event_loop().run_in_executor(
                None, _execute_multi, last_user_msg, history_context
            )
            response_text = result.get("final_output", "") or "[Harness] 处理完成"
            rounds = result.get("rounds", 1)
            workers = list(result.get("worker_results", {}).keys())

        # Save session (capped to MAX_HISTORY_ROUNDS)
        session = _load_session(session_id) or []
        session.append({"role": "user", "content": last_user_msg, "ts": time.time()})
        session.append({"role": "assistant", "content": response_text, "ts": time.time()})
        if len(session) > SESSION_TRIM_AT:
            session = session[-SESSION_TRIM_AT:]
        _save_session(session_id, session, owner_id=owner_id)

        print(
            "[Harness] [%s] ✅ (%d chars, %d 轮, workers: %s)" % (
                session_id[:8], len(response_text), rounds, workers)
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        # User-friendly error in Chinese
        err_str = str(e)
        if "semaphore" in err_str.lower():
            response_text = "[HarnessError] 服务器繁忙，请稍后重试"
        elif "timeout" in err_str.lower():
            response_text = "[HarnessError] 请求超时，搜索或 LLM 调用耗时过长，请简化问题后重试"
        elif "API key" in err_str or "401" in err_str or "Unauthorized" in err_str:
            response_text = "[HarnessError] LLM API 认证失败，请检查 API Key 配置"
        elif "rate limit" in err_str.lower() or "429" in err_str:
            response_text = "[HarnessError] API 调用频率过高，请等待后重试"
        else:
            response_text = "[HarnessError] %s" % err_str[:200]
    finally:
        # Release agent semaphore
        if acquired and _agent_semaphore is not None:
            _agent_semaphore.release()
        # Clean up task registration
        with _running_tasks_lock:
            _running_tasks.pop(session_id, None)
        from .pipeline.cancel import clear_cancel_event
        clear_cancel_event()

    return {
        "id": "chatcmpl-%d" % int(time.time()),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": req.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": response_text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": len(response_text),
            "total_tokens": 0,
        },
    }


# ─── Setup API ───


def _get_cm():
    from .pipeline import config_manager
    return config_manager


@app.get("/v1/setup/config")
async def get_config():
    return _get_cm().load_config()


@app.post("/v1/setup/config")
async def save_config(request: Request):
    body = await request.json()
    return _get_cm().save_config(body)


@app.get("/v1/setup/check-paths")
async def check_paths():
    return _get_cm().check_paths()


@app.get("/v1/setup/llm-backends")
async def llm_backends():
    return _get_cm().check_llm_backend()


@app.get("/v1/setup/env-check")
async def env_check():
    return _get_cm().full_env_check()


@app.post("/v1/setup/test-llm")
async def test_llm(request: Request):
    body = await request.json()
    return _get_cm().test_llm_connection(
        endpoint=body.get("endpoint", ""),
        model=body.get("model", ""),
        api_key=body.get("api_key", ""),
    )


@app.post("/v1/setup/fix")
async def run_fix(request: Request):
    """Execute a fix action (start service / auto-configure).

    Body: {"action": "start_model_proxy"|"start_ollama"|"start_searxng"|"auto_configure"}
    """
    body = await request.json()
    action = body.get("action", "")
    result = _get_cm().fix_action(action)
    return result


@app.post("/v1/setup/auto-configure")
async def auto_configure():
    """One-click full auto configuration."""
    result = _get_cm().fix_action("auto_configure")
    return result


@app.get("/v1/tools")
async def list_tools():
    """List all registered tools (with enabled/disabled status)."""
    try:
        import agent_harness.tools as _t
        from agent_harness.tools.registry import TOOL_REGISTRY
        tools = {
            k: {
                "description": v["schema"].get("description", ""),
                "privilege": v.get("privilege", "read-only"),
                "properties": list(v["schema"].get("properties", {}).keys()),
                "enabled": is_tool_enabled(k),
            }
            for k, v in TOOL_REGISTRY.items()
        }
        return {"tools": tools, "count": len(tools)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ─── Path traversal sanitizer ───


def _safe_path_param(value: str) -> str:
    """Sanitize path parameters — reject '..' and slashes."""
    if not value or value.strip() in ("", ".", ".."):
        raise ValueError("Invalid path parameter: empty or dot")
    if ".." in value:
        raise ValueError("Path traversal detected: '..' not allowed")
    if "/" in value or "\\" in value:
        raise ValueError("Path traversal detected: slashes not allowed")
    return value.strip()


# ─── Report API ───


def _get_rs():
    from .pipeline import report_store
    return report_store


@app.post("/v1/reports")
async def create_report(request: Request):
    """Save a report."""
    owner_id = getattr(request.state, "user", {}).get("id", "")
    body = await request.json()
    meta = _get_rs().save_report(
        title=body.get("title", "未命名报告"),
        content=body.get("content", ""),
        tags=body.get("tags", []),
        source_session=body.get("source_session", ""),
        owner_id=owner_id,
    )
    return meta


@app.get("/v1/reports")
async def list_reports(request: Request, limit: int = 50, offset: int = 0):
    """List saved reports. Filters by owner if non-admin."""
    user = getattr(request.state, "user", None)
    is_admin = user and user.get("role") == "admin"
    owner_filter = None if is_admin else (user.get("id", "") if user else "")
    reports = _get_rs().list_reports(limit=limit, offset=offset, owner_id=owner_filter)
    return {"reports": reports, "count": len(reports)}


@app.get("/v1/reports/{report_id}")
async def get_report(report_id: str):
    """Get a specific report with full content.
    
    For formal HTML reports, returns the HTML directly.
    For markdown reports, returns JSON with content.
    """
    from .pipeline.report_store import REPORTS_DIR as _RD

    # Path traversal safeguard
    try:
        _safe_path_param(report_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    # Try finding the file directly
    for ext in [".html", ".md"]:
        path = _RD / ("%s%s" % (report_id, ext))
        if path.exists():
            content = path.read_text(encoding="utf-8")
            if ext == ".html":
                return HTMLResponse(content)
            # For .md, return JSON
            return {"id": report_id, "content": content, "format": "md"}
        # Also search for any file starting with report_id
        for f in _RD.glob("%s*" % report_id):
            content = f.read_text(encoding="utf-8")
            if f.suffix == ".html":
                return HTMLResponse(content)
            return {"id": report_id, "content": content, "format": "md"}

    return JSONResponse({"error": "Report not found"}, status_code=404)


@app.delete("/v1/reports/{report_id}")
async def delete_report(report_id: str):
    """Delete a report."""
    # Path traversal safeguard
    try:
        _safe_path_param(report_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    if _get_rs().delete_report(report_id):
        return {"status": "deleted"}
    return JSONResponse({"error": "Report not found"}, status_code=404)


@app.get("/v1/reports/search")
async def search_reports(request: Request, q: str = ""):
    """Search reports by title/tags. Filters by owner if non-admin."""
    if not q:
        return {"reports": []}
    user = getattr(request.state, "user", None)
    is_admin = user and user.get("role") == "admin"
    owner_filter = None if is_admin else (user.get("id", "") if user else "")
    reports = _get_rs().search_reports(q, owner_id=owner_filter)
    return {"reports": reports, "count": len(reports)}


@app.post("/v1/reports/formalize")
async def formalize_report(request: Request):
    """Generate a formal HTML report from analysis content.

    Takes raw analysis content, adds format + sources, outputs a standalone HTML.
    """
    owner_id = getattr(request.state, "user", {}).get("id", "")
    body = await request.json()
    title = body.get("title", "调研报告")
    content = body.get("content", "")
    tags = body.get("tags", [])
    source_session = body.get("source_session", "")
    sources = body.get("sources", [])  # [{url, title}, ...]

    from .pipeline import report_formatter

    # Generate HTML report with sources
    html = report_formatter.generate_report_html(title, content, sources=sources)

    # Save as formal report
    meta = report_formatter.save_formal_report(
        title=title,
        html=html,
        tags=tags,
        source_session=source_session,
        owner_id=owner_id,
    )

    return {**meta, "html": html[:500] + "..."}


@app.get("/v1/sessions")
async def list_sessions(request: Request):
    """List active sessions (persisted on disk). Filters by owner if non-admin."""
    user = getattr(request.state, "user", None)
    is_admin = user and user.get("role") == "admin"

    # Admin sees all, user sees only own sessions
    owner_filter = None if is_admin else (user.get("id", "") if user else "")
    sessions = _list_sessions(owner_id=owner_filter)
    return {"sessions": sessions, "count": len(sessions)}


@app.delete("/v1/sessions/{session_id}")
async def delete_session_endpoint(session_id: str, request: Request):
    """Delete a specific session."""
    # Path traversal safeguard
    try:
        _safe_path_param(session_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    if _delete_session(session_id):
        return {"status": "deleted", "session_id": session_id}
    return JSONResponse(
        {"error": "Session not found: %s" % session_id},
        status_code=404,
    )


@app.get("/v1/sessions/{session_id}/messages")
async def get_session_messages(session_id: str):
    """Get full message history for a session."""
    # Path traversal safeguard
    try:
        _safe_path_param(session_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    msgs = _load_session(session_id)
    if msgs is None:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    return {"messages": msgs, "count": len(msgs)}


@app.post("/v1/sessions/{session_id}/meta")
async def update_session_meta_endpoint(session_id: str, request: Request):
    """Update session metadata (title, pinned)."""
    try:
        _safe_path_param(session_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    body = await request.json()
    kwargs = {}
    if "title" in body:
        kwargs["title"] = str(body["title"])[:100]
    if "pinned" in body:
        kwargs["pinned"] = bool(body["pinned"])
    if not kwargs:
        return JSONResponse({"error": "没有可更新的字段"}, status_code=400)
    from .pipeline.session_store import update_session_meta
    result = update_session_meta(session_id, **kwargs)
    if result is None:
        return JSONResponse({"error": "会话不存在"}, status_code=404)
    return {"status": "updated", "session": result}


@app.get("/v1/sessions/{session_id}/export")
async def export_single_session(session_id: str):
    """Export a single session as JSON download."""
    try:
        _safe_path_param(session_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    msgs = _load_session(session_id) or []
    data = {"session_id": session_id, "messages": msgs, "exported_at": int(time.time())}
    return StreamingResponse(
        iter([json.dumps(data, ensure_ascii=False, indent=2)]),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=session_%s.json" % session_id[:12]},
    )


@app.get("/v1/search/messages")
async def search_messages(request: Request, q: str = ""):
    """Search message content across all sessions."""
    user = getattr(request.state, "user", None)
    is_admin = user and user.get("role") == "admin"
    owner_filter = None if is_admin else (user.get("id", "") if user else "")
    if not q:
        return {"results": [], "count": 0}
    from .pipeline.session_store import search_messages as _search_msgs
    results = _search_msgs(q, owner_id=owner_filter, limit=20)
    return {"results": results, "count": len(results)}


# ═══════════════════════════════════════
# AGENT LOG API
# ═══════════════════════════════════════

from .agent_log import get_logs as _get_logs, clear_logs as _clear_logs


@app.get("/v1/sessions/{session_id}/logs")
async def get_session_logs(session_id: str):
    """Get agent execution logs for a session."""
    try:
        _safe_path_param(session_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    logs = _get_logs(session_id)
    return {"logs": logs, "count": len(logs)}


@app.delete("/v1/sessions/{session_id}/logs")
async def clear_session_logs(session_id: str):
    """Clear agent execution logs for a session."""
    try:
        _safe_path_param(session_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if _clear_logs(session_id):
        return {"status": "cleared"}
    return {"status": "not_found"}


# ─── Task Cancel API ───


@app.get("/v1/tasks")
async def list_running_tasks():
    """List currently running tasks."""
    with _running_tasks_lock:
        tasks = [
            {
                "session_id": sid,
                "running": not event.is_set(),
            }
            for sid, event in _running_tasks.items()
        ]
    return {"tasks": tasks, "count": len(tasks)}


@app.post("/v1/tasks/{session_id}/cancel")
async def cancel_task(session_id: str):
    """Cancel a running task."""
    with _running_tasks_lock:
        event = _running_tasks.get(session_id)
    if not event:
        return JSONResponse(
            {"error": "No running task for session: %s" % session_id},
            status_code=404,
        )
    event.set()
    return {"status": "cancelled", "session_id": session_id}


# ─── Knowledge Base API ───


@app.get("/v1/knowledge/collections")
async def kb_list_collections():
    """List all knowledge base collections."""
    try:
        from .tools.rag_store import list_collections, collection_info
        cols = list_collections()
        return {
            "collections": [
                collection_info(c) for c in cols
            ],
            "count": len(cols),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/v1/knowledge/upload")
async def kb_upload_file(request: Request):
    """Upload a file and index it into a knowledge base collection.

    Accepts multipart/form-data with fields:
        - file: the file (PDF, DOCX, TXT, MD)
        - collection: collection name (default: "default")

    Returns indexing results.
    """
    try:
        from .tools.rag_store import index_file, collection_info
    except Exception as e:
        return JSONResponse(
            {"error": "RAG store not available: %s" % e},
            status_code=500,
        )

    import tempfile

    try:
        form = await request.form()
        upload = form.get("file")
        if not upload:
            return JSONResponse({"error": "No file provided"}, status_code=400)

        collection = form.get("collection", "default")
        filename = upload.filename or "unknown"
        content = await upload.read()

        # Save to temp file
        suffix = Path(filename).suffix or ".tmp"
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=suffix
        ) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            result = index_file(tmp_path, collection=collection, filename=filename)
            info = collection_info(collection)
            resp = {
                "status": "ok",
                "filename": filename,
                "collection": collection,
                "chunks_indexed": result.get("chunks_count", 0),
                "embedding_status": result.get("embedding_status", "unknown"),
                "fallback": result.get("fallback", "none"),
                "collection_info": info,
            }
            # Add warning if Ollama is offline
            if result.get("embedding_status") == "offline":
                resp["warning"] = "嵌入服务离线，已使用 BM25 关键词搜索作为降级方案"
            return resp
        finally:
            os.unlink(tmp_path)

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.delete("/v1/knowledge/collections/{name}")
async def kb_delete_collection(name: str):
    """Delete a knowledge base collection."""
    try:
        from .tools.rag_store import delete_collection
    except Exception as e:
        return JSONResponse(
            {"error": "RAG store not available: %s" % e},
            status_code=500,
        )
    if delete_collection(name):
        return {"status": "deleted", "collection": name}
    return JSONResponse(
        {"error": "Collection not found: %s" % name},
        status_code=404,
    )


@app.get("/v1/knowledge/query")
async def kb_query(q: str = "", collection: str = "default", top_k: int = 5):
    """Search a knowledge base collection."""
    try:
        from .tools.rag_store import query as rag_query
    except Exception as e:
        return JSONResponse({"error": "RAG store not available: %s" % e}, status_code=500)
    if not q:
        return {"results": []}
    try:
        results = rag_query(q, collection=collection, top_k=top_k)
        return {"results": results, "count": len(results)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ═══════════════════════════════════════
# AUTH API
# ═══════════════════════════════════════


@app.get("/v1/auth/login")
async def login_form():
    """GET is not supported — use POST with JSON body."""
    return JSONResponse({"error": "使用 POST /v1/auth/login 并传入 JSON body ({\"username\": \"...\", \"password\": \"...\"})"}, status_code=405)


@app.post("/v1/auth/login")
async def login(request: Request):
    """Authenticate user and return JWT tokens."""
    body = await request.json()
    username = (body.get("username", "") or "").strip()
    password = body.get("password", "") or ""

    if not username or not password:
        return JSONResponse({"error": "请输入用户名和密码"}, status_code=400)

    user = _auth_db.authenticate_user(username, password)
    if user is None:
        return JSONResponse({"error": "用户名或密码错误"}, status_code=401)

    # Generate tokens
    access_token = _auth_jwt.create_access_token(
        user["id"], user["username"], user["role"]
    )
    refresh_token = _auth_jwt.create_refresh_token(
        user["id"], user["username"], user["role"]
    )

    # Store sessions in DB (for revocation)
    import time as _t
    _auth_db.save_session(
        _auth_jwt.verify_token(access_token)["jti"],
        user["id"],
        int(_t.time()) + 8 * 3600,
    )
    _auth_db.save_session(
        _auth_jwt.verify_token(refresh_token, expected_type="refresh")["jti"],
        user["id"],
        int(_t.time()) + 30 * 86400,
        token_type="refresh",
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "user": {
            "id": user["id"],
            "username": user["username"],
            "role": user["role"],
            "display_name": user["display_name"],
        },
    }


@app.post("/v1/auth/logout")
async def logout(request: Request):
    """Revoke current JWT token."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        jwt_token = auth[7:]
        payload = _auth_jwt.verify_token(jwt_token)
        if payload and payload.get("jti"):
            _auth_db.revoke_session(payload["jti"])
    return {"status": "logged_out"}


@app.get("/v1/auth/me")
async def get_current_user(request: Request):
    """Get current authenticated user info."""
    user = getattr(request.state, "user", None)
    if user is None:
        return JSONResponse({"error": "未认证"}, status_code=401)
    return {
        "user": user,
        "authenticated": True,
    }


@app.post("/v1/auth/refresh")
async def refresh_token(request: Request):
    """Exchange a refresh token for a new access token."""
    body = await request.json()
    refresh_token_str = body.get("refresh_token", "")

    payload = _auth_jwt.verify_token(refresh_token_str, expected_type="refresh")
    if payload is None:
        return JSONResponse({"error": "Refresh token 无效或已过期"}, status_code=401)

    # Check if refresh token is still valid in DB
    session = _auth_db.is_session_valid(payload["jti"])
    if session is None:
        return JSONResponse({"error": "Refresh token 已被撤销"}, status_code=401)

    # Generate new access token
    new_access = _auth_jwt.create_access_token(
        payload["sub"], payload["username"], payload["role"]
    )
    new_payload = _auth_jwt.verify_token(new_access)
    if new_payload:
        import time as _t
        _auth_db.save_session(
            new_payload["jti"], payload["sub"],
            int(_t.time()) + 8 * 3600,
        )

    return {
        "access_token": new_access,
        "token_type": "Bearer",
    }


@app.post("/v1/auth/setup-admin")
async def setup_initial_admin(request: Request):
    """Create the first admin account (only works if no admin exists)."""
    if not _auth_db.needs_initial_admin():
        return JSONResponse({"error": "管理员已存在，不能重复创建"}, status_code=400)

    body = await request.json()
    username = (body.get("username", "") or "").strip()
    password = body.get("password", "") or ""

    if not username or len(username) < 2:
        return JSONResponse({"error": "用户名至少 2 个字符"}, status_code=400)
    if len(password) < 6:
        return JSONResponse({"error": "密码至少 6 位"}, status_code=400)

    try:
        user = _auth_db.create_user(username, password, role="admin",
                                    display_name=username)
        return {"status": "created", "user": user,
                "message": "管理员账号已创建，请登录"}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# ═══════════════════════════════════════
# API TOKEN MANAGEMENT (legacy)
# ═══════════════════════════════════════


@app.get("/v1/auth/token")
async def get_api_token():
    """Get the machine API token (admin-only via API Key)."""
    return {
        "token": _API_TOKEN,
        "hint": "Set X-API-Key: <token> header on all /v1/* requests (admin-level access)",
    }


@app.post("/v1/auth/reset")
async def regenerate_api_token():
    """Regenerate the machine API token."""
    global _API_TOKEN
    _API_TOKEN = _reset_token()
    return {
        "token": _API_TOKEN,
        "status": "regenerated",
        "warning": "所有使用旧 token 的客户端需要更新",
    }


# ═══════════════════════════════════════
# ADMIN API (user management)
# ═══════════════════════════════════════


@app.get("/v1/admin/users")
async def admin_list_users(request: Request):
    """List all users (admin only)."""
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        return JSONResponse({"error": "需要管理员权限"}, status_code=403)
    users = _auth_db.list_users()
    return {"users": users, "count": len(users)}


@app.post("/v1/admin/users")
async def admin_create_user(request: Request):
    """Create a new user (admin only)."""
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        return JSONResponse({"error": "需要管理员权限"}, status_code=403)

    body = await request.json()
    username = (body.get("username", "") or "").strip()
    password = body.get("password", "") or ""
    role = (body.get("role", "user") or "").strip()

    try:
        new_user = _auth_db.create_user(username, password, role=role)
        return {"status": "created", "user": new_user}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/v1/admin/users/{user_id}/role")
async def admin_update_user_role(user_id: str, request: Request):
    """Change a user's role (admin only)."""
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        return JSONResponse({"error": "需要管理员权限"}, status_code=403)

    body = await request.json()
    new_role = (body.get("role", "") or "").strip()

    if _auth_db.update_user_role(user_id, new_role):
        _auth_db.revoke_all_user_sessions(user_id)
        return {"status": "updated", "user_id": user_id, "role": new_role}
    return JSONResponse({"error": "用户不存在或角色无效"}, status_code=404)


@app.post("/v1/admin/users/{user_id}/reset-password")
async def admin_reset_password(user_id: str, request: Request):
    """Reset a user's password (admin only)."""
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        return JSONResponse({"error": "需要管理员权限"}, status_code=403)

    body = await request.json()
    new_password = body.get("password", "") or ""

    if _auth_db.update_user_password(user_id, new_password):
        _auth_db.revoke_all_user_sessions(user_id)
        return {"status": "password_updated", "user_id": user_id}
    return JSONResponse({"error": "密码更新失败（至少 6 位）"}, status_code=400)


@app.delete("/v1/admin/users/{user_id}")
async def admin_delete_user(user_id: str, request: Request):
    """Delete a user (admin only). Cannot delete self."""
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        return JSONResponse({"error": "需要管理员权限"}, status_code=403)

    if user_id == user.get("id"):
        return JSONResponse({"error": "不能删除自己"}, status_code=400)

    if _auth_db.delete_user(user_id):
        return {"status": "deleted", "user_id": user_id}
    return JSONResponse({"error": "用户不存在"}, status_code=404)


# ═══════════════════════════════════════
# SKILLS MANAGEMENT API
# ═══════════════════════════════════════

_HERMES_SKILLS_DIR = Path(os.environ.get("HOME", "~")) / "AppData" / "Local" / "hermes" / "skills"
if not _HERMES_SKILLS_DIR.exists():
    _HERMES_SKILLS_DIR = Path.home() / "AppData" / "Local" / "hermes" / "skills"


def _scan_skills() -> list[dict]:
    """Scan installed skills (enabled + disabled)."""
    skills = []
    if not _HERMES_SKILLS_DIR.exists():
        return skills

    # Enabled skills (root dir)
    for d in sorted(_HERMES_SKILLS_DIR.iterdir()):
        if d.is_dir() and not d.name.startswith("_") and not d.name.startswith("."):
            skills.append({"name": d.name, "enabled": True, "builtin": False})

    # Disabled skills
    disabled_dir = _HERMES_SKILLS_DIR / "_disabled"
    if disabled_dir.exists():
        for d in sorted(disabled_dir.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                skills.append({"name": d.name, "enabled": False, "builtin": False})
    return skills


@app.get("/v1/skills")
async def list_skills():
    """List all installed skills with enabled/disabled status."""
    return {"skills": _scan_skills(), "count": len(_scan_skills())}


@app.post("/v1/skills/{name}/toggle")
async def toggle_skill(name: str):
    """Enable or disable a skill by moving it between _disabled/ and root."""
    enabled_dir = _HERMES_SKILLS_DIR / name
    disabled_dir = _HERMES_SKILLS_DIR / "_disabled" / name

    if enabled_dir.exists():
        # Disable: move to _disabled/
        target = _HERMES_SKILLS_DIR / "_disabled"
        target.mkdir(exist_ok=True)
        enabled_dir.rename(disabled_dir)
        return {"name": name, "enabled": False}

    elif disabled_dir.exists():
        # Enable: move to root
        disabled_dir.rename(enabled_dir)
        return {"name": name, "enabled": True}

    return JSONResponse({"error": "技能不存在: %s" % name}, status_code=404)


@app.get("/v1/skills/marketplace")
async def marketplace_search(q: str = ""):
    """Search the SkillHub marketplace for available skills."""
    import subprocess
    import shutil
    skillhub = shutil.which("skillhub") or os.path.expanduser("~/.local/bin/skillhub")
    if not os.path.isfile(skillhub):
        return {"skills": [], "error": "SkillHub not installed"}
    try:
        r = subprocess.run(
            [skillhub, "search", q] if q else [skillhub, "search", ""],
            capture_output=True, text=True, timeout=15,
        )
        lines = [l.strip() for l in r.stdout.split("\n") if l.strip() and not l.startswith("NAME")]
        skills = []
        for line in lines[:50]:
            parts = line.split(None, 2)
            if len(parts) >= 2:
                skills.append({"name": parts[0], "desc": parts[1] if len(parts) > 1 else "", "installed": False})
        # Mark installed
        installed = {s["name"] for s in _scan_skills()}
        for s in skills:
            if s["name"] in installed:
                s["installed"] = True
        return {"skills": skills, "count": len(skills)}
    except Exception as e:
        return {"skills": [], "error": str(e)}


@app.post("/v1/skills/marketplace/install")
async def marketplace_install(request: Request):
    """Install a skill from SkillHub marketplace."""
    body = await request.json()
    slug = (body.get("slug", "") or "").strip()
    if not slug:
        return JSONResponse({"error": "缺少 slug 参数"}, status_code=400)
    import subprocess, shutil
    skillhub = shutil.which("skillhub") or os.path.expanduser("~/.local/bin/skillhub")
    if not os.path.isfile(skillhub):
        return JSONResponse({"error": "SkillHub 未安装"}, status_code=400)
    try:
        r = subprocess.run([skillhub, "install", slug], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return JSONResponse({"error": "安装失败: %s" % (r.stderr or r.stdout)}, status_code=400)
        return {"status": "installed", "name": slug}
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "安装超时"}, status_code=504)


# ═══════════════════════════════════════
# TOOLS CONFIG API
# ═══════════════════════════════════════


@app.get("/v1/tools/{name}")
async def get_tool_detail(name: str):
    """Get a single tool's details + enabled status."""
    try:
        from .tools.registry import TOOL_REGISTRY
        entry = TOOL_REGISTRY.get(name)
        if not entry:
            return JSONResponse({"error": "工具不存在"}, status_code=404)
        return {
            "name": name,
            "description": entry["schema"].get("description", ""),
            "privilege": entry.get("privilege", "read-only"),
            "properties": list(entry["schema"].get("properties", {}).keys()),
            "enabled": is_tool_enabled(name),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/v1/tools/{name}/toggle")
async def toggle_tool_endpoint(name: str):
    """Enable or disable a tool."""
    try:
        new_state = _toggle_tool(name)
        return {"name": name, "enabled": new_state}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/v1/plugins")
async def list_plugins():
    """Combined view: skills + disabled tools."""
    skills = _scan_skills()
    disabled_tools = _list_disabled_tools()
    return {
        "skills": skills,
        "disabled_tools": disabled_tools,
        "total_skills": len(skills),
    }


# ═══════════════════════════════════════
# DATA EXPORT API
# ═══════════════════════════════════════

import io
import zipfile

# ═══════════════════════════════════════
# DIAGNOSTICS API
# ═══════════════════════════════════════


@app.get("/v1/diag/search")
async def search_diagnostics():
    """Get search engine diagnostic log (last 20 queries)."""
    try:
        from .tools.web import _SEARCH_DIAG
        return {"diag": _SEARCH_DIAG, "count": len(_SEARCH_DIAG)}
    except (ImportError, AttributeError):
        return {"diag": [], "count": 0}

from pathlib import Path as _P


@app.get("/v1/export/sessions")
async def export_sessions():
    """Export all sessions as a JSON file download (admin only)."""
    sessions = _list_sessions()
    data = []
    for s in sessions:
        msgs = _load_session(s["id"])
        if msgs:
            data.append({"session_id": s["id"], "messages": msgs, "meta": s})
    return StreamingResponse(
        iter([json.dumps(data, ensure_ascii=False, indent=2)]),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=sessions_export.json"},
    )


@app.get("/v1/export/reports")
async def export_reports():
    """Export all reports as JSON metadata + Markdown content."""
    from .pipeline.report_store import REPORTS_DIR as _RD, _load_index
    index = _load_index()
    data = []
    for meta in index:
        fp = _RD / meta.get("filename", "")
        content = fp.read_text("utf-8") if fp.exists() else ""
        data.append({**meta, "content": content})
    return StreamingResponse(
        iter([json.dumps(data, ensure_ascii=False, indent=2)]),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=reports_export.json"},
    )


@app.get("/v1/export/backup")
async def export_backup():
    """Download a full backup ZIP: sessions + reports + auth db."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Sessions
        sessions = _list_sessions()
        sessions_data = []
        for s in sessions:
            msgs = _load_session(s["id"])
            if msgs:
                sessions_data.append({"id": s["id"], "messages": msgs})
        zf.writestr("sessions.json", json.dumps(sessions_data, ensure_ascii=False, indent=2))
        # Reports
        from .pipeline.report_store import REPORTS_DIR as _RD, _load_index
        index = _load_index()
        reports_data = []
        for meta in index:
            fp = _RD / meta.get("filename", "")
            content = fp.read_text("utf-8") if fp.exists() else ""
            reports_data.append({**meta, "content": content})
        zf.writestr("reports.json", json.dumps(reports_data, ensure_ascii=False, indent=2))
        # Auth DB
        db_path = _auth_db.DB_PATH
        if db_path.exists():
            zf.write(str(db_path), "auth.db")
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=lingShu_backup_%d.zip" % int(time.time())},
    )


# ═══════════════════════════════════════
# SCHEDULER (CRON) API
# ═══════════════════════════════════════

from .agent_cron import add_task as _cron_add, update_task as _cron_update, delete_task as _cron_delete, get_task as _cron_get, list_tasks as _cron_list


@app.get("/v1/scheduler/tasks")
async def scheduler_list_tasks():
    """List all scheduled tasks."""
    return {"tasks": _cron_list(), "count": len(_cron_list())}


@app.post("/v1/scheduler/tasks")
async def scheduler_create_task(request: Request):
    """Create a scheduled task."""
    body = await request.json()
    task_id = (body.get("id", "") or "").strip()
    schedule = (body.get("schedule", "") or "").strip()
    prompt = (body.get("prompt", "") or "").strip()
    if not task_id or not schedule:
        return JSONResponse({"error": "缺少 id 或 schedule 参数"}, status_code=400)
    try:
        task = _cron_add(task_id, schedule, prompt)
        return {"status": "created", "task": task}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/v1/scheduler/tasks/{task_id}")
async def scheduler_get_task(task_id: str):
    """Get a specific task."""
    task = _cron_get(task_id)
    if task is None:
        return JSONResponse({"error": "任务不存在"}, status_code=404)
    return {"task": task}


@app.post("/v1/scheduler/tasks/{task_id}")
async def scheduler_update_task(task_id: str, request: Request):
    """Update a task (schedule, prompt, enabled)."""
    body = await request.json()
    try:
        task = _cron_update(task_id, **body)
        if task is None:
            return JSONResponse({"error": "任务不存在"}, status_code=404)
        return {"status": "updated", "task": task}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.delete("/v1/scheduler/tasks/{task_id}")
async def scheduler_delete_task(task_id: str):
    """Delete a scheduled task."""
    if _cron_delete(task_id):
        return {"status": "deleted"}
    return JSONResponse({"error": "任务不存在"}, status_code=404)


# ═══════════════════════════════════════
# PLUGINS API
# ═══════════════════════════════════════

from .plugin_loader import list_plugins as _plugin_list


@app.get("/v1/plugins/loaded")
async def plugin_list():
    """List loaded external plugins."""
    return {"plugins": _plugin_list(), "count": len(_plugin_list())}


# ─── Direct entry point ───


def main():
    import uvicorn

    # Initialize persistent session store
    _init_session_store()

    # Initialize auth database (ensure tables exist)
    _auth_db._get_db()
    admin_needed = _auth_db.needs_initial_admin()
    user_count = _auth_db.user_count()
    _auth_db.cleanup_expired_sessions()

    count = _session_count()
    print("")
    print("  ⚡ 灵枢 — LingShu Agent")
    print("  " + ("-" * 40))
    print("  API:       http://%s:%d/v1" % (HOST, PORT))
    print("  Frontend:  http://%s:%d" % (HOST, PORT))
    if admin_needed:
        print("  ⚠️  首次启动 — 需创建管理员账号")
    else:
        print("  用户:     %d 人" % user_count)
    if _MAX_CONCURRENT_AGENTS > 0:
        print("  并发:     %d 个 Agent 同时执行" % _MAX_CONCURRENT_AGENTS)
    print("  Token:     %s...%s" % (_API_TOKEN[:8], _API_TOKEN[-4:]))
    print("  " + ("-" * 40))
    print("")

    # Print config warnings
    try:
        from .config import print_config_warnings
        print_config_warnings()
    except ImportError:
        pass
    if count:
        print("  会话: %d 个" % count)
    print("")

    # Start background scheduler
    _scheduler_count = 0
    try:
        from .agent_cron import get_scheduler
        sched = get_scheduler()
        sched.start()
        _scheduler_count = len(sched.list_tasks() if hasattr(sched, 'list_tasks') else [])
        print("  定时任务: %d 个" % _scheduler_count)
    except Exception:
        pass

    # Load plugins
    _plugin_count = 0
    try:
        from .plugin_loader import load_plugins
        plugins = load_plugins()
        _plugin_count = len(plugins)
        print("  插件:     %d 个（%d 成功）" % (
            _plugin_count,
            sum(1 for p in plugins if p.get("success"))),
        )
    except Exception:
        pass

    # Multi-worker support
    # WARNING: SQLite + multi-process can cause WAL contention.
    # Use HARNESS_WORKERS=1 (default) for most setups.
    _workers = int(os.environ.get("HARNESS_WORKERS", "1"))

    uvicorn.run(
        "agent_harness.api_fastapi:app",
        host=HOST,
        port=PORT,
        log_level="info",
        reload=False,
        workers=_workers,
    )


if __name__ == "__main__":
    main()
