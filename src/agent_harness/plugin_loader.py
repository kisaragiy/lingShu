"""Plugin Loader — dynamically load external tools from plugins/ directory.

Scan order: ~/.agent-harness/plugins/*.py
Each plugin is a Python file that can call register_tool() from tools.registry.
Plugins are loaded once at startup. Errors in one plugin don't block others.

Usage:
    from .plugin_loader import load_plugins, list_plugins
    load_plugins()  # Called at startup
    plugins = list_plugins()  # [(name, description, error?), ...]
"""

import importlib
import importlib.util
import os
import sys
import traceback
from pathlib import Path

_HARNESS_PLUGINS_DIR = Path(os.environ.get(
    "HARNESS_PLUGINS_DIR",
    str(Path.home() / ".agent-harness" / "plugins"),
))
_loaded_plugins: list[dict] = []
# 能力缝 Provider 归属: plugin_name -> [tool_names provided]
_plugin_tool_map: dict[str, list[str]] = {}


def _ensure_dir():
    _HARNESS_PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    # Create example plugin if directory is empty
    example = _HARNESS_PLUGINS_DIR / "example_plugin.py"
    if not example.exists() and not list(_HARNESS_PLUGINS_DIR.glob("*.py")):
        example.write_text(
            '"""Example plugin — template for creating custom tools."""\n'
            "from agent_harness.core.tools.registry import register_tool\n\n\n"
            "def _tool_my_echo(text: str) -> str:\n"
            '    """Echo the input text back."""\n'
            '    return "[echo] %s" % text\n\n\n'
            'register_tool("my_echo", _tool_my_echo, {\n'
            '    "description": "回显输入文本（示例插件工具）",\n'
            '    "properties": {"text": "string"},\n'
            '}, privilege="read-only")\n'
        )


def load_plugins() -> list[dict]:
    """Load all plugins from the plugins directory.

    能力缝：核心不做任何"特权"——只做能力注册/发现；插件作为 Provider 自助
    register_tool() 注册能力。加载时 diff TOOL_REGISTRY 归属每个插件提供的工具。

    Returns:
        List of {name, file, success, error?, tools?}
    """
    global _loaded_plugins, _plugin_tool_map
    _loaded_plugins = []
    _plugin_tool_map = {}
    _ensure_dir()

    from agent_harness.core.tools.registry import TOOL_REGISTRY

    for pyfile in sorted(_HARNESS_PLUGINS_DIR.glob("*.py")):
        if pyfile.name.startswith("_"):
            continue
        info = {"file": pyfile.name, "name": pyfile.stem, "success": False}

        try:
            # Add plugins dir to sys.path for import
            plugin_dir = str(_HARNESS_PLUGINS_DIR)
            if plugin_dir not in sys.path:
                sys.path.insert(0, plugin_dir)

            # 快照加载前的能力集 → diff 出该插件提供的工具（Provider 归属）
            before = set(TOOL_REGISTRY.keys())

            spec = importlib.util.spec_from_file_location(pyfile.stem, str(pyfile))
            if spec is None or spec.loader is None:
                info["error"] = "无法加载 spec"
                _loaded_plugins.append(info)
                continue

            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            info["success"] = True

            after = set(TOOL_REGISTRY.keys())
            provided = sorted(after - before)
            _plugin_tool_map[pyfile.stem] = provided
            info["tools"] = provided

        except Exception as e:
            info["error"] = f"{type(e).__name__}: {str(e)}"
            info["traceback"] = traceback.format_exc()[-500:]

        _loaded_plugins.append(info)

    return _loaded_plugins


def list_plugins() -> list[dict]:
    """Return loaded plugin list."""
    return _loaded_plugins.copy()


def get_plugin_tool_map() -> dict[str, list[str]]:
    """Return provider attribution: plugin_name -> [tool_names].

    能力缝三角色的 Provider 侧——调用方/前端可看到"哪个插件提供哪些能力"。
    """
    return {k: list(v) for k, v in _plugin_tool_map.items()}


def get_plugin_tools() -> list[str]:
    """Get tool names contributed by plugins (provider-attributed)."""
    return sorted({t for tools in _plugin_tool_map.values() for t in tools})
