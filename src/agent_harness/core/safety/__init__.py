"""
安全护栏模块 — WorkBuddy 权限双层架构移植（P0）

├── mode.py    会话安全模式: default(沙箱优先) / full(全权+全量审计)
├── risk.py    危险操作确认矩阵: 按参数分类检测 + 待确认队列
└── backup.py  写前自动备份 + 删除保护(回收站)

对外统一入口:
    from agent_harness.core.safety import get_mode, set_mode, is_full_access
    from agent_harness.core.safety import check_operation, confirm_operation, pending_operations
    from agent_harness.core.safety import backup_before_write, safe_delete
"""
from .mode import get_mode, set_mode, is_full_access, MODE_DEFAULT, MODE_FULL
from .risk import (
    check_operation,
    confirm_operation,
    pending_operations,
)
from .backup import backup_before_write, safe_delete

__all__ = [
    "get_mode", "set_mode", "is_full_access", "MODE_DEFAULT", "MODE_FULL",
    "check_operation", "confirm_operation", "pending_operations",
    "backup_before_write", "safe_delete",
]
