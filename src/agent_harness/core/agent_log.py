"""Agent execution log — records per-session agent run traces.

Stores structured logs of what the agent did: which workers ran,
how long each took, search queries executed, etc.
Storage: ~/.agent-harness/agent_logs/{session_id}.jsonl
"""

import contextlib
import json
import os
import threading
import time
from pathlib import Path

LOG_DIR = Path(os.environ.get("HARNESS_DATA_DIR",
    Path.home() / ".agent-harness")) / "agent_logs"

_lock = threading.Lock()


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
