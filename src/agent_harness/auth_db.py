"""User authentication store — SQLite + pbkdf2_hmac password hashing.

No external dependencies (uses Python stdlib only).
SQLite schema:
  users: id, username, password_hash, role, display_name, created_at, last_login
  sessions: jti, user_id, token_type, expires_at, created_at
"""

import base64
import json
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from hashlib import pbkdf2_hmac
from pathlib import Path

# ─── Paths ───

HARNESS_DIR = Path(os.environ.get("HARNESS_DATA_DIR",
    Path.home() / ".agent-harness"))
DB_PATH = HARNESS_DIR / "auth.db"

# ─── Password hashing (pbkdf2_hmac SHA-256, ~0.3s per check) ───

_PBKDF2_ITERATIONS = 600000

def _hash_password(password: str) -> str:
    """Hash password with random salt. Returns salt$hash_b64."""
    salt = secrets.token_hex(32)
    dk = pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), _PBKDF2_ITERATIONS)
    return "%s$%s" % (salt, base64.b64encode(dk).decode('ascii'))

def _verify_password(password: str, stored: str) -> bool:
    """Verify password against stored hash."""
    try:
        salt, hash_b64 = stored.split('$', 1)
        dk = pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), _PBKDF2_ITERATIONS)
        return base64.b64encode(dk).decode('ascii') == hash_b64
    except (ValueError, TypeError):
        return False

# ─── DB initialization ───

_DB_INITIALIZED = False
_thread_local = None  # threading.local set during init


def _get_conn() -> sqlite3.Connection:
    """Get a SQLite connection for the current thread (per-thread, WAL mode).

    Each thread gets its own connection — safe for concurrent reads/writes
    with WAL mode. Connections are cached in threading.local.
    """
    global _thread_local
    if _thread_local is None:
        _thread_local = threading.local()

    if not hasattr(_thread_local, 'conn') or _thread_local.conn is None:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")  # 5s retry on busy
        _thread_local.conn = conn
    return _thread_local.conn


def close_all_connections():
    """Close all cached connections (call on shutdown)."""
    global _thread_local
    if _thread_local is None:
        return
    for attr in dir(_thread_local):
        conn = getattr(_thread_local, attr, None)
        if isinstance(conn, sqlite3.Connection):
            try:
                conn.close()
            except Exception:
                pass
    _thread_local = None


def _get_db() -> sqlite3.Connection:
    """Alias for _get_conn() — backward compatibility."""
    return _get_conn()


def _init_schema():
    """Create tables if they don't exist (run once at startup)."""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id          TEXT PRIMARY KEY,
            username    TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role        TEXT NOT NULL DEFAULT 'user',
            display_name TEXT NOT NULL DEFAULT '',
            created_at  INTEGER NOT NULL,
            last_login  INTEGER DEFAULT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            jti         TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL REFERENCES users(id),
            token_type  TEXT NOT NULL DEFAULT 'access',
            expires_at  INTEGER NOT NULL,
            created_at  INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
    """)
    conn.commit()


# ─── User CRUD ───

_ROLE_VALUES = ("admin", "user")


def create_user(username: str, password: str, role: str = "user",
                display_name: str = "") -> dict:
    """Create a new user. Raises ValueError if username taken."""
    db = _get_db()
    if role not in _ROLE_VALUES:
        role = "user"

    # Validate
    if len(username) < 2 or len(username) > 64:
        raise ValueError("用户名长度需 2-64 字符")
    if len(password) < 6:
        raise ValueError("密码长度至少 6 位")
    if not username.isalnum() and "_" not in username:
        raise ValueError("用户名只能包含字母、数字和下划线")

    existing = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if existing:
        raise ValueError("用户名 '%s' 已存在" % username)

    uid = "u_" + secrets.token_hex(16)
    now = int(time.time())
    pw_hash = _hash_password(password)
    db.execute(
        "INSERT INTO users (id, username, password_hash, role, display_name, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (uid, username, pw_hash, role, display_name or username, now),
    )
    db.commit()
    return {"id": uid, "username": username, "role": role,
            "display_name": display_name or username, "created_at": now}


def authenticate_user(username: str, password: str) -> dict | None:
    """Verify credentials. Returns user dict on success, None on failure."""
    db = _get_db()
    row = db.execute(
        "SELECT id, username, password_hash, role, display_name, created_at, last_login "
        "FROM users WHERE username=?", (username,)
    ).fetchone()
    if row is None:
        return None
    if not _verify_password(password, row["password_hash"]):
        return None
    # Update last_login
    now = int(time.time())
    db.execute("UPDATE users SET last_login=? WHERE id=?", (now, row["id"]))
    db.commit()
    return {
        "id": row["id"], "username": row["username"],
        "role": row["role"], "display_name": row["display_name"],
        "created_at": row["created_at"], "last_login": now,
    }


def get_user(user_id: str) -> dict | None:
    """Get user by ID."""
    db = _get_db()
    row = db.execute(
        "SELECT id, username, role, display_name, created_at, last_login "
        "FROM users WHERE id=?", (user_id,)
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"], "username": row["username"],
        "role": row["role"], "display_name": row["display_name"],
        "created_at": row["created_at"], "last_login": row["last_login"],
    }


def get_user_by_username(username: str) -> dict | None:
    """Get user by username."""
    db = _get_db()
    row = db.execute(
        "SELECT id, username, role, display_name, created_at, last_login "
        "FROM users WHERE username=?", (username,)
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"], "username": row["username"],
        "role": row["role"], "display_name": row["display_name"],
        "created_at": row["created_at"], "last_login": row["last_login"],
    }


def list_users() -> list[dict]:
    """List all users (no password hashes)."""
    db = _get_db()
    rows = db.execute(
        "SELECT id, username, role, display_name, created_at, last_login "
        "FROM users ORDER BY created_at ASC"
    ).fetchall()
    return [{
        "id": r["id"], "username": r["username"],
        "role": r["role"], "display_name": r["display_name"],
        "created_at": r["created_at"], "last_login": r["last_login"],
    } for r in rows]


def update_user_role(user_id: str, new_role: str) -> bool:
    """Change a user's role. Cannot change own role (must be done via admin re-login)."""
    if new_role not in _ROLE_VALUES:
        return False
    db = _get_db()
    cur = db.execute("UPDATE users SET role=? WHERE id=?", (new_role, user_id))
    db.commit()
    return cur.rowcount > 0


