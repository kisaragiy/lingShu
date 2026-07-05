"""FastAPI server — OpenAI-compatible API for Agent Harness.

Start: agent-harness serve (port 8788)
Connect: OpenAI client with base_url=http://localhost:8788/v1
"""

import sys
import os
import json
import time
import asyncio
import traceback

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel


HOST = os.environ.get("HARNESS_API_HOST", "127.0.0.1")
PORT = int(os.environ.get("HARNESS_API_PORT", "8788"))


class ChatRequest(BaseModel):
    model: str = "agent-harness"
    messages: list[dict]
    stream: bool = False
    max_tokens: int = 4096
    temperature: float = 0.7


app = FastAPI(title="Agent Harness API", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.2.0"}


@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {"id": "agent-harness", "object": "model"},
            {"id": "agent-harness-multi", "object": "model"},
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest):
    user_msg = req.messages[-1]["content"] if req.messages else ""

    if req.stream:
        return StreamingResponse(
            _stream_response(user_msg, req.model),
            media_type="text/event-stream",
        )

    # Non-streaming
    result = _run_harness(user_msg, req.model)
    return JSONResponse({
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": req.model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": result},
            "finish_reason": "stop",
        }],
    })


async def _stream_response(prompt: str, model: str):
    """Stream response as SSE."""
    result = _run_harness(prompt, model)
    chunk = {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": {"content": result}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


def _run_harness(prompt: str, model: str) -> str:
    """Run the appropriate agent based on model selection."""
    try:
        if model == "agent-harness":
            from .graph import run as run_single
            return run_single(prompt)
        else:
            from .graph_multi import run_multi_agent
            result = run_multi_agent(prompt)
            return result.get("final_output", "")
    except Exception as e:
        traceback.print_exc()
        return f"Error: {e}"


def main():
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
