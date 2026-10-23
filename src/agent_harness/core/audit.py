"""
操作审计 — 记录每次 API 调用的 trace_id、params、duration、result。

用法:
    from agent_harness.core.audit import audit
    audit("generate", model="sdxl", duration=12.3, result="ok")
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

# 数据根绑定（模块级可见：架构边界测试的运行时归属断言按属性名核对）
data_dir = os.environ.get("HARNESS_DATA_DIR")


def _resolve_db_path() -> Path:
    """审计库位置：显式变量 > 数据根 > 用户家目录（**绝不**落安装目录）。

    原实现 ``Path(__file__).parent.parent.parent / "data" / "audit.db"`` 指向
    ``src/data/audit.db``：pip 安装后 ``site-packages`` 不可写 → 审计静默失效；
    跑测试则在源码树里写脏文件。其它数据根（session_store / safety.mode）都读
    环境变量，这里对齐。
    """
    explicit = os.environ.get("HARNESS_AUDIT_DB")
    if explicit:
        return Path(explicit)
    if data_dir:
        return Path(data_dir) / "audit.db"
    return Path.home() / ".agent-harness" / "audit.db"


DB_PATH = _resolve_db_path()


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


def _redact_enabled() -> bool:
    """SEC-19 落库闸：HARNESS_AUDIT_REDACT=0 时保留原文（本地调试用）。

    与 SEC-3 模块层的 HARNESS_PII_REDACT 是两层开关：本闸管"审计落库"，
    模块闸管"报告输出"，不合并（一个关掉不影响另一个的语义）。
    """
    return os.environ.get("HARNESS_AUDIT_REDACT", "1") != "0"


def audit(action: str, **kwargs: Any) -> str:
    """记录一条审计日志。返回 trace_id。

    敏感实参（api_key/token/Bearer/手机号/邮箱…）在**落库前**脱敏 ——
    审计的用途是排障，排障不能以明文留存密钥/隐私为代价。
    """
    from agent_harness.core.safety.pii_redact import redact_pii

    trace_id = uuid.uuid4().hex[:12]
    now = time.time()
    duration = kwargs.pop("duration_ms", None)
    result = kwargs.pop("result", None)
    error = kwargs.pop("error", "")

    params_json = json.dumps(kwargs, ensure_ascii=False, default=str) if kwargs else None
    result_json = json.dumps(result, ensure_ascii=False, default=str) if result else None
    if _redact_enabled():
        if params_json:
            params_json = redact_pii(params_json)
        if result_json:
            result_json = redact_pii(result_json)

    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO audit_log (id, trace_id, action, params, result, duration_ms, error, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                uuid.uuid4().hex[:16],
                trace_id,
                action,
                params_json,
                result_json,
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
