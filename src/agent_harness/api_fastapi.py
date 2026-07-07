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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

HOST = os.environ.get("HARNESS_API_HOST", "127.0.0.1")
PORT = int(os.environ.get("HARNESS_API_PORT", "8788"))

# ─── Token optimization: conversation window ───
# 分析结论：80% 请求 ≤2轮，95% ≤4轮，99%+ ≤6轮
# 保留 4 轮（8 条消息）覆盖 95% 场景，平衡质量与成本
# 可通过环境变量 HARNESS_MAX_HISTORY 运行时调整（例: HARNESS_MAX_HISTORY=8 agent-harness serve）
MAX_HISTORY_ROUNDS = int(os.environ.get("HARNESS_MAX_HISTORY", "4"))
MAX_HISTORY_MSGS = MAX_HISTORY_ROUNDS * 2    # 每个 round = user + assistant
SESSION_TRIM_AT = MAX_HISTORY_ROUNDS * 4     # 持久化保留弹性

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
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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
        return FileResponse(str(index))
    return JSONResponse({"message": "灵枢 API 运行中", "docs": "/docs"}, status_code=200)


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
    try:
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

        # Save session (capped to MAX_HISTORY_ROUNDS to prevent unbounded growth)
        session = _load_session(session_id) or []
        session.append({"role": "user", "content": last_user_msg, "ts": time.time()})
        session.append({"role": "assistant", "content": response_text, "ts": time.time()})
        # Trim old rounds
        if len(session) > SESSION_TRIM_AT:
            session = session[-SESSION_TRIM_AT:]
        _save_session(session_id, session)

        print(
            "[Harness] [%s] ✅ (%d chars, %d 轮, workers: %s)" % (
                session_id[:8], len(response_text), rounds, workers)
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        response_text = "[HarnessError] %s" % e
    finally:
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


@app.get("/v1/sessions")
async def list_sessions():
    """List active sessions (persisted on disk)."""
    sessions = _list_sessions()
    return {"sessions": sessions, "count": len(sessions)}


@app.delete("/v1/sessions/{session_id}")
async def delete_session_endpoint(session_id: str):
    """Delete a specific session."""
    if _delete_session(session_id):
        return {"status": "deleted", "session_id": session_id}
    return JSONResponse(
        {"error": "Session not found: %s" % session_id},
        status_code=404,
    )


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
            count = index_file(tmp_path, collection=collection, filename=filename)
            info = collection_info(collection)
            return {
                "status": "ok",
                "filename": filename,
                "collection": collection,
                "chunks_indexed": count,
                "collection_info": info,
            }
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


# ─── Direct entry point ───


def main():
    import uvicorn

    # Initialize persistent session store
    _init_session_store()
    count = _session_count()
    print("")
    print("  ⚡ 灵枢 — LingShu Agent")
    print("  " + ("-" * 40))
    print("  API:       http://%s:%d/v1" % (HOST, PORT))
    print("  Frontend:  http://%s:%d" % (HOST, PORT))
    print("  Docs:      http://%s:%d/docs" % (HOST, PORT))
    print("  " + ("-" * 40))
    print("")

    uvicorn.run(
        "agent_harness.api_fastapi:app",
        host=HOST,
        port=PORT,
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    main()
