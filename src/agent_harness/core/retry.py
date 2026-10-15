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


# ─── P0-4: 无进展检测（连续同 tool+同参调用 → 熔断）───

class NoProgressError(Exception):
    """连续 K 次相同调用（同指纹）——判定为无进展，熔断停止。

    指纹由 ``key_fn`` 决定（默认取参数 repr）；指纹变化即计数归零。
    """

    code = "NO_PROGRESS"
    recoverable = False

    def __init__(self, message: str = "", *, fingerprint: Any = None, repeats: int = 0):
        super().__init__(message)
        self.fingerprint = fingerprint
        self.repeats = repeats


def _default_call_key(args: tuple, kwargs: dict) -> str:
    """默认调用指纹：参数 repr（对不可哈希对象也安全）。"""
    try:
        return repr((args, tuple(sorted(kwargs.items()))))
    except Exception:
        return repr(args)


def with_no_progress_guard(
    max_repeats: int = 3,
    key_fn: Callable[..., Any] | None = None,
) -> Callable:
    """无进展熔断装饰器。

    连续 ``max_repeats`` 次「相同指纹」的调用 → 抛 :class:`NoProgressError`，
    且**熔断那一次不会执行原函数**（避免浪费调用）。指纹变化时计数归零。

    Args:
        max_repeats: 连续相同调用达到该次数即熔断（>=1）
        key_fn: 可注入的调用指纹函数 ``key_fn(*args, **kwargs) -> hashable``；
                默认取 ``(args, sorted(kwargs))`` 的 repr。便于按「tool 名」等
                自定义粒度判重，也便于测试注入。
    """
    if max_repeats < 1:
        raise ValueError("max_repeats 必须 >= 1")
    key_fn = key_fn or (lambda *a, **k: _default_call_key(a, k))

    def decorator(fn: Callable) -> Callable:
        state: dict = {"key": object(), "count": 0}

        def _update(fp: Any) -> int:
            if fp == state["key"]:
                state["count"] += 1
            else:
                state["key"] = fp
                state["count"] = 1
            return state["count"]

        def _check(fp: Any, name: str) -> None:
            if _update(fp) >= max_repeats:
                raise NoProgressError(
                    f"连续 {state['count']} 次相同调用（无进展），熔断: {name}",
                    fingerprint=fp, repeats=state["count"],
                )

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            _check(key_fn(*args, **kwargs), fn.__name__)
            return fn(*args, **kwargs)

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            _check(key_fn(*args, **kwargs), fn.__name__)
            return await fn(*args, **kwargs)

        if asyncio.iscoroutinefunction(fn):
            return async_wrapper
        return sync_wrapper

    return decorator
