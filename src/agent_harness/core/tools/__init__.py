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
from .semantic import (
    clear_semantic_validators,
    matches_type,
    monotonic,
    non_negative,
    numeric_range,
    one_of,
    register_semantic_validator,
    run_semantic_validators,
    unregister_semantic_validator,
)

# Optional: parallel executor
with contextlib.suppress(ImportError):
    from . import parallel as _parallel

__all__ = [
    "TOOL_REGISTRY", "register_tool", "call_tool", "validate_result",
    "_capture_error_screenshot",
    # P0-5 语义校验（anomaly gate）
    "register_semantic_validator", "unregister_semantic_validator",
    "clear_semantic_validators", "run_semantic_validators",
    "non_negative", "numeric_range", "matches_type", "one_of", "monotonic",
    "_desktop", "_misc", "_web", "_pattern_scan",
    "_mcp_health", "_reasoning", "_parallel",
]