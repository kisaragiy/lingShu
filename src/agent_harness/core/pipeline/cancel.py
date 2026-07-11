"""Task cancellation — shared module for graceful task interruption.

Both graph_multi.py and supervisor.py need to check cancellation status.
Putting this in a separate module avoids circular imports.
"""

import threading

_cancel_event: threading.Event | None = None


class CancelledError(Exception):
    """Raised when the user cancels the current task."""
    pass


def set_cancel_event(event: threading.Event | None):
    global _cancel_event
    _cancel_event = event


def clear_cancel_event():
    global _cancel_event
    _cancel_event = None


def is_cancelled() -> bool:
    return _cancel_event is not None and _cancel_event.is_set()


def check_cancelled():
    if is_cancelled():
        raise CancelledError("Task cancelled by user")
