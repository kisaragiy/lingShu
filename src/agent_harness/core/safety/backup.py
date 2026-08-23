"""
文件保护 — 写前自动备份 + 删除保护（回收站）

对齐 WorkBuddy "改文件前自动备份副本" + "删除保护"设计：

  backup_before_write(path)  — 目标文件已存在且将被覆盖时，先复制到
                               ~/.agent-harness/backup/<date>/ 下留副本。
  safe_delete(path)          — 删除操作不直接 rm，移入回收站
                               ~/.agent-harness/trash/，可恢复。

策略:
  - 自动备份：仅对"覆盖写"生效（追加写不影响旧内容，不备份）；
    备份保留最近 N 份（默认 10），超限轮转最旧的。
  - 回收站：移动失败（跨盘等）时降级为复制+删除原文件，仍不丢数据。
"""
from __future__ import annotations

import os
import shutil
import time
from datetime import datetime
from pathlib import Path

from . import mode as safety_mode

_BACKUP_KEEP = int(os.environ.get("HARNESS_BACKUP_KEEP", "10"))
TRASH_DIR = safety_mode.STATE_DIR / "trash"


def _backup_dir() -> Path:
    d = safety_mode.STATE_DIR / "backup" / datetime.now().strftime("%Y%m%d")
    d.mkdir(parents=True, exist_ok=True)
    return d


def backup_before_write(path: str | os.PathLike, mode: str = "w") -> Path | None:
    """覆盖写前备份。目标不存在或非覆盖模式时跳过。

    Returns: 备份文件路径；未备份返回 None。
    """
    p = Path(path)
    if mode not in ("w", "overwrite", "write"):
        return None
    if not p.is_file():
        return None

    try:
        backup_dir = _backup_dir()
        ts = time.strftime("%H%M%S")
        target = backup_dir / f"{p.stem}.{ts}.bak{p.suffix}"
        shutil.copy2(p, target)
        _rotate(backup_dir)
        safety_mode._audit("backup_created", path=str(p), backup=str(target))
        return target
    except OSError:
        return None


def _rotate(backup_dir: Path) -> None:
    """保留最近 N 份备份，删除更旧的（按修改时间）。"""
    try:
        files = sorted(backup_dir.glob("*.bak*"), key=lambda f: f.stat().st_mtime)
        while len(files) > _BACKUP_KEEP:
            files[0].unlink(missing_ok=True)
            files = files[1:]
    except OSError:
        pass


def safe_delete(path: str | os.PathLike) -> dict:
    """删除保护：移入回收站而非直接删除。

    Returns:
        {"ok": True, "trashed": <回收站路径>, "original": <原路径>}
        或 {"ok": False, "error": ...}
    """
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"路径不存在: {path}"}

    try:
        trash = TRASH_DIR / datetime.now().strftime("%Y%m%d")
        trash.mkdir(parents=True, exist_ok=True)
        target = trash / f"{p.name}.{time.strftime('%H%M%S')}"
        if p.is_dir():
            shutil.move(str(p), str(target))
        else:
            try:
                shutil.move(str(p), str(target))
            except OSError:
                # 跨盘移动失败 → 复制+删除原文件，数据仍保留
                shutil.copy2(p, target)
                p.unlink()
        safety_mode._audit("trashed", path=str(p), target=str(target))
        return {"ok": True, "trashed": str(target), "original": str(p)}
    except OSError as e:
        return {"ok": False, "error": f"删除保护失败: {e}"}
