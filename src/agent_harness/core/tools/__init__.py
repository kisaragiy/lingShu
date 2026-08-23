"""Core tools — import all tool modules to trigger register_tool() calls.

Each module registers its tools at import time via the global TOOL_REGISTRY.
"""
import contextlib

from . import (
    desktop as _desktop,
)
from . import (
    misc as _misc,
)
from . import (
    web as _web,
)
from . import (
    rev_utils as _rev_utils,
    pattern_scan as _pattern_scan,
    mcp_health as _mcp_health,
    reasoning as _reasoning,
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
    "_desktop", "_misc", "_web", "_rev_utils", "_pattern_scan",
    "_mcp_health", "_reasoning", "_parallel",
]