"""Research API (灵枢) — routes for the AI research assistant."""
import asyncio
import json
import os
import queue
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from agent_harness import __version__
from agent_harness.core.auth import auth_db as _auth_db
from agent_harness.core.auth import auth_jwt as _auth_jwt
from agent_harness.core.auth.api_security import (
    load_or_generate_token,
    validate_token,
)
from agent_harness.core.auth.api_security import (
    reset_token as _reset_token,
)
from agent_harness.core.tools.tool_config import is_tool_enabled
from agent_harness.core.tools.tool_config import list_disabled as _list_disabled_tools
from agent_harness.core.tools.tool_config import toggle_tool as _toggle_tool
from agent_harness.core.pipeline.session_store import (
    delete_session as _delete_session,
)
from agent_harness.core.pipeline.session_store import (
    init_store as _init_session_store,
)
from agent_harness.core.pipeline.session_store import (
    list_sessions as _list_sessions,
)
from agent_harness.core.pipeline.session_store import (
    load_session as _load_session,
)
from agent_harness.core.pipeline.session_store import (
    save_session as _save_session,
)
from agent_harness.core.pipeline.session_store import (
    session_count as _session_count,
)
from agent_harness.core.agent_log import clear_logs as _clear_logs
from agent_harness.core.agent_log import get_logs as _get_logs
from agent_harness.core.agent_cron import add_task as _cron_add
from agent_harness.core.agent_cron import delete_task as _cron_delete
from agent_harness.core.agent_cron import get_task as _cron_get
from agent_harness.core.agent_cron import list_tasks as _cron_list
from agent_harness.core.agent_cron import update_task as _cron_update
from agent_harness.plugin_loader import list_plugins as _plugin_list

HOST = os.environ.get("HARNESS_API_HOST", "127.0.0.1")
PORT = int(os.environ.get("HARNESS_API_PORT", "8788"))

# ─── API Key (CLI / Open WebUI fallback) ───
_API_TOKEN: str = load_or_generate_token()

# ─── Rate limiter ───
_RATE_LIMIT_WINDOW = 60
_RATE_LIMIT_MAX = int(os.environ.get("HARNESS_RATE_LIMIT", "100"))
_RATE_LIMIT_ENABLED = os.environ.get("HARNESS_DISABLE_RATE_LIMIT", "").lower() not in ("1", "true", "yes")
_rate_limit_store: dict[str, list[float]] = {}

def _check_rate_limit(ip: str) -> bool:
    if not _RATE_LIMIT_ENABLED:
        return True
    now = time.time()
    window_start = now - _RATE_LIMIT_WINDOW
    timestamps = _rate_limit_store.get(ip, [])
    timestamps = [t for t in timestamps if t > window_start]
    if len(timestamps) >= _RATE_LIMIT_MAX:
        return False
    timestamps.append(now)
    _rate_limit_store[ip] = timestamps[-_RATE_LIMIT_MAX:]
    if len(_rate_limit_store) > 10000:
        for k in list(_rate_limit_store.keys()):
            _rate_limit_store[k] = [t for t in _rate_limit_store[k] if t > window_start]
            if not _rate_limit_store[k]:
                del _rate_limit_store[k]
    return True

_AUTH_EXEMPT_PREFIXES = ("/health",)
_AUTH_EXEMPT_EXACT = ("/", "/setup", "/dashboard")
_AUTH_EXEMPT_V1 = ("/v1/auth/login", "/v1/auth/refresh", "/v1/auth/setup-admin", "/v1/setup/config")

# ─── Token optimization: conversation window ───
MAX_HISTORY_ROUNDS = int(os.environ.get("HARNESS_MAX_HISTORY", "8"))
MAX_HISTORY_MSGS = MAX_HISTORY_ROUNDS * 2
SESSION_TRIM_AT = MAX_HISTORY_ROUNDS * 4

# ─── Running task registry (for cancellation) ───
_running_tasks: dict[str, threading.Event] = {}
_running_tasks_lock = threading.Lock()

# ─── Agent concurrency control ───
_MAX_CONCURRENT_AGENTS = int(os.environ.get("HARNESS_MAX_CONCURRENT_AGENTS", "5"))
_agent_semaphore = threading.Semaphore(_MAX_CONCURRENT_AGENTS) if _MAX_CONCURRENT_AGENTS > 0 else None

