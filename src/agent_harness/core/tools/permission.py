"""
工具权限检查 — 按伤害分级，带审计日志

权限级别:
  read-only     — 只读操作，无害，允许自动调用
  reversible    — 有副作用但可逆（如文件写入、生图），自动放行但记录
  irreversible  — 有不可逆影响（如代码执行、桌面操作），需显式确认

在 call_tool() 中，irreversible 工具始终记录审计日志。
外部调用者可通过 auto_confirm=False 触发 403 拒绝。
"""

import itertools
import json
import os
import sys
from datetime import UTC, datetime

# ─── Audit log directory ───
HARNESS_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".."))
AUDIT_DIR = os.path.join(os.path.expanduser("~"), ".agent-harness", "audit")

# 进程内单调序号：Windows 时钟粒度不足以保证「同一微秒」不重号，而文件名只用
# 时间戳 → 并发（线程/连续调用）撞名时后一条会覆盖前一条，审计静默丢记录。
_AUDIT_SEQ = itertools.count(1)


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
    """写入结构化审计日志

    审计失败绝不打断业务（磁盘满/权限不足时工具照常执行），但也绝不静默：
    写不进去就往 stderr 明确告警一次，让审计断档可见。
    """
    seq = next(_AUDIT_SEQ)
    entry = {
        # 微秒 + 进程内单调序号：并发/连续调用不会撞名，序号单调可排序
        "ts": "{}_{:06d}".format(datetime.now(UTC).strftime("%Y%m%d_%H%M%S%f"), seq),
        "seq": seq,
        "tool": tool_name,
        "source": source,
        "args": {k: str(v)[:200] for k, v in args.items()},
        "result": str(result)[:200],
        "duration_ms": round(duration_ms, 1),
    }
    path = os.path.join(AUDIT_DIR, "audit_{}.json".format(entry["ts"]))
    try:
        _ensure_audit_dir()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001 — 审计失败不杀业务，但必须可见
        print(
            f"[AUDIT] 审计日志写入失败，本次记录已丢失 — tool={tool_name} "
            f"source={source} path={path} 原因={type(exc).__name__}: {exc}",
            file=sys.stderr,
        )


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
        # 兼容模式：记录日志后放行；严格模式：需要人类确认
        return auto_confirm

    # 未知级别，安全起见拒绝
    return False