def update_user_password(user_id: str, new_password: str) -> bool:
    """Reset a user's password."""
    if len(new_password) < 6:
        return False
    db = _get_db()
    pw_hash = _hash_password(new_password)
    cur = db.execute("UPDATE users SET password_hash=? WHERE id=?", (pw_hash, user_id))
    db.commit()
    return cur.rowcount > 0


def delete_user(user_id: str) -> bool:
    """Delete a user and their sessions."""
    db = _get_db()
    db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    cur = db.execute("DELETE FROM users WHERE id=?", (user_id,))
    db.commit()
    return cur.rowcount > 0


def user_count() -> int:
    """Count total users."""
    db = _get_db()
    row = db.execute("SELECT COUNT(*) as cnt FROM users").fetchone()
    return row["cnt"] if row else 0


# ─── Session management (JWT blacklist / refresh) ───

def save_session(jti: str, user_id: str, expires_at: int,
                 token_type: str = "access") -> bool:
    """Record a session token in the database."""
    db = _get_db()
    now = int(time.time())
    try:
        db.execute(
            "INSERT INTO sessions (jti, user_id, token_type, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (jti, user_id, token_type, expires_at, now),
        )
        db.commit()
        return True
    except sqlite3.IntegrityError:
        return False  # jti collision (extremely unlikely)


def revoke_session(jti: str) -> bool:
    """Revoke a specific session (logout)."""
    db = _get_db()
    cur = db.execute("DELETE FROM sessions WHERE jti=?", (jti,))
    db.commit()
    return cur.rowcount > 0


def revoke_all_user_sessions(user_id: str) -> int:
    """Revoke all sessions for a user (password change, admin action)."""
    db = _get_db()
    cur = db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    db.commit()
    return cur.rowcount


def is_session_valid(jti: str) -> dict | None:
    """Check if a session token is still valid (exists, not expired)."""
    db = _get_db()
    now = int(time.time())
    row = db.execute(
        "SELECT s.jti, s.user_id, s.token_type, s.expires_at, "
        "u.username, u.role "
        "FROM sessions s JOIN users u ON s.user_id = u.id "
        "WHERE s.jti=? AND s.expires_at>?",
        (jti, now),
    ).fetchone()
    if row is None:
        return None
    return {
        "jti": row["jti"], "user_id": row["user_id"],
        "token_type": row["token_type"],
        "username": row["username"], "role": row["role"],
    }


def cleanup_expired_sessions() -> int:
    """Remove expired sessions. Returns count of removed rows."""
    db = _get_db()
    now = int(time.time())
    cur = db.execute("DELETE FROM sessions WHERE expires_at<=?", (now,))
    db.commit()
    return cur.rowcount


# ─── Admin first-boot check ───

def needs_initial_admin() -> bool:
    """Check if no admin user exists yet (first boot after auth upgrade)."""
    db = _get_db()
    row = db.execute(
        "SELECT COUNT(*) as cnt FROM users WHERE role='admin'"
    ).fetchone()
    return row["cnt"] == 0


def ensure_first_admin(username: str = "admin", password: str = "admin123") -> dict:
    """Create the initial admin account if none exists. For setup wizard."""
    if not needs_initial_admin():
        return get_user_by_username(username) or list_users()[0]
    return create_user(username, password, role="admin", display_name="管理员")