_LLM_UNREACHABLE_REPLY = (
    "⚠️ LLM 后端不可用，无法生成回复。\n\n"
    "请检查配置：\n"
    "- HARNESS_DEEPSEEK_API / HARNESS_CLOUD_KEY\n"
    "- 或 HARNESS_LLAMA_API（本地模型）\n\n"
    "详细配置说明：https://github.com/kisaragiy/lingShu"
)


def _build_history_context(messages: list[dict]) -> str:
    if not messages:
        return ""
    trimmed = messages[-(MAX_HISTORY_MSGS):]
    lines = []
    for m in trimmed:
        role = m.get("role", "unknown")
        content = m.get("content", "")
        if len(content) > 300:
            content = content[:300] + "..."
        lines.append(f"[{role}]: {content}")
    return "\n".join(lines[-16:])


def _sse_chunk(content: str, role: str = "") -> str:
    delta = {}
    if role:
        delta["role"] = role
    if content:
        delta["content"] = content
    obj = {
        "choices": [{"delta": delta, "index": 0, "finish_reason": None}]
    }
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _sse_done(result_text: str) -> str:
    usage = {"prompt_tokens": 0, "completion_tokens": len(result_text), "total_tokens": 0}
    obj = {
        "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}],
        "usage": usage,
    }
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\ndata: [DONE]\n\n"


# ─── Execute harness ───

def _execute_multi(prompt: str, history_context: str, session_id: str = "") -> dict:
    from agent_harness.core.graph.graph_multi import _progress_queue, run_multi_agent
    enhanced = prompt
    if history_context:
        enhanced = (
            f"以下是本对话的历史记录（越新的越靠前）:\n{history_context}\n\n"
            f"请基于以上对话上下文，回答用户当前的问题:\n{prompt}"
        )
    try:
        from agent_harness.core.tools.rag_store import list_collections
        from agent_harness.core.tools.rag_store import query as rag_query
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
                    "[知识库 - {}] {}".format(r.get("source", "unknown"), r["text"])
                    for r in kb_results[:3]
                )
                enhanced = (
                    f"以下是从知识库中检索到的相关信息（请优先使用这些信息回答）:\n{kb_context}\n\n"
                    f"用户问题: {enhanced}"
                )
                if _progress_queue:
                    _progress_queue.put({
                        "type": "progress",
                        "content": f"📚 从知识库中找到 {len(kb_results)} 条相关信息\\n"
                    })
    except Exception:
        pass
    return run_multi_agent(enhanced)


def _execute_single(prompt: str, history_context: str) -> str:
    from agent_harness.core.graph import run as run_single
    enhanced = prompt
    if history_context:
        enhanced = f"以下是对话历史:\n{history_context}\n\n当前问题: {prompt}"
    try:
        from agent_harness.core.tools.rag_store import list_collections
        from agent_harness.core.tools.rag_store import query as rag_query
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
                    "[知识库] {}".format(r["text"]) for r in kb_results[:2]
                )
                enhanced = f"知识库信息:\n{kb_context}\n\n{enhanced}"
    except Exception:
        pass
    return run_single(enhanced)


def _run_with_queue(prompt: str, history: str, model: str, q: queue.Queue, session_id: str = ""):
    try:
        from agent_harness.core.graph.graph_multi import clear_progress_queue, set_progress_queue
        from agent_harness.core.pipeline.cancel import clear_cancel_event, set_cancel_event
        set_progress_queue(q)
        if _agent_semaphore is not None:
            _agent_semaphore.acquire()
        if session_id:
            cancel_event = threading.Event()
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
                status_line = f"✅ {len(workers)} 个 Worker 完成 ({rounds} 轮): {', '.join(workers)}\\n"
                q.put({"type": "status", "content": status_line})
            if errors:
                q.put({"type": "status", "content": f"⚠️ {len(errors)} 个错误\\n"})
            final = result.get("final_output", "")
            if final:
                q.put({"type": "result", "content": final})
            else:
                q.put({"type": "result", "content": final or _LLM_UNREACHABLE_REPLY})
        q.put({"type": "done"})
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        q.put({"type": "error", "content": f"❌ 执行出错: {e}\n\n{tb[:500]}"})
        q.put({"type": "done"})
    finally:
        try:
            if _agent_semaphore is not None:
                _agent_semaphore.release()
            from agent_harness.core.graph.graph_multi import clear_progress_queue
            from agent_harness.core.pipeline.cancel import clear_cancel_event
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
    session = _load_session(session_id) or []
    session.append({"role": "user", "content": prompt, "ts": time.time()})
    session.append({"role": "assistant", "content": result_text, "ts": time.time()})
    _save_session(session_id, session)
    yield _sse_done(result_text)


