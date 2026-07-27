"""
操作审计 — 记录每次 API 调用的 trace_id、params、duration、result。

用法:
    from agent_harness.core.audit import audit
    audit("generate", model="sdxl", duration=12.3, result="ok")
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).parent.parent.parent / "data" / "audit.db"


def _get_conn():
    import sqlite3
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id TEXT PRIMARY KEY,
            trace_id TEXT,
            action TEXT NOT NULL,
            params TEXT,
            result TEXT,
            duration_ms REAL,
            error TEXT,
            created_at REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at)")
    conn.commit()
    return conn


def audit(action: str, **kwargs: Any) -> str:
    """记录一条审计日志。返回 trace_id。"""
    trace_id = uuid.uuid4().hex[:12]
    now = time.time()
    duration = kwargs.pop("duration_ms", None)
    result = kwargs.pop("result", None)
    error = kwargs.pop("error", "")
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO audit_log (id, trace_id, action, params, result, duration_ms, error, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                uuid.uuid4().hex[:16],
                trace_id,
                action,
                json.dumps(kwargs, ensure_ascii=False, default=str) if kwargs else None,
                json.dumps(result, ensure_ascii=False, default=str) if result else None,
                duration,
                error,
                now,
            ),
        )
    return trace_id


def query_audit(action: str = "", limit: int = 50) -> list[dict]:
    """查询审计日志。"""
    import sqlite3
    with _get_conn() as conn:
        conn.row_factory = sqlite3.Row
        if action:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE action = ? ORDER BY created_at DESC LIMIT ?",
                (action, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]
