"""MCP Server — expose tools as Model Context Protocol interface.

All 41+ tools from the registry are exposed as MCP tools.
Any MCP client (Hermes, Claude Desktop, etc.) can call them natively.

Protocol: JSON-RPC 2.0 over stdio
"""

import json
import os
import sys
import traceback

# Ensure imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_harness.tools.registry import TOOL_REGISTRY
from agent_harness.tools.registry import call_tool as _call_tool


def _rpc_error(id_val, code: int, message: str):
    return {"jsonrpc": "2.0", "id": id_val, "error": {"code": code, "message": message}}


def _rpc_response(id_val, result):
    return {"jsonrpc": "2.0", "id": id_val, "result": result}


def handle_request(req: dict) -> dict | None:
    """Handle a single JSON-RPC request. Returns response or None for notifications."""
    method = req.get("method", "")
    req_id = req.get("id")
    params = req.get("params", {})

    if method == "initialize":
        return _rpc_response(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": "agent-harness-mcp",
                "version": "0.2.0",
            },
        })

    if method == "notifications/initialized":
        return None  # No response for notifications

    if method == "tools/list":
        tools = []
        for name, entry in sorted(TOOL_REGISTRY.items()):
            schema = entry["schema"].copy()
            tools.append({
                "name": name,
                "description": schema.pop("description", ""),
                "inputSchema": schema,
            })
        return _rpc_response(req_id, {"tools": tools})

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        if tool_name not in TOOL_REGISTRY:
            return _rpc_error(req_id, -32602, f"Unknown tool: {tool_name}")

        try:
            result = _call_tool(tool_name, **arguments)
            content = json.dumps(result.get("data", result), ensure_ascii=False)
            return _rpc_response(req_id, {
                "content": [{"type": "text", "text": content}],
            })
        except Exception as e:
            return _rpc_response(req_id, {
                "content": [{"type": "text", "text": f"Error: {e}"}],
                "isError": True,
            })

    return _rpc_error(req_id, -32601, f"Method not found: {method}")


def main():
    """MCP stdio server main loop."""
    print("Agent Harness MCP Server v0.2.0", file=sys.stderr)
    print(f"Tools loaded: {len(TOOL_REGISTRY)}", file=sys.stderr)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            resp = handle_request(req)
            if resp is not None:
                sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except json.JSONDecodeError:
            pass
        except Exception:
            traceback.print_exc(file=sys.stderr)


if __name__ == "__main__":
    main()
