"""Tool config — per-tool enable/disable state.

Stores disabled tools list in ~/.agent-harness/tool_config.json.
All tools are enabled by default. Only explicitly disabled tools are stored.
"""

import json
import os
import threading
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("HARNESS_DATA_DIR",
    Path.home() / ".agent-harness"))
CONFIG_FILE = CONFIG_DIR / "tool_config.json"

_lock = threading.Lock()

# In-memory cache (also used by tool registration)
_disabled_tools: set[str] = set()


def _load():
    global _disabled_tools
    try:
        if CONFIG_FILE.exists():
            with _lock, open(CONFIG_FILE, "r") as f:
                data = json.load(f)
            _disabled_tools = set(data.get("disabled_tools", []))
        else:
            _disabled_tools = set()
    except Exception:
        _disabled_tools = set()


def _save():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with _lock, open(CONFIG_FILE, "w") as f:
        json.dump({"disabled_tools": sorted(_disabled_tools)}, f, indent=2)


def is_tool_enabled(tool_name: str) -> bool:
    """Check if a tool is enabled (not in disabled list)."""
    if not _disabled_tools:
        _load()
    return tool_name not in _disabled_tools


def toggle_tool(tool_name: str, enable: bool | None = None) -> bool:
    """Toggle a tool's enabled state.

    Args:
        tool_name: Tool name
        enable: True=enable, False=disable, None=toggle

    Returns:
        New state (True=enabled, False=disabled)
    """
    if not _disabled_tools:
        _load()

    currently_enabled = tool_name not in _disabled_tools
    if enable is None:
        enable = not currently_enabled

    if enable:
        _disabled_tools.discard(tool_name)
    else:
        _disabled_tools.add(tool_name)

    _save()
    return enable


def list_disabled() -> list[str]:
    """Get list of disabled tool names."""
    if not _disabled_tools:
        _load()
    return sorted(_disabled_tools)


# Load on import
_load()
