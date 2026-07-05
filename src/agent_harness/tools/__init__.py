"""Tools — import all tool modules to trigger register_tool() calls.

Each module registers its tools at import time via the global TOOL_REGISTRY.
"""

from .registry import (
    TOOL_REGISTRY,
    register_tool,
    call_tool,
    validate_result,
    _capture_error_screenshot,
)

# Import tool modules to trigger registration
from . import misc      # think, code_execute, github_issues, file ops, RAG, stock, datetime, summarize
from . import web       # search, fetch, web_browse, web_scrape, agent_browser
from . import desktop   # desktop_gui, browser_automation, wechat_send, qq_send, chat_send, app_launch
from . import comfyui   # comfyui_text2img, img2img, character_sheet, scene_grid, etc.

# Optional: parallel executor
try:
    from . import parallel
except ImportError:
    pass

__all__ = [
    "TOOL_REGISTRY", "register_tool", "call_tool", "validate_result",
    "_capture_error_screenshot",
]
