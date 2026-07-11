"""Tools — import all tool modules to trigger register_tool() calls.

Each module registers its tools at import time via the global TOOL_REGISTRY.
"""

# Import tool modules to trigger registration
import contextlib

from . import (
    comfyui as _comfyui,
    desktop as _desktop,
    misc as _misc,
    web as _web,
)
from .registry import (
    TOOL_REGISTRY,
    _capture_error_screenshot,
    call_tool,
    register_tool,
    validate_result,
)

# Optional: parallel executor
with contextlib.suppress(ImportError):
    from . import parallel as _parallel

__all__ = [
    "TOOL_REGISTRY", "register_tool", "call_tool", "validate_result",
    "_capture_error_screenshot",
]
