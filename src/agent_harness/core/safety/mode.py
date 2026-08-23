"""
会话安全模式 — Default / Full Access 双层架构

灵感来源：WorkBuddy（腾讯云桌面 AI agent）权限双层设计。
对齐业界"沙箱优先、被拦再升级"原则：

  default  — 沙箱优先：危险操作需显式确认（走 risk 矩阵），
             工作区边界强制，文件写自动备份，删除进回收站。
  full     — 全权模式：所有操作放行，但全量审计（操作即留痕）。

切换模式必须记录审计日志（谁、何时、从哪个模式切到哪个模式），
Full Access 是高风险状态，切换前需要用户确认。
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

# ─── 模式常量 ───
MODE_DEFAULT = "default"  # 沙箱优先（默认）
MODE_FULL = "full"        # 全权访问（需显式切换）

_VALID_MODES = (MODE_DEFAULT, MODE_FULL)

# ─── 状态存储 ───
STATE_DIR = Path(os.environ.get("HARNESS_DATA_DIR", Path.home() / ".agent-harness"))
STATE_FILE = STATE_DIR / "safety_mode.json"

_lock = threading.RLock()
_current_mode: str = MODE_DEFAULT

# 审计目录复用 permission.py 约定
AUDIT_DIR = STATE_DIR / "audit"


def _ensure_dirs() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)


def _load_persisted() -> str:
    """启动时读取持久化模式（默认 default，绝不默认 full）。"""
    global _current_mode
    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            mode = data.get("mode", MODE_DEFAULT)
            if mode in _VALID_MODES:
                _current_mode = mode
    except Exception:
        pass  # 读取失败保持默认，安全侧保守
    return _current_mode


def _persist(mode: str) -> None:
    try:
        _ensure_dirs()
        STATE_FILE.write_text(
            json.dumps({"mode": mode, "updated_at": datetime.now(UTC).isoformat()},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def _audit(action: str, **kwargs) -> None:
    """安全事件审计（模式切换、确认、拒绝都留痕）。"""
    try:
        _ensure_dirs()
        entry = {
            "ts": datetime.now(UTC).strftime("%Y%m%d_%H%M%S%f"),
            "action": action,
            **kwargs,
        }
        path = AUDIT_DIR / f"safety_{entry['ts']}.json"
        path.write_text(json.dumps(entry, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
    except OSError:
        pass


def get_mode() -> str:
    """当前安全模式。"""
    with _lock:
        return _current_mode


def is_full_access() -> bool:
    """是否 Full Access 全权模式。"""
    return get_mode() == MODE_FULL


def set_mode(mode: str, source: str = "api", operator: str = "unknown") -> dict:
    """切换安全模式。

    Args:
        mode: 'default' 或 'full'
        source: 调用来源 (api / cli / internal)
        operator: 操作者标识（用户 ID 或客户端标识）

    Returns:
        {"ok": True, "mode": <新模式>} 或 {"ok": False, "error": ...}
    """
    global _current_mode
    mode = (mode or "").strip().lower()
    if mode not in _VALID_MODES:
        return {"ok": False, "error": f"无效模式: {mode}，可选: {', '.join(_VALID_MODES)}"}

    with _lock:
        old = _current_mode
        if old == mode:
            return {"ok": True, "mode": mode, "changed": False}
        _current_mode = mode
        _persist(mode)
        _audit("mode_switch", from_mode=old, to_mode=mode, source=source, operator=operator)
        return {"ok": True, "mode": mode, "changed": True, "from": old}


# 启动时加载持久化模式
_load_persisted()
