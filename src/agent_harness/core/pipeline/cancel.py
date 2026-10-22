"""Task cancellation — shared module for graceful task interruption.

Both graph_multi.py and supervisor.py need to check cancellation status.
Putting this in a separate module avoids circular imports.

ENG-21：取消信号**按 task/session 作用域**注册（`_events: dict[task_id, Event]`）。
此前是一个全局单点 `_cancel_event`：并发下后启动的任务会覆盖它，取消 A 变成取消 B
（或谁都取消不了）。消费方（worker/supervisor/graph_multi）现在把 session_id 作为
task_id 一路透传下来，`is_cancelled(task_id)` 只读自己那个 event。

向后兼容：`set_cancel_event`/`clear_cancel_event`/`is_cancelled()` 旧签名保留，
内部转发到 `_default_event`（CLI / 脚本 / 旧测试没有 task_id 上下文时走这条）。
"""

import threading

# task_id → 该任务的取消信号
_events: dict[str, threading.Event] = {}
_lock = threading.Lock()

# 无 task_id 上下文时的兜底（CLI/脚本）；API 服务不再用它
_default_event: threading.Event | None = None


class CancelledError(Exception):
    """Raised when the user cancels the current task."""

    pass


def register_task(task_id: str) -> threading.Event:
    """为 task_id 注册一个全新的取消信号，并返回它（供调用方持有/查询）。"""
    event = threading.Event()
    with _lock:
        _events[task_id] = event
    return event


def unregister_task(task_id: str) -> None:
    """任务收尾：注销信号。**不 set** —— 正常结束不该留下"已取消"的痕迹。"""
    with _lock:
        _events.pop(task_id, None)


def cancel_task(task_id: str) -> bool:
    """只取消 task_id 对应的任务。返回 False 表示该任务不在运行中。"""
    with _lock:
        event = _events.get(task_id)
    if event is None:
        return False
    event.set()
    return True


def is_cancelled(task_id: str | None = None) -> bool:
    """查询取消状态。

    带 task_id → 只查该任务的信号（未注册视为未取消）；
    不带 task_id → 回落到全局兜底事件（向后兼容）。
    """
    if task_id:
        with _lock:
            event = _events.get(task_id)
        return event is not None and event.is_set()
    return _default_event is not None and _default_event.is_set()


def check_cancelled(task_id: str | None = None) -> None:
    if is_cancelled(task_id):
        raise CancelledError("Task cancelled by user")


# ─── 旧 API（薄 wrapper，转发到全局兜底事件）─────────────────────────────


def set_cancel_event(event: threading.Event | None) -> None:
    global _default_event
    _default_event = event


def clear_cancel_event() -> None:
    global _default_event
    _default_event = None
