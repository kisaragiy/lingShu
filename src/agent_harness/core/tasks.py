"""
任务队列 — SQLite 持久化，支持状态轮询和重试。

用法:
    POST /v1/tasks  {"type": "draw", "params": {...}}
    GET  /v1/tasks/{id}  → 状态 + 结果
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sqlite3

DB_PATH = Path(__file__).parent.parent.parent / "data" / "tasks.db"


def _get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            params TEXT,
            result TEXT,
            error TEXT,
            created_at REAL,
            updated_at REAL
        )
    """)
    conn.commit()
    return conn


def create_task(task_type: str, params: dict | None = None) -> dict:
    """创建任务，返回 {id, type, status, ...}"""
    task_id = uuid.uuid4().hex[:12]
    now = time.time()
    with _get_db() as conn:
        conn.execute(
            "INSERT INTO tasks (id, type, status, params, created_at, updated_at) VALUES (?, ?, 'pending', ?, ?, ?)",
            (task_id, task_type, json.dumps(params or {}, ensure_ascii=False), now, now),
        )
    return get_task(task_id)


def get_task(task_id: str) -> dict | None:
    """查询任务状态。"""
    with _get_db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        return None
    return dict(row)


def update_task(task_id: str, status: str, result: Any = None, error: str = "") -> None:
    """更新任务状态。"""
    now = time.time()
    with _get_db() as conn:
        conn.execute(
            "UPDATE tasks SET status = ?, result = ?, error = ?, updated_at = ? WHERE id = ?",
            (status, json.dumps(result, ensure_ascii=False) if result else None, error, now, task_id),
        )


def list_tasks(limit: int = 20, status: str = "") -> list[dict]:
    """列出任务。"""
    with _get_db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


# ── FastAPI 路由 ──────────────────────────────────


def register_task_routes(app):
    """注册任务队列 API 端点。"""
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse

    router = APIRouter(prefix="/v1/tasks", tags=["任务队列"])

    @router.post("")
    async def create_task_endpoint(type: str, params: dict = {}):
        task = create_task(type, params)
        return JSONResponse({"ok": True, "task": task})

    @router.get("/{task_id}")
    async def get_task_endpoint(task_id: str):
        task = get_task(task_id)
        if task is None:
            return JSONResponse({"ok": False, "error": "task not found"}, status_code=404)
        return JSONResponse({"ok": True, "task": task})

    @router.get("")
    async def list_tasks_endpoint(limit: int = 20, status: str = ""):
        tasks = list_tasks(limit, status)
        return JSONResponse({"ok": True, "tasks": tasks})

    app.include_router(router)
