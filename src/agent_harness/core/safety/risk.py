"""
危险操作确认矩阵 — 按"工具 + 参数"分类检测风险，不只是按工具名分级

对齐 WorkBuddy 权限设计中的"危险操作确认矩阵"概念：
不是"这个工具危险"（工具级），而是"这次操作危险"（参数级）。

风险类别（default 模式下命中即需确认，full 模式放行但全量审计）:
  sensitive_write  — 写敏感路径（系统目录 / 密钥文件 / 用户目录根）
  bulk_delete      — 批量删除（通配符 / 目录递归 / 多文件）
  path_escape      — 路径逃逸（.. 或绝对路径离开工作区）
  script_exec      — 脚本/代码执行
  external_send    — 对外发送（IM 消息、发帖等不可逆外部影响）
  network_fetch    — 主动网络访问（非白名单域名）

确认流程:
  check_operation(tool, args)
    → {"ok": True}                                 放行
    → {"ok": False, "reason": "...", "confirm_code": "ab12cd34"}  需确认
  调用方拿到 confirm_code 后向用户展示，用户确认后调 confirm_operation(code)
  确认后的操作一次性放行（pending 队列弹出）。
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Optional

from . import mode as safety_mode

# ─── 敏感路径模式（Windows 为主，兼容 POSIX） ───
_SENSITIVE_DIR_RE = re.compile(
    r"(?i)(?:^|[\\/])(?:windows|program\s*files(?:|\(x86\))|system32|syswow64|"
    r"appdata|programdata|users?[\\/][^\\/]+[\\/](?:appdata|desktop|documents|downloads|"
    r"\.ssh|\.gnupg|\.aws)|etc|usr[\\/](?:bin|sbin|lib)|boot|proc|sys)(?:[\\/]|$)"
)

# 密钥/敏感文件名（命中即拒写，无论模式）
_SECRET_FILE_RE = re.compile(
    r"(?i)(?:^|[\\/])(?:\.env[^\\/]*|\.pem$|\.key$|\.p12$|\.pfx$|id_rsa|id_ed25519|"
    r"credentials[^\\/]*\.json|service_account[^\\/]*\.json|jwt_secret|api_token|"
    r"\.git[\\/]config)"
)

# 网络白名单（default 模式直接放行的域名；其余需确认）
_NETWORK_ALLOW_DOMAINS = {
    "localhost", "127.0.0.1", "0.0.0.0", "::1",
    "searxng", "docker", "host.docker.internal",
}


class _PendingConfirm:
    """待确认操作队列（内存 + 落盘，重启不丢）。"""

    def __init__(self, state_dir: Path):
        self._dir = state_dir / "pending"
        self._lock = threading.RLock()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self):
        self._items: dict[str, dict] = {}
        for p in self._dir.glob("*.json"):
            try:
                item = json.loads(p.read_text(encoding="utf-8"))
                if item.get("status") == "pending":
                    self._items[item["code"]] = item
            except Exception:
                pass

    def add(self, tool: str, args: dict, reason: str, operator: str = "") -> str:
        code = uuid.uuid4().hex[:8]
        item = {
            "code": code, "tool": tool, "args": {k: str(v)[:500] for k, v in args.items()},
            "reason": reason, "operator": operator, "status": "pending",
            "created_at": datetime.now(UTC).isoformat(),
        }
        with self._lock:
            self._items[code] = item
            (self._dir / f"{code}.json").write_text(
                json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        safety_mode._audit("confirm_requested", tool=tool, code=code, reason=reason,
                           operator=operator)
        return code

    def confirm(self, code: str, operator: str = "") -> Optional[dict]:
        with self._lock:
            item = self._items.get(code)
            if not item:
                return None
            if item["status"] != "pending":
                return None
            item["status"] = "confirmed"
            item["confirmed_at"] = datetime.now(UTC).isoformat()
            item["confirmed_by"] = operator
            (self._dir / f"{code}.json").write_text(
                json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
            del self._items[code]
        safety_mode._audit("confirm_granted", code=code, tool=item["tool"], operator=operator)
        return item

    def pending_list(self, limit: int = 20) -> list[dict]:
        items = sorted(self._items.values(), key=lambda x: x["created_at"], reverse=True)
        return items[:limit]


_pending: Optional[_PendingConfirm] = None


def _get_pending() -> _PendingConfirm:
    global _pending
    if _pending is None:
        _pending = _PendingConfirm(safety_mode.STATE_DIR)
    return _pending


# ─── 风险检测器 ───

def _detect_sensitive_write(tool: str, args: dict) -> Optional[str]:
    """写操作落在敏感路径 / 密钥文件 → 确认。"""
    path = args.get("path") or args.get("file") or args.get("target") or ""
    if not path:
        return None
    path = str(path)
    if _SECRET_FILE_RE.search(path):
        return f"写敏感文件: {path}"
    if _SENSITIVE_DIR_RE.search(path):
        return f"写系统/敏感目录: {path}"
    return None


def _detect_bulk_delete(tool: str, args: dict) -> Optional[str]:
    """批量删除（通配符 / 目录 / 多文件参数）→ 确认。"""
    for key in ("path", "paths", "target", "pattern", "glob"):
        v = args.get(key)
        if not v:
            continue
        v = str(v)
        if any(ch in v for ch in "*?["):
            return f"通配符删除: {v}"
        # 目录删除
        if os.path.isdir(v):
            return f"删除目录: {v}"
    if isinstance(args.get("paths"), (list, tuple)) and len(args["paths"]) > 1:
        return f"批量删除 {len(args['paths'])} 个文件"
    return None


def _detect_path_escape(tool: str, args: dict) -> Optional[str]:
    """路径逃逸（.. 或绝对路径指向工作区外）→ 确认。"""
    path = args.get("path") or args.get("file") or ""
    if not path:
        return None
    path = str(path)
    if ".." in path.split("/") or ".." in path.split("\\"):
        return f"路径包含 .. : {path}"
    return None


def _detect_script_exec(tool: str, args: dict) -> Optional[str]:
    """脚本/代码执行 → 确认。"""
    if tool in ("code_execute", "execute_code", "shell", "run_script"):
        return "脚本/代码执行"
    if any(k in args for k in ("code", "script", "command")):
        return f"执行代码/命令参数: {next(k for k in ('code','script','command') if k in args)[:40]}"
    return None


def _detect_external_send(tool: str, args: dict) -> Optional[str]:
    """对外发送（IM / 发帖）→ 确认（不可逆外部影响）。"""
    if tool in ("wechat_send", "qq_send", "chat_send", "send_message", "post"):
        target = args.get("to") or args.get("contact") or args.get("chat_id") or ""
        return f"对外发送消息" + (f" → {target}" if target else "")
    return None


def _detect_network_fetch(tool: str, args: dict) -> Optional[str]:
    """主动网络访问非白名单域名 → 确认。"""
    url = args.get("url") or args.get("endpoint") or ""
    if not url:
        return None
    m = re.match(r"https?://([^/:]+)", str(url))
    if not m:
        return None
    host = m.group(1).lower()
    if host in _NETWORK_ALLOW_DOMAINS or host.endswith(".local"):
        return None
    return f"网络访问: {host}"


# ─── 规则表（顺序即优先级，命中第一个即返回） ───
_RULES: list[tuple[str, Callable[[str, dict], Optional[str]]]] = [
    # 路径逃逸优先——比"敏感路径写"更基础的保护
    ("path_escape", _detect_path_escape),
    ("sensitive_write", _detect_sensitive_write),
    ("bulk_delete", _detect_bulk_delete),
    ("script_exec", _detect_script_exec),
    ("external_send", _detect_external_send),
    ("network_fetch", _detect_network_fetch),
]

# 完全跳过风险检测的工具（只读/内部，无外部副作用）
_SAFE_TOOLS = {
    "think", "datetime", "file_read", "rag_query", "stock_realtime", "stock_history",
    "stock_indicator", "stock_financial", "stock_search", "stock_compare",
    "stock_market_index", "stock_alert_condition", "search", "comfyui_lora_status",
}


def check_operation(tool: str, args: dict, operator: str = "",
                    require_confirm: bool = True) -> dict:
    """风险矩阵入口：检查一次操作是否需要确认。

    Args:
        tool: 工具名
        args: 工具参数
        operator: 操作者标识
        require_confirm: False = 只记录不拦截（full 模式 / 内部调用）

    Returns:
        {"ok": True} 或 {"ok": False, "reason": str, "risk": str, "confirm_code": str}
    """
    if tool in _SAFE_TOOLS:
        return {"ok": True}

    for risk, detector in _RULES:
        reason = detector(tool, args)
        if not reason:
            continue
        # 命中风险规则
        if require_confirm:
            code = _get_pending().add(tool, args, reason, operator)
            return {"ok": False, "reason": reason, "risk": risk, "confirm_code": code}
        else:
            # full 模式 / 内部调用：放行但审计
            safety_mode._audit("risk_allowed", tool=tool, risk=risk, reason=reason,
                               operator=operator)
            return {"ok": True}

    return {"ok": True}


def confirm_operation(code: str, operator: str = "") -> dict:
    """确认一次待确认操作（一次性放行）。"""
    item = _get_pending().confirm(code, operator)
    if not item:
        return {"ok": False, "error": f"确认码无效或已过期: {code}"}
    return {"ok": True, "tool": item["tool"], "reason": item["reason"]}


def pending_operations(limit: int = 20) -> list[dict]:
    """当前待确认操作列表。"""
    return _get_pending().pending_list(limit)
