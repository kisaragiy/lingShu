"""
结构化日志 — 替代 print()。

用法:
    from agent_harness.core.logging import log
    log.info("generate", model="sdxl", duration=12.3, result="ok")
    log.warning("vlm_unavailable", fallback="text_only")
    log.error("comfyui_down", detail="Connection refused")
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

_RESERVED = {"msg", "level", "time"}


class StructuredLogger:
    """结构化日志器 —— 每行 JSON，可直接 grep 分析。"""

    def __init__(self, name: str = "harness", level: str = "INFO"):
        self._logger = logging.getLogger(name)
        self._logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        self._handler = logging.StreamHandler(sys.stdout)
        self._handler.setFormatter(logging.Formatter("%(message)s"))
        if not self._logger.handlers:
            self._logger.addHandler(self._handler)

    def _emit(self, level: str, msg: str, **kwargs: Any) -> None:
        record = {
            "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": level,
            "msg": msg,
        }
        for k, v in kwargs.items():
            if k not in _RESERVED:
                record[k] = v
            else:
                record[f"_{k}"] = v
        line = json.dumps(record, ensure_ascii=False, default=str)
        getattr(self._logger, level.lower(), self._logger.info)(line)

    def info(self, msg: str, **kwargs: Any) -> None:
        self._emit("INFO", msg, **kwargs)

    def warning(self, msg: str, **kwargs: Any) -> None:
        self._emit("WARNING", msg, **kwargs)

    def error(self, msg: str, **kwargs: Any) -> None:
        self._emit("ERROR", msg, **kwargs)

    def debug(self, msg: str, **kwargs: Any) -> None:
        self._emit("DEBUG", msg, **kwargs)


# 全局单例
_logger: StructuredLogger | None = None


def get_logger(name: str = "harness") -> StructuredLogger:
    global _logger
    if _logger is None:
        _logger = StructuredLogger(name)
    return _logger


def log_info(msg: str, **kwargs: Any) -> None:
    get_logger().info(msg, **kwargs)


def log_warning(msg: str, **kwargs: Any) -> None:
    get_logger().warning(msg, **kwargs)


def log_error(msg: str, **kwargs: Any) -> None:
    get_logger().error(msg, **kwargs)