# ─── FastAPI router ───

router = APIRouter()

class ChatRequest(BaseModel):
    model: str = "agent-harness-multi"
    messages: list[dict] = []
    stream: bool = False
    max_tokens: int = 4096
    temperature: float = 0.7


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "version": "1.0.0",
        "service": "agent-harness",
        "active_sessions": _session_count(),
    }


@router.get("/v1/models")
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


@router.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest, request: Request):
    session_id = request.headers.get("X-Session-Id", "") or str(uuid.uuid4())
    if not req.messages:
        return JSONResponse({"error": "No messages provided"}, status_code=400)
    last_user_msg = ""
    for msg in reversed(req.messages):
        if msg.get("role") == "user" and isinstance(msg.get("content"), str):
            last_user_msg = msg["content"]
            break
    if not last_user_msg:
        return JSONResponse({"error": "No user message found"}, status_code=400)
    last_user_idx = -1
    for i in range(len(req.messages) - 1, -1, -1):
        if req.messages[i].get("role") == "user":
            last_user_idx = i
            break
    history_messages = req.messages[:last_user_idx]
    history_context = _build_history_context(history_messages)
    print(f"[Harness] [{session_id[:8]}] model={req.model} stream={req.stream} msg={last_user_msg[:60]}... history={len(history_messages)}")

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

    owner_id = getattr(request.state, "user", {}).get("id", "")
    acquired = False
    try:
        if _agent_semaphore is not None:
            acquired = _agent_semaphore.acquire(timeout=300)
            if not acquired:
                return JSONResponse(
                    {"error": f"服务器繁忙，当前有 {_MAX_CONCURRENT_AGENTS} 个任务在执行中，请稍后重试"},
                    status_code=503,
                )
        cancel_event = threading.Event()
        with _running_tasks_lock:
            _running_tasks[session_id] = cancel_event
        from agent_harness.core.pipeline.cancel import set_cancel_event
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
            response_text = result.get("final_output", "") or _LLM_UNREACHABLE_REPLY
            rounds = result.get("rounds", 1)
            workers = list(result.get("worker_results", {}).keys())

        if not response_text or response_text.startswith("[Harness]") or response_text.startswith("[HarnessError]"):
            response_text = _LLM_UNREACHABLE_REPLY

        session = _load_session(session_id) or []
        session.append({"role": "user", "content": last_user_msg, "ts": time.time()})
        session.append({"role": "assistant", "content": response_text, "ts": time.time()})
        if len(session) > SESSION_TRIM_AT:
            session = session[-SESSION_TRIM_AT:]
        _save_session(session_id, session, owner_id=owner_id)

        print(f"[Harness] [{session_id[:8]}] ✅ ({len(response_text)} chars, {rounds} 轮, workers: {workers})")

    except Exception as e:
        import traceback
        traceback.print_exc()
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
            response_text = f"[HarnessError] {err_str[:200]}"
    finally:
        if acquired and _agent_semaphore is not None:
            _agent_semaphore.release()
        with _running_tasks_lock:
            _running_tasks.pop(session_id, None)
        from agent_harness.core.pipeline.cancel import clear_cancel_event
        clear_cancel_event()

    return {
        "id": f"chatcmpl-{int(time.time())}",
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
    from agent_harness.core.pipeline import config_manager
    return config_manager


@router.get("/v1/setup/config")
async def get_config():
    return _get_cm().load_config()


@router.post("/v1/setup/config")
async def save_config(request: Request):
    body = await request.json()
    return _get_cm().save_config(body)


@router.get("/v1/setup/check-paths")
async def check_paths():
    return _get_cm().check_paths()


@router.get("/v1/setup/llm-backends")
async def llm_backends():
    return _get_cm().check_llm_backend()


@router.get("/v1/setup/env-check")
async def env_check():
    return _get_cm().full_env_check()


@router.post("/v1/setup/test-llm")
async def test_llm(request: Request):
    body = await request.json()
    return _get_cm().test_llm_connection(
        endpoint=body.get("endpoint", ""),
        model=body.get("model", ""),
        api_key=body.get("api_key", ""),
    )


@router.post("/v1/setup/fix")
async def run_fix(request: Request):
    body = await request.json()
    action = body.get("action", "")
    result = _get_cm().fix_action(action)
    return result


@router.post("/v1/setup/auto-configure")
async def auto_configure():
    result = _get_cm().fix_action("auto_configure")
    return result


# ─── Tools API ───

@router.get("/v1/tools")
async def list_tools():
    try:
        from agent_harness.core.tools.registry import TOOL_REGISTRY
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
    if not value or value.strip() in ("", ".", ".."):
        raise ValueError("Invalid path parameter: empty or dot")
    if ".." in value:
        raise ValueError("Path traversal detected: '..' not allowed")
    if "/" in value or "\\" in value:
        raise ValueError("Path traversal detected: slashes not allowed")
    return value.strip()


# ─── Report API ───

def _get_rs():
    from agent_harness.apps.research.pipeline import report_store
    return report_store


@router.post("/v1/reports")
async def create_report(request: Request):
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


@router.get("/v1/reports")
async def list_reports(request: Request, limit: int = 50, offset: int = 0):
    user = getattr(request.state, "user", None)
    is_admin = user and user.get("role") == "admin"
    owner_filter = None if is_admin else (user.get("id", "") if user else "")
    reports = _get_rs().list_reports(limit=limit, offset=offset, owner_id=owner_filter)
    return {"reports": reports, "count": len(reports)}


@router.get("/v1/reports/{report_id}")
async def get_report(report_id: str):
    from agent_harness.apps.research.pipeline.report_store import REPORTS_DIR as _RD
    try:
        _safe_path_param(report_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    for ext in [".html", ".md"]:
        path = _RD / (f"{report_id}{ext}")
        if path.exists():
            content = path.read_text(encoding="utf-8")
            if ext == ".html":
                return HTMLResponse(content)
            return {"id": report_id, "content": content, "format": "md"}
        for f in _RD.glob(f"{report_id}*"):
            content = f.read_text(encoding="utf-8")
            if f.suffix == ".html":
                return HTMLResponse(content)
            return {"id": report_id, "content": content, "format": "md"}
    return JSONResponse({"error": "Report not found"}, status_code=404)


@router.delete("/v1/reports/{report_id}")
async def delete_report(report_id: str):
    try:
        _safe_path_param(report_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if _get_rs().delete_report(report_id):
        return {"status": "deleted"}
    return JSONResponse({"error": "Report not found"}, status_code=404)


@router.get("/v1/reports/search")
async def search_reports(request: Request, q: str = ""):
    if not q:
        return {"reports": []}
    user = getattr(request.state, "user", None)
    is_admin = user and user.get("role") == "admin"
    owner_filter = None if is_admin else (user.get("id", "") if user else "")
    reports = _get_rs().search_reports(q, owner_id=owner_filter)
    return {"reports": reports, "count": len(reports)}


@router.post("/v1/reports/formalize")
async def formalize_report(request: Request):
    owner_id = getattr(request.state, "user", {}).get("id", "")
    body = await request.json()
    title = body.get("title", "调研报告")
    content = body.get("content", "")
    tags = body.get("tags", [])
    source_session = body.get("source_session", "")
    sources = body.get("sources", [])
    from agent_harness.apps.research.pipeline import report_formatter
    html = report_formatter.generate_report_html(title, content, sources=sources)
    meta = report_formatter.save_formal_report(
        title=title,
        html=html,
        tags=tags,
        source_session=source_session,
        owner_id=owner_id,
    )
    return {**meta, "html": html[:500] + "..."}


# ─── Sessions API ───

@router.get("/v1/sessions")
async def list_sessions(request: Request):
    user = getattr(request.state, "user", None)
    is_admin = user and user.get("role") == "admin"
    owner_filter = None if is_admin else (user.get("id", "") if user else "")
    sessions = _list_sessions(owner_id=owner_filter)
    return {"sessions": sessions, "count": len(sessions)}


@router.delete("/v1/sessions/{session_id}")
async def delete_session_endpoint(session_id: str, request: Request):
    try:
        _safe_path_param(session_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if _delete_session(session_id):
        return {"status": "deleted", "session_id": session_id}
    return JSONResponse({"error": f"Session not found: {session_id}"}, status_code=404)


@router.get("/v1/sessions/{session_id}/messages")
async def get_session_messages(session_id: str):
    try:
        _safe_path_param(session_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    msgs = _load_session(session_id)
    if msgs is None:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    return {"messages": msgs, "count": len(msgs)}


@router.post("/v1/sessions/{session_id}/meta")
async def update_session_meta_endpoint(session_id: str, request: Request):
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
    from agent_harness.core.pipeline.session_store import update_session_meta
    result = update_session_meta(session_id, **kwargs)
    if result is None:
        return JSONResponse({"error": "会话不存在"}, status_code=404)
    return {"status": "updated", "session": result}


@router.get("/v1/sessions/{session_id}/export")
async def export_single_session(session_id: str):
    try:
        _safe_path_param(session_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    msgs = _load_session(session_id) or []
    data = {"session_id": session_id, "messages": msgs, "exported_at": int(time.time())}
    return StreamingResponse(
        iter([json.dumps(data, ensure_ascii=False, indent=2)]),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=session_{session_id[:12]}.json"},
    )


@router.get("/v1/search/messages")
async def search_messages(request: Request, q: str = ""):
    user = getattr(request.state, "user", None)
    is_admin = user and user.get("role") == "admin"
    owner_filter = None if is_admin else (user.get("id", "") if user else "")
    if not q:
        return {"results": [], "count": 0}
    from agent_harness.core.pipeline.session_store import search_messages as _search_msgs
    results = _search_msgs(q, owner_id=owner_filter, limit=20)
    return {"results": results, "count": len(results)}


# ─── Agent Log API ───

@router.get("/v1/sessions/{session_id}/logs")
async def get_session_logs(session_id: str):
    try:
        _safe_path_param(session_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    logs = _get_logs(session_id)
    return {"logs": logs, "count": len(logs)}


@router.delete("/v1/sessions/{session_id}/logs")
async def clear_session_logs(session_id: str):
    try:
        _safe_path_param(session_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if _clear_logs(session_id):
        return {"status": "cleared"}
    return {"status": "not_found"}


# ─── Task Cancel API ───

@router.get("/v1/tasks")
async def list_running_tasks():
    with _running_tasks_lock:
        tasks = [
            {
                "session_id": sid,
                "running": not event.is_set(),
            }
            for sid, event in _running_tasks.items()
        ]
    return {"tasks": tasks, "count": len(tasks)}


@router.post("/v1/tasks/{session_id}/cancel")
async def cancel_task(session_id: str):
    with _running_tasks_lock:
        event = _running_tasks.get(session_id)
    if not event:
        return JSONResponse(
            {"error": f"No running task for session: {session_id}"},
            status_code=404,
        )
    event.set()
    return {"status": "cancelled", "session_id": session_id}


# ─── Knowledge Base API ───

@router.get("/v1/knowledge/collections")
async def kb_list_collections():
    try:
        from agent_harness.core.tools.rag_store import collection_info, list_collections
        cols = list_collections()
        return {
            "collections": [collection_info(c) for c in cols],
            "count": len(cols),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/v1/knowledge/upload")
async def kb_upload_file(request: Request):
    try:
        from agent_harness.core.tools.rag_store import collection_info, index_file
    except Exception as e:
        return JSONResponse({"error": f"RAG store not available: {e}"}, status_code=500)
    import tempfile
    try:
        form = await request.form()
        upload = form.get("file")
        if not upload:
            return JSONResponse({"error": "No file provided"}, status_code=400)
        collection = form.get("collection", "default")
        filename = upload.filename or "unknown"
        content = await upload.read()
        suffix = Path(filename).suffix or ".tmp"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
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
            if result.get("embedding_status") == "offline":
                resp["warning"] = "嵌入服务离线，已使用 BM25 关键词搜索作为降级方案"
            return resp
        finally:
            os.unlink(tmp_path)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.delete("/v1/knowledge/collections/{name}")
async def kb_delete_collection(name: str):
    try:
        from agent_harness.core.tools.rag_store import delete_collection
    except Exception as e:
        return JSONResponse({"error": f"RAG store not available: {e}"}, status_code=500)
    if delete_collection(name):
        return {"status": "deleted", "collection": name}
    return JSONResponse({"error": f"Collection not found: {name}"}, status_code=404)


@router.get("/v1/knowledge/query")
async def kb_query(q: str = "", collection: str = "default", top_k: int = 5):
    try:
        from agent_harness.core.tools.rag_store import query as rag_query
    except Exception as e:
        return JSONResponse({"error": f"RAG store not available: {e}"}, status_code=500)
    if not q:
        return {"results": []}
    try:
        results = rag_query(q, collection=collection, top_k=top_k)
        return {"results": results, "count": len(results)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ─── Auth API ───

@router.get("/v1/auth/login")
async def login_form():
    return JSONResponse({"error": "使用 POST /v1/auth/login 并传入 JSON body ({\"username\": \"...\", \"password\": \"...\"})"}, status_code=405)


@router.post("/v1/auth/login")
async def login(request: Request):
    body = await request.json()
    username = (body.get("username", "") or "").strip()
    password = body.get("password", "") or ""
    if not username or not password:
        return JSONResponse({"error": "请输入用户名和密码"}, status_code=400)
    user = _auth_db.authenticate_user(username, password)
    if user is None:
        return JSONResponse({"error": "用户名或密码错误"}, status_code=401)
    access_token = _auth_jwt.create_access_token(user["id"], user["username"], user["role"])
    refresh_token = _auth_jwt.create_refresh_token(user["id"], user["username"], user["role"])
    import time as _t
    _auth_db.save_session(_auth_jwt.verify_token(access_token)["jti"], user["id"], int(_t.time()) + 8 * 3600)
    _auth_db.save_session(_auth_jwt.verify_token(refresh_token, expected_type="refresh")["jti"], user["id"], int(_t.time()) + 30 * 86400, token_type="refresh")
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


@router.post("/v1/auth/logout")
async def logout(request: Request):
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        jwt_token = auth[7:]
        payload = _auth_jwt.verify_token(jwt_token)
        if payload and payload.get("jti"):
            _auth_db.revoke_session(payload["jti"])
    return {"status": "logged_out"}


@router.get("/v1/auth/me")
async def get_current_user(request: Request):
    user = getattr(request.state, "user", None)
    if user is None:
        return JSONResponse({"error": "未认证"}, status_code=401)
    return {"user": user, "authenticated": True}


@router.post("/v1/auth/refresh")
async def refresh_token(request: Request):
    body = await request.json()
    refresh_token_str = body.get("refresh_token", "")
    payload = _auth_jwt.verify_token(refresh_token_str, expected_type="refresh")
    if payload is None:
        return JSONResponse({"error": "Refresh token 无效或已过期"}, status_code=401)
    session = _auth_db.is_session_valid(payload["jti"])
    if session is None:
        return JSONResponse({"error": "Refresh token 已被撤销"}, status_code=401)
    new_access = _auth_jwt.create_access_token(payload["sub"], payload["username"], payload["role"])
    new_payload = _auth_jwt.verify_token(new_access)
    if new_payload:
        import time as _t
        _auth_db.save_session(new_payload["jti"], payload["sub"], int(_t.time()) + 8 * 3600)
    return {"access_token": new_access, "token_type": "Bearer"}


@router.post("/v1/auth/setup-admin")
async def setup_initial_admin(request: Request):
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
        user = _auth_db.create_user(username, password, role="admin", display_name=username)
        return {"status": "created", "user": user, "message": "管理员账号已创建，请登录"}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# ─── API Token Management ───

@router.get("/v1/auth/token")
async def get_api_token():
    return {"token": _API_TOKEN, "hint": "Set X-API-Key: *** header on all /v1/* requests (admin-level access)"}


@router.post("/v1/auth/reset")
async def regenerate_api_token():
    global _API_TOKEN
    _API_TOKEN = _reset_token()
    return {"token": _API_TOKEN, "status": "regenerated", "warning": "所有使用旧 token 的客户端需要更新"}


# ─── Admin API ───

@router.get("/v1/admin/users")
async def admin_list_users(request: Request):
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        return JSONResponse({"error": "需要管理员权限"}, status_code=403)
    users = _auth_db.list_users()
    return {"users": users, "count": len(users)}


@router.post("/v1/admin/users")
async def admin_create_user(request: Request):
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


@router.post("/v1/admin/users/{user_id}/role")
async def admin_update_user_role(user_id: str, request: Request):
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        return JSONResponse({"error": "需要管理员权限"}, status_code=403)
    body = await request.json()
    new_role = (body.get("role", "") or "").strip()
    if _auth_db.update_user_role(user_id, new_role):
        _auth_db.revoke_all_user_sessions(user_id)
        return {"status": "updated", "user_id": user_id, "role": new_role}
    return JSONResponse({"error": "用户不存在或角色无效"}, status_code=404)


@router.post("/v1/admin/users/{user_id}/reset-password")
async def admin_reset_password(user_id: str, request: Request):
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        return JSONResponse({"error": "需要管理员权限"}, status_code=403)
    body = await request.json()
    new_password = body.get("password", "") or ""
    if _auth_db.update_user_password(user_id, new_password):
        _auth_db.revoke_all_user_sessions(user_id)
        return {"status": "password_updated", "user_id": user_id}
    return JSONResponse({"error": "密码更新失败（至少 6 位）"}, status_code=400)


@router.delete("/v1/admin/users/{user_id}")
async def admin_delete_user(user_id: str, request: Request):
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        return JSONResponse({"error": "需要管理员权限"}, status_code=403)
    if user_id == user.get("id"):
        return JSONResponse({"error": "不能删除自己"}, status_code=400)
    if _auth_db.delete_user(user_id):
        return {"status": "deleted", "user_id": user_id}
    return JSONResponse({"error": "用户不存在"}, status_code=404)


# ─── Skills Management API ───

_HERMES_SKILLS_DIR = Path(os.environ.get("HOME", "~")) / "AppData" / "Local" / "hermes" / "skills"
if not _HERMES_SKILLS_DIR.exists():
    _HERMES_SKILLS_DIR = Path.home() / "AppData" / "Local" / "hermes" / "skills"


def _scan_skills() -> list[dict]:
    skills = []
    if not _HERMES_SKILLS_DIR.exists():
        return skills
    for d in sorted(_HERMES_SKILLS_DIR.iterdir()):
        if d.is_dir() and not d.name.startswith("_") and not d.name.startswith("."):
            skills.append({"name": d.name, "enabled": True, "builtin": False})
    disabled_dir = _HERMES_SKILLS_DIR / "_disabled"
    if disabled_dir.exists():
        for d in sorted(disabled_dir.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                skills.append({"name": d.name, "enabled": False, "builtin": False})
    return skills


@router.get("/v1/skills")
async def list_skills():
    return {"skills": _scan_skills(), "count": len(_scan_skills())}


@router.post("/v1/skills/{name}/toggle")
async def toggle_skill(name: str):
    enabled_dir = _HERMES_SKILLS_DIR / name
    disabled_dir = _HERMES_SKILLS_DIR / "_disabled" / name
    if enabled_dir.exists():
        target = _HERMES_SKILLS_DIR / "_disabled"
        target.mkdir(exist_ok=True)
        enabled_dir.rename(disabled_dir)
        return {"name": name, "enabled": False}
    elif disabled_dir.exists():
        disabled_dir.rename(enabled_dir)
        return {"name": name, "enabled": True}
    return JSONResponse({"error": f"技能不存在: {name}"}, status_code=404)


@router.get("/v1/skills/marketplace")
async def marketplace_search(q: str = ""):
    import shutil, subprocess
    skillhub = shutil.which("skillhub") or os.path.expanduser("~/.local/bin/skillhub")
    if not os.path.isfile(skillhub):
        return {"skills": [], "error": "SkillHub not installed"}
    try:
        r = subprocess.run([skillhub, "search", q] if q else [skillhub, "search", ""], capture_output=True, text=True, timeout=15)
        lines = [line.strip() for line in r.stdout.split("\n") if line.strip() and not line.startswith("NAME")]
        skills = []
        for line in lines[:50]:
            parts = line.split(None, 2)
            if len(parts) >= 2:
                skills.append({"name": parts[0], "desc": parts[1] if len(parts) > 1 else "", "installed": False})
        installed = {s["name"] for s in _scan_skills()}
        for s in skills:
            if s["name"] in installed:
                s["installed"] = True
        return {"skills": skills, "count": len(skills)}
    except Exception as e:
        return {"skills": [], "error": str(e)}


@router.post("/v1/skills/marketplace/install")
async def marketplace_install(request: Request):
    body = await request.json()
    slug = (body.get("slug", "") or "").strip()
    if not slug:
        return JSONResponse({"error": "缺少 slug 参数"}, status_code=400)
    import shutil, subprocess
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


# ─── Tools Config API ───

@router.get("/v1/tools/{name}")
async def get_tool_detail(name: str):
    try:
        from agent_harness.core.tools.registry import TOOL_REGISTRY
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


@router.post("/v1/tools/{name}/toggle")
async def toggle_tool_endpoint(name: str):
    try:
        new_state = _toggle_tool(name)
        return {"name": name, "enabled": new_state}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/v1/plugins")
async def list_plugins():
    skills = _scan_skills()
    disabled_tools = _list_disabled_tools()
    return {"skills": skills, "disabled_tools": disabled_tools, "total_skills": len(skills)}


# ─── Diagnostics API ───

@router.get("/v1/diag/search")
async def search_diagnostics():
    try:
        from agent_harness.core.tools.web import _SEARCH_DIAG
        return {"diag": _SEARCH_DIAG, "count": len(_SEARCH_DIAG)}
    except (ImportError, AttributeError):
        return {"diag": [], "count": 0}


# ─── Data Export API ───

import io
import zipfile


@router.get("/v1/export/sessions")
async def export_sessions():
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


@router.get("/v1/export/reports")
async def export_reports():
    from agent_harness.apps.research.pipeline.report_store import REPORTS_DIR as _RD
    from agent_harness.apps.research.pipeline.report_store import _load_index
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


@router.get("/v1/export/backup")
async def export_backup():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        sessions = _list_sessions()
        sessions_data = []
        for s in sessions:
            msgs = _load_session(s["id"])
            if msgs:
                sessions_data.append({"id": s["id"], "messages": msgs})
        zf.writestr("sessions.json", json.dumps(sessions_data, ensure_ascii=False, indent=2))
        from agent_harness.apps.research.pipeline.report_store import REPORTS_DIR as _RD
        from agent_harness.apps.research.pipeline.report_store import _load_index
        index = _load_index()
        reports_data = []
        for meta in index:
            fp = _RD / meta.get("filename", "")
            content = fp.read_text("utf-8") if fp.exists() else ""
            reports_data.append({**meta, "content": content})
        zf.writestr("reports.json", json.dumps(reports_data, ensure_ascii=False, indent=2))
        db_path = _auth_db.DB_PATH
        if db_path.exists():
            zf.write(str(db_path), "auth.db")
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=lingShu_backup_{int(time.time())}.zip"},
    )


# ─── Scheduler (CRON) API ───

@router.get("/v1/scheduler/tasks")
async def scheduler_list_tasks():
    return {"tasks": _cron_list(), "count": len(_cron_list())}


@router.post("/v1/scheduler/tasks")
async def scheduler_create_task(request: Request):
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


@router.get("/v1/scheduler/tasks/{task_id}")
async def scheduler_get_task(task_id: str):
    task = _cron_get(task_id)
    if task is None:
        return JSONResponse({"error": "任务不存在"}, status_code=404)
    return {"task": task}


@router.post("/v1/scheduler/tasks/{task_id}")
async def scheduler_update_task(task_id: str, request: Request):
    body = await request.json()
    try:
        task = _cron_update(task_id, **body)
        if task is None:
            return JSONResponse({"error": "任务不存在"}, status_code=404)
        return {"status": "updated", "task": task}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.delete("/v1/scheduler/tasks/{task_id}")
async def scheduler_delete_task(task_id: str):
    if _cron_delete(task_id):
        return {"status": "deleted"}
    return JSONResponse({"error": "任务不存在"}, status_code=404)


# ─── Plugins API ───

@router.get("/v1/plugins/loaded")
async def plugin_list():
    return {"plugins": _plugin_list(), "count": len(_plugin_list())}
