"""
工具权限检查 — 按伤害分级，带审计日志

权限级别:
  read-only     — 只读操作，无害，允许自动调用
  reversible    — 有副作用但可逆（如文件写入、生图），自动放行但记录
  irreversible  — 有不可逆影响（如代码执行、桌面操作）

irreversible 的处置（P0-4）：
  default 模式 — 一律拒绝（不再看 auto_confirm），并把待批请求落盘到
                 PENDING_DIR/req_*.json，供人工切 full 审批。理由：auto_confirm
                 默认 True，靠它兜底等于默认全开，而这一级是任意代码执行。
  full  模式 — 放行；调用方显式传 auto_confirm=False 时仍拒绝。
                 full 带 TTL（默认 30 分钟），到期自动回落 default。

在 call_tool() 中，irreversible 工具始终记录审计日志。
"""

import itertools
import json
import os
import sys
from datetime import UTC, datetime

# ─── Audit log directory ───
HARNESS_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".."))
AUDIT_DIR = os.path.join(os.path.expanduser("~"), ".agent-harness", "audit")
# 待批工单目录：default 模式下 irreversible 被拒时落盘，让人工知道「谁想跑什么」
PENDING_DIR = os.path.join(
    os.environ.get("HARNESS_DATA_DIR") or os.path.expanduser("~/.agent-harness"),
    "pending",
)

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


def _demo_mode() -> bool:
    """demo / 共享实例（DISABLE_AUTH=1）—— 访客不等于部署者。"""
    from agent_harness.core.config import DISABLE_AUTH
    return bool(DISABLE_AUTH)


def _demo_write_tool_allowed(tool_name: str) -> bool:
    """demo 下允许的写类工具白名单（逗号分隔，默认空 = 全拒，fail-closed）。"""
    raw = os.environ.get("HARNESS_DEMO_ALLOW_WRITE_TOOLS", "")
    return tool_name in {t.strip() for t in raw.split(",") if t.strip()}


def _write_pending_ticket(tool_name: str, source: str) -> str:
    """把待批请求落盘，返回工单 ID（写失败返回空串）。

    契约：拒绝一定发生，工单只是让「谁想跑什么」可追溯，**不是放行凭据** ——
    任何读工单自动放行的实现都会把这道闸变回默认全开。
    """
    try:
        os.makedirs(PENDING_DIR, exist_ok=True)
        seq = next(_AUDIT_SEQ)
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S%f")
        ticket = "req_{}_{:06d}".format(ts, seq)
        body = {
            "ticket": ticket,
            "tool": tool_name,
            "source": source,
            "ts": datetime.now(UTC).isoformat(),
            "status": "pending",
            "approve_hint": "确需执行时切 full 模式（set_mode('full')，带 TTL）后重试",
        }
        with open(os.path.join(PENDING_DIR, ticket + ".json"), "w", encoding="utf-8") as f:
            json.dump(body, f, ensure_ascii=False, indent=2)
        return ticket
    except Exception as exc:  # noqa: BLE001 — 工单写失败不能让拒绝退化成放行
        print(f"[PENDING] 待批工单写入失败 tool={tool_name} "
              f"原因={type(exc).__name__}: {exc}", file=sys.stderr)
        return ""


def check_permission(tool_name: str, source: str = "harness",
                     auto_confirm: bool = True) -> bool:
    """检查是否允许调用指定工具

    Args:
        tool_name: 工具名称
        source: 调用来源 (harness / mcp / human / api)
        auto_confirm:
            True (默认) — read-only / reversible 放行；irreversible 在 default 模式
                          **仍拒绝**（降级失效），只在 full 模式放行
            False — irreversible 一律拒绝（full 模式下也拒）
                    供前端 API 的严格调用方使用

    Returns:
        True 允许调用，False 拒绝

    权限分级之外的**第二道闸**：demo / 共享实例（DISABLE_AUTH=1）下，写类工具
    （reversible + irreversible）一律拒绝，除非在 HARNESS_DEMO_ALLOW_WRITE_TOOLS 里
    逐个点名。为什么不再走 auto_confirm：它默认 True，靠它兜底等于默认全开 ——
    而 reversible 里是 file_write / rag_index / skill_generate（写盘），irreversible 里
    是 code_execute（任意代码执行）。公网 demo 的访客拿到这些就是拿服务器。
    """
    level = get_tool_privilege(tool_name)

    if level == "read-only":
        return True

    if _demo_mode() and not _demo_write_tool_allowed(tool_name):
        log_audit(tool_name, source, {}, f"{level} denied in demo mode")
        return False

    if level == "reversible":
        return True

    if level == "irreversible":
        # 始终记录审计日志
        log_audit(tool_name, source, {})
        try:
            from ..safety.mode import is_full_access
            full_access = bool(is_full_access())
        except Exception:  # noqa: BLE001 — 模式模块坏掉按 default 处理，绝不当作 full
            full_access = False
        if not full_access:
            # default 模式：一律拒绝 + 落待批工单（P0-4）
            ticket = _write_pending_ticket(tool_name, source)
            log_audit(tool_name, source, {},
                      f"irreversible denied in default mode; "
                      f"ticket={ticket or 'write_failed'}")
            return False
        # full 模式：放行；调用方显式 auto_confirm=False 时仍拒绝
        return auto_confirm

    # 未知级别，安全起见拒绝
    return False
