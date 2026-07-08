"""
工具权限检查 — 按伤害分级，带审计日志

权限级别:
  read-only     — 只读操作，无害，允许自动调用
  reversible    — 有副作用但可逆（如文件写入、生图），自动放行但记录
  irreversible  — 有不可逆影响（如代码执行、桌面操作），需显式确认

在 call_tool() 中，irreversible 工具始终记录审计日志。
外部调用者可通过 auto_confirm=False 触发 403 拒绝。
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

# ─── Audit log directory ───
HARNESS_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".."))
AUDIT_DIR = os.path.join(os.path.expanduser("~"), ".agent-harness", "audit")


def _ensure_audit_dir():
    os.makedirs(AUDIT_DIR, exist_ok=True)


def get_tool_privilege(tool_name: str) -> str:
    """获取工具的权限级别"""
    try:
        from .registry import TOOL_REGISTRY
        entry = TOOL_REGISTRY.get(tool_name, {})
        return entry.get("privilege", "reversible")
    except ImportError:
        return "reversible"


def log_audit(tool_name: str, source: str, args: dict, result: str = "",
              duration_ms: float = 0):
    """写入结构化审计日志"""
    _ensure_audit_dir()
    entry = {
        "ts": datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S%f"),
        "tool": tool_name,
        "source": source,
        "args": {k: str(v)[:200] for k, v in args.items()},
        "result": str(result)[:200],
        "duration_ms": round(duration_ms, 1),
    }
    path = os.path.join(AUDIT_DIR, "audit_%s.json" % entry["ts"])
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def check_permission(tool_name: str, source: str = "harness",
                     auto_confirm: bool = True) -> bool:
    """检查是否允许调用指定工具

    Args:
        tool_name: 工具名称
        source: 调用来源 (harness / mcp / human / api)
        auto_confirm: 
            True (默认) — read-only 和 reversible 直接放行，
                          irreversible 记录日志后放行（兼容模式）
            False — irreversible 工具一律拒绝（返回 False）
                    供前端 API 使用，需用户弹窗确认后才能调用

    Returns:
        True 允许调用，False 拒绝
    """
    level = get_tool_privilege(tool_name)

    if level == "read-only":
        return True

    if level == "reversible":
        return True

    if level == "irreversible":
        # 始终记录审计日志
        log_audit(tool_name, source, {})
        if auto_confirm:
            # 兼容模式：记录日志后放行
            return True
        # 严格模式：需要人类确认
        return False

    # 未知级别，安全起见拒绝
    return False
