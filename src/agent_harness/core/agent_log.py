"""Agent execution log — records per-session agent run traces.

Stores structured logs of what the agent did: which workers ran,
how long each took, search queries executed, etc.
Storage: ~/.agent-harness/agent_logs/{session_id}.jsonl
"""

import contextlib
import contextvars
import json
import os
import threading
import time
from pathlib import Path

LOG_DIR = Path(os.environ.get("HARNESS_DATA_DIR",
    Path.home() / ".agent-harness")) / "agent_logs"

_lock = threading.Lock()

# ── 当前会话上下文（P1-1）──────────────────────────────
# 让 LLM 调用点（llm.py / supervisor.py）在没有 session_id 参数的情况下也能把
# 可复现信封写进正确的会话日志。默认 None —— 取不到就只发结构化日志，不报错。
_current_session: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "harness_current_session", default=None)


def set_current_session(session_id: str | None):
    """设置当前会话 ID（contextvar，随协程/线程隔离）。返回 token 供重置。"""
    return _current_session.set(session_id)


def get_current_session() -> str | None:
    """读当前会话 ID；未设置时返回 None（调用方自行决定降级）。"""
    return _current_session.get()



def _ensure():
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def log_event(session_id: str, event_type: str, data: dict):
    """Log a single agent execution event.

    Args:
        session_id: Chat session ID
        event_type: 'search', 'worker_start', 'worker_end', 'llm_call', 'finalize'
        data: Event-specific data dict
    """
    _ensure()
    safe_id = session_id.replace("/", "_").replace("\\", "_").replace("..", "_")[:40]
    log_file = LOG_DIR / (f"{safe_id}.jsonl")
    entry = {
        "ts": time.time(),
        "type": event_type,
        "data": data,
    }
    with _lock:
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass


def get_logs(session_id: str, limit: int = 50) -> list[dict]:
    """Get agent execution logs for a session, newest first.

    Args:
        session_id: Chat session ID
        limit: Max events to return

    Returns:
        List of event dicts
    """
    _ensure()
    safe_id = session_id.replace("/", "_").replace("\\", "_").replace("..", "_")[:40]
    log_file = LOG_DIR / (f"{safe_id}.jsonl")
    events = []
    with _lock:
        try:
            if log_file.exists():
                with open(log_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            with contextlib.suppress(json.JSONDecodeError):
                                events.append(json.loads(line))
        except OSError:
            pass
    events.sort(key=lambda e: e.get("ts", 0), reverse=True)
    return events[:limit]


def clear_logs(session_id: str) -> bool:
    """Delete agent logs for a session."""
    _ensure()
    safe_id = session_id.replace("/", "_").replace("\\", "_").replace("..", "_")[:40]
    log_file = LOG_DIR / (f"{safe_id}.jsonl")
    with _lock:
        try:
            if log_file.exists():
                log_file.unlink()
                return True
        except OSError:
            pass
    return False


def log_llm_envelope(session_id: str | None, envelope: dict, usage: dict | None = None):
    """落一次 LLM 调用的**可复现信封**（P1-1 + P1-4）。

    写出的事件类型为 `llm_call`，`data` 同时含旧键与 OTel `gen_ai.*` 键
    （见 `reproducibility.envelope_record`），旧读者不受影响。

    `session_id` 为空时：只发结构化日志（`core.log_kit`），**不报错、不写错会话**。
    整函数被 try 包住 —— 观测层故障绝不改变 LLM 调用结果（与 metrics 同一条原则）。
    """
    try:
        from .reproducibility import envelope_record
        record = dict(envelope or {})
        record = {**record, **envelope_record(envelope, usage)}

        sid = session_id if session_id is not None else get_current_session()
        if sid:
            log_event(sid, "llm_call", record)

        try:
            from .log_kit import log_info
            log_info("llm_call", **record)
        except Exception:
            pass
    except Exception:
        pass

