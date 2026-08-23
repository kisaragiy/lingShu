"""MCP Health Check — I call this before using any MCP tool to verify connectivity.

Industry best practice: always probe before you call. Saves tokens on dead-connection timeouts.
"""
import json, os, socket, time
from pathlib import Path

# Known MCP server endpoints
MCP_SERVERS = {
    "langgraph-tools": {"host": "localhost", "port": 8788},
    "comfyui": {"host": "localhost", "port": 8188},
    "searxng": {"host": "localhost", "port": 4000},
    "blender": {"host": "localhost", "port": 8765},
}

def _check_server(host: str, port: int, timeout: int = 3) -> dict:
    try:
        t0 = time.time()
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        latency = int((time.time() - t0) * 1000)
        s.close()
        return {"alive": True, "latency_ms": latency}
    except Exception as e:
        return {"alive": False, "error": str(e)[:80]}

def _tool_mcp_health(server_name: str = "") -> str:
    """Check which MCP servers are reachable before I call them."""

    targets = {server_name: MCP_SERVERS[server_name]} if server_name else MCP_SERVERS
    results = {}
    for name, cfg in targets.items():
        results[name] = _check_server(cfg["host"], cfg["port"])
    return json.dumps({"ok": True, "servers": results}, indent=2, ensure_ascii=False)

from .registry import register_tool
register_tool("mcp_health", _tool_mcp_health, {
    "description": "🔌 MCP 健康检测 — 调用 MCP 工具前先查服务器存活，避免超时浪费 token",
    "properties": {"server_name": {"type": "string", "description": "指定服务器名（留空=全查）"}},
}, privilege="read-only")