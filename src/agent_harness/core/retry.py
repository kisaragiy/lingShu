"""
指数退避重试装饰器 — 外部服务调用偶发失败自动重试。

用法:
    @with_retry(max_attempts=3, base_delay=1.0)
    def call_comfyui(...):
        ...
"""
from __future__ import annotations

import asyncio
import functools
import logging
import random
import time
from typing import Any, Callable, Type

logger = logging.getLogger(__name__)

# 默认重试的异常类型
_RETRYABLE = (ConnectionError, TimeoutError, OSError)


def with_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable_exceptions: tuple[Type[Exception], ...] = _RETRYABLE,
) -> Callable:
    """指数退避重试装饰器。

    Args:
        max_attempts: 最大尝试次数
        base_delay: 初始延迟（秒）
        max_delay: 最大延迟（秒）
        retryable_exceptions: 可重试的异常类型
    """
    def decorator(fn: Callable) -> Callable:
        # 同步函数
        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exc = e
                    if attempt < max_attempts:
                        delay = min(base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5), max_delay)
                        logger.warning(
                            "retry_sync",
                            extra={"fn": fn.__name__, "attempt": attempt, "delay": round(delay, 1), "error": str(e)},
                        )
                        time.sleep(delay)
            raise last_exc  # type: ignore

        # 异步函数
        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await fn(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exc = e
                    if attempt < max_attempts:
                        delay = min(base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5), max_delay)
                        logger.warning(
                            "retry_async",
                            extra={"fn": fn.__name__, "attempt": attempt, "delay": round(delay, 1), "error": str(e)},
                        )
                        await asyncio.sleep(delay)
            raise last_exc  # type: ignore

        if asyncio.iscoroutinefunction(fn):
            return async_wrapper
        return sync_wrapper

    return decorator
