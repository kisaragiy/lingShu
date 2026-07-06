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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

HOST = os.environ.get("HARNESS_API_HOST", "127.0.0.1")
PORT = int(os.environ.get("HARNESS_API_PORT", "8788"))

# ─── In-memory session store ───
_sessions: dict[str, list[dict]] = {}
_SESSION_TTL = 7200  # 2 hours idle timeout


def _clean_expired_sessions():
    now = time.time()
    expired = [
        sid for sid, msgs in list(_sessions.items())
        if msgs and (now - msgs[-1].get("ts", 0)) > _SESSION_TTL
    ]
    for sid in expired:
        del _sessions[sid]


def _get_or_create_session(session_id: str) -> list[dict]:
    _clean_expired_sessions()
    if session_id not in _sessions:
        _sessions[session_id] = []
    return _sessions[session_id]


def _build_history_context(messages: list[dict]) -> str:
    if not messages:
        return ""
    lines = []
    for m in messages:
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

def _execute_multi(prompt: str, history_context: str) -> dict:
    from .graph_multi import run_multi_agent
    enhanced = prompt
    if history_context:
        enhanced = (
            "以下是本对话的历史记录（越新的越靠前）:\n%s\n\n"
            "请基于以上对话上下文，回答用户当前的问题:\n%s"
        ) % (history_context, prompt)
    return run_multi_agent(enhanced)


def _execute_single(prompt: str, history_context: str) -> str:
    from .graph import run as run_single
    enhanced = prompt
    if history_context:
        enhanced = "以下是对话历史:\n%s\n\n当前问题: %s" % (history_context, prompt)
    return run_single(enhanced)


# ─── Streaming with progress queue ───

def _run_with_queue(prompt: str, history: str, model: str, q: queue.Queue):
    try:
        q.put({"type": "status", "content": "🤔 分析请求中...\n"})

        if model in ("agent-harness", "agent-harness-single"):
            q.put({"type": "status", "content": "⚙️ 单 Agent 处理中...\n"})
            result = _execute_single(prompt, history)
            q.put({"type": "result", "content": result})
        else:
            q.put({"type": "status", "content": "🔀 多 Agent 协作执行中...\n"})
            result = _execute_multi(prompt, history)
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


async def _stream_progress(prompt: str, history: str, model: str, session_id: str):
    q = queue.Queue()
    t = threading.Thread(
        target=_run_with_queue, args=(prompt, history, model, q), daemon=True
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
        elif event["type"] == "error":
            content = event["content"]
            result_text += content
            yield _sse_chunk(content)
        elif event["type"] == "status":
            content = event["content"]
            result_text += content
            yield _sse_chunk(content)
        elif event["type"] == "result":
            content = event["content"]
            result_text = content
            yield _sse_chunk(content)

    # Save to session
    session = _get_or_create_session(session_id)
    session.append({"role": "user", "content": prompt, "ts": time.time()})
    session.append({"role": "assistant", "content": result_text, "ts": time.time()})

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
        "active_sessions": len(_sessions),
    }


@app.get("/v1/models")
async def list_models():
    now = int(time.time())
    return {
        "object": "list",
        "data": [
            {
                "id": "agent-harness-multi",
                "object": "model",
                "created": now,
                "owned_by": "harness",
            },
            {
                "id": "agent-harness",
                "object": "model",
                "created": now,
                "owned_by": "harness",
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
        if req.model in ("agent-harness", "agent-harness-single"):
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

        # Save session
        session = _get_or_create_session(session_id)
        session.append({"role": "user", "content": last_user_msg, "ts": time.time()})
        session.append({"role": "assistant", "content": response_text, "ts": time.time()})

        print(
            "[Harness] [%s] ✅ (%d chars, %d 轮, workers: %s)" % (
                session_id[:8], len(response_text), rounds, workers)
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        response_text = "[HarnessError] %s" % e

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


@app.get("/v1/sessions")
async def list_sessions():
    _clean_expired_sessions()
    now = time.time()
    info = []
    for sid, msgs in _sessions.items():
        if msgs:
            info.append({
                "id": sid[:8],
                "exchanges": len(msgs) // 2,
                "last_active": int(now - msgs[-1]["ts"]),
                "preview": msgs[-1].get("content", "")[:60],
            })
    return {"sessions": info, "count": len(info)}


# ─── Direct entry point ───


def main():
    import uvicorn

    print("")
    print("  Agent Harness API")
    print("  " + ("-" * 40))
    print("  Endpoint:  http://%s:%d/v1" % (HOST, PORT))
    print("  Models:    agent-harness (single), agent-harness-multi (multi)")
    print("  Sessions:  ✓ (X-Session-Id header)")
    print("  Streaming: ✓ (SSE progressive)")
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
