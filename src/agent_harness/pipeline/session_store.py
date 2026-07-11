"""Session Store — persistent session storage using JSON files.

Thread-safe with a single RLock covering all file operations.
Supports owner_id for multi-user session isolation.

Usage:
    from agent_harness.pipeline.session_store import (
        save_session, load_session, list_sessions,
        delete_session, clean_expired, init_store,
    )

    init_store()
    save_session("session-123", msgs, owner_id="u_xxx")
    msgs = load_session("session-123")
    sessions = list_sessions(owner_id="u_xxx")
"""

import contextlib
import json
import os
import threading
import time

# ─── Config ───

# Storage directory
DEFAULT_DIR = os.path.join(os.path.expanduser("~"), ".agent-harness", "sessions")
SESSION_DIR = os.environ.get("HARNESS_SESSION_DIR", DEFAULT_DIR)

# Session TTL (seconds) — sessions older than this are pruned
SESSION_TTL = int(os.environ.get("HARNESS_SESSION_TTL", str(7 * 24 * 3600)))  # 7 days

# Reentrant lock — covers all file operations (reads + writes)
_lock = threading.RLock()


# ─── Path helpers ───

def _session_path(session_id: str) -> str:
    """Get filesystem path for a session file."""
    safe_id = session_id.replace("/", "_").replace("\\", "_").replace("..", "_")
    return os.path.join(SESSION_DIR, f"{safe_id}.json")


# ─── Public API ───

def init_store():
    """Initialize the session store directory."""
    os.makedirs(SESSION_DIR, exist_ok=True)
    clean_expired()


def save_session(session_id: str, messages: list[dict], owner_id: str = ""):
    """Save a session's messages to disk (thread-safe, atomic write).

    Args:
        session_id: Unique session identifier
        messages: List of message dicts (must include 'ts' timestamp)
        owner_id: User ID who owns this session (for multi-user isolation)
    """
    if not messages:
        return

    path = _session_path(session_id)
    now = time.time()

    # Load existing session data to preserve title/pinned
    existing = {}
    try:
        with _lock, open(path, encoding="utf-8") as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    # Build session record
    session = {
        "session_id": session_id,
        "title": existing.get("title", ""),
        "pinned": existing.get("pinned", False),
        "updated_at": now,
        "created_at": messages[0].get("ts", now) if messages else now,
        "message_count": len(messages),
        "exchanges": len(messages) // 2,
        "last_preview": messages[-1].get("content", "")[:120],
        "owner_id": owner_id,
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
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
            raise


def load_session(session_id: str) -> list[dict] | None:
    """Load a session's messages from disk.

    Returns:
        List of message dicts, or None if session doesn't exist
    """
    path = _session_path(session_id)
    with _lock:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data.get("messages", [])
        except (FileNotFoundError, json.JSONDecodeError):
            return None


def list_sessions(owner_id: str | None = None) -> list[dict]:
    """List all persisted sessions with metadata.

    Args:
        owner_id: If set, only return sessions owned by this user.
                  If None, return all sessions (admin view).

    Returns:
        List of session summary dicts (without full messages), newest first
    """
    if not os.path.isdir(SESSION_DIR):
        return []

    now = time.time()
    sessions = []

    with _lock:
        for fname in os.listdir(SESSION_DIR):
            if not fname.endswith(".json") or fname.endswith(".tmp"):
                continue
            path = os.path.join(SESSION_DIR, fname)
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                # Skip expired
                age = now - data.get("updated_at", 0)
                if age > SESSION_TTL:
                    with contextlib.suppress(OSError):
                        os.unlink(path)
                    continue
                # Filter by owner
                if owner_id is not None and data.get("owner_id", "") != owner_id:
                    continue
                sessions.append({
                    "id": data.get("session_id", fname[:-5]),
                    "title": data.get("title", ""),
                    "pinned": data.get("pinned", False),
                    "exchanges": data.get("exchanges", 0),
                    "message_count": data.get("message_count", 0),
                    "created_at": data.get("created_at", 0),
                    "updated_at": data.get("updated_at", 0),
                    "last_active": int(age),
                    "preview": data.get("last_preview", ""),
                    "owner_id": data.get("owner_id", ""),
                })
            except (json.JSONDecodeError, OSError):
                continue

    sessions.sort(key=lambda s: (not s.get("pinned", False), -s.get("updated_at", 0)))
    return sessions


def delete_session(session_id: str) -> bool:
    """Delete a session from disk.

    Returns:
        True if deleted, False if not found
    """
    path = _session_path(session_id)
    with _lock:
        try:
            os.unlink(path)
            return True
        except FileNotFoundError:
            return False


def clean_expired() -> int:
    """Remove sessions older than TTL. Returns count of removed sessions."""
    if not os.path.isdir(SESSION_DIR):
        return 0
    now = time.time()
    count = 0
    with _lock:
        for fname in os.listdir(SESSION_DIR):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(SESSION_DIR, fname)
            try:
                with open(path, encoding="utf-8") as f:
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


def search_messages(query: str, owner_id: str | None = None, limit: int = 20) -> list[dict]:
    """Search message content across all sessions.

    Args:
        query: Text to search for (case-insensitive)
        owner_id: If set, only search this user's sessions
        limit: Max results

    Returns:
        List of {session_id, session_title, role, content_preview, ts, score}
    """
    q = query.lower()
    results = []
    sessions = list_sessions(owner_id=owner_id)

    for s in sessions:
        sid = s["id"]
        msgs = load_session(sid)
        if not msgs:
            continue
        for msg in msgs:
            content = msg.get("content", "")
            if q in content.lower():
                ts = msg.get("ts", 0)
                preview = content[:200]
                results.append({
                    "session_id": sid,
                    "session_title": s.get("title", "") or s.get("preview", ""),
                    "role": msg.get("role", ""),
                    "content_preview": preview,
                    "ts": ts,
                    "time": time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "",
                })
                if len(results) >= limit:
                    break
        if len(results) >= limit:
            break

    results.sort(key=lambda r: r.get("ts", 0), reverse=True)
    return results[:limit]


def update_session_meta(session_id: str, **kwargs) -> dict | None:
    """Update session metadata (title, pinned) without rewriting messages.

    Args:
        session_id: Session ID
        **kwargs: Fields to update (title, pinned)

    Returns:
        Updated session metadata, or None if session not found
    """
    path = _session_path(session_id)
    with _lock:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        for k in ("title", "pinned"):
            if k in kwargs:
                data[k] = kwargs[k]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return {
        "id": data.get("session_id", session_id),
        "title": data.get("title", ""),
        "pinned": data.get("pinned", False),
        "exchanges": data.get("exchanges", 0),
    }
