"""Session Store — persistent session storage using JSON files.

Saves conversation sessions to disk so they survive server restarts.
Auto-prunes sessions older than the configured TTL.

Usage:
    from agent_harness.pipeline.session_store import (
        save_session, load_session, list_sessions,
        delete_session, clean_expired, init_store,
    )

    init_store()
    save_session("session-123", [{"role": "user", "content": "hi", "ts": ...}])
    msgs = load_session("session-123")
    sessions = list_sessions()
"""

import json
import os
import time
import threading
from pathlib import Path

# ─── Config ───

# Storage directory
DEFAULT_DIR = os.path.join(os.path.expanduser("~"), ".agent-harness", "sessions")
SESSION_DIR = os.environ.get("HARNESS_SESSION_DIR", DEFAULT_DIR)

# Session TTL (seconds) — sessions older than this are pruned
SESSION_TTL = int(os.environ.get("HARNESS_SESSION_TTL", str(7 * 24 * 3600)))  # 7 days

# File lock for thread safety
_lock = threading.Lock()


# ─── Path helpers ───

def _session_path(session_id: str) -> str:
    """Get filesystem path for a session file."""
    # Sanitize session_id to prevent directory traversal
    safe_id = session_id.replace("/", "_").replace("\\", "_").replace("..", "_")
    return os.path.join(SESSION_DIR, "%s.json" % safe_id)


# ─── Public API ───

def init_store():
    """Initialize the session store directory."""
    os.makedirs(SESSION_DIR, exist_ok=True)
    clean_expired()


def save_session(session_id: str, messages: list[dict]):
    """Save a session's messages to disk.

    Args:
        session_id: Unique session identifier
        messages: List of message dicts (must include 'ts' timestamp)
    """
    if not messages:
        return

    path = _session_path(session_id)
    now = time.time()

    # Build session record
    session = {
        "session_id": session_id,
        "updated_at": now,
        "created_at": messages[0].get("ts", now) if messages else now,
        "message_count": len(messages),
        "exchanges": len(messages) // 2,
        "last_preview": messages[-1].get("content", "")[:100],
        "messages": messages,
    }

    with _lock:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(session, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)  # Atomic on POSIX, near-atomic on Windows
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise


def load_session(session_id: str) -> list[dict] | None:
    """Load a session's messages from disk.

    Returns:
        List of message dicts, or None if session doesn't exist
    """
    path = _session_path(session_id)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("messages", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def list_sessions() -> list[dict]:
    """List all persisted sessions with metadata.

    Returns:
        List of session summary dicts (without full messages)
    """
    os.makedirs(SESSION_DIR, exist_ok=True)
    now = time.time()
    sessions = []

    with _lock:
        for fname in os.listdir(SESSION_DIR):
            if not fname.endswith(".json") or fname.endswith(".tmp.json"):
                continue
            path = os.path.join(SESSION_DIR, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Skip expired
                age = now - data.get("updated_at", 0)
                if age > SESSION_TTL:
                    os.unlink(path)
                    continue
                sessions.append({
                    "id": data.get("session_id", fname[:-5]),
                    "exchanges": data.get("exchanges", 0),
                    "message_count": data.get("message_count", 0),
                    "created_at": data.get("created_at", 0),
                    "updated_at": data.get("updated_at", 0),
                    "last_active": int(age),
                    "preview": data.get("last_preview", ""),
                })
            except (json.JSONDecodeError, OSError):
                continue

    sessions.sort(key=lambda s: s.get("updated_at", 0), reverse=True)
    return sessions


def delete_session(session_id: str) -> bool:
    """Delete a session from disk.

    Returns:
        True if deleted, False if not found
    """
    path = _session_path(session_id)
    try:
        os.unlink(path)
        return True
    except FileNotFoundError:
        return False


def clean_expired():
    """Remove sessions older than TTL."""
    now = time.time()
    count = 0
    with _lock:
        for fname in os.listdir(SESSION_DIR):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(SESSION_DIR, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                age = now - data.get("updated_at", 0)
                if age > SESSION_TTL:
                    os.unlink(path)
                    count += 1
            except (json.JSONDecodeError, OSError):
                # Corrupt or unreadable — delete
                try:
                    os.unlink(path)
                    count += 1
                except OSError:
                    pass
    return count


def session_count() -> int:
    """Return the number of active (non-expired) sessions."""
    return len(list_sessions())


def get_session_summary(session_id: str) -> dict | None:
    """Get session metadata without loading all messages."""
    sessions = list_sessions()
    for s in sessions:
        if s["id"] == session_id:
            return s
    return None
