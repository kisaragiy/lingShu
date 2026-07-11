"""
MCP 工具调用工具 — 通过 mcporter 调用第三方 MCP 服务

从 graph.py 迁出，供 pipeline 节点和 desktop 工具使用。
"""

import os
import subprocess


def call_mcp_tool(server: str, tool: str, args: dict) -> dict:
    """通过 mcporter 调用 MCP 工具"""
    mcporter = os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "npm", "mcporter.cmd")
    cmd = [mcporter, "call", f"{server}.{tool}"]
    for k, v in args.items():
        if isinstance(v, bool):
            cmd.extend(["--bool", k])
        elif isinstance(v, int):
            cmd.extend(["--int", k])
        else:
            cmd.extend(["--str", k, str(v)])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                           encoding='utf-8', errors='replace')
        return {"output": r.stdout, "error": r.stderr, "exit_code": r.returncode}
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    except FileNotFoundError:
        return {"error": "mcporter not found"}


def call_playwright(tool: str, args: dict, timeout: int = 30) -> str:
    """调用 Playwright MCP 工具，返回可读文本结果"""
    mcporter = os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "npm", "mcporter.cmd")
    cmd = [mcporter, "call", f"playwright.{tool}"]
    for k, v in args.items():
        if isinstance(v, bool):
            cmd.extend(["--bool", k])
        elif isinstance(v, int):
            cmd.extend(["--int", k])
        else:
            cmd.extend(["--str", k, str(v)])
    env = os.environ.copy()
    env["PATH"] = r"C:\node-v22;" + env.get("PATH", "")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env,
                                encoding='utf-8', errors='replace')
        if result.returncode != 0:
            return f"[playwright] 失败: {result.stderr[:200]}"
        output = result.stdout.strip()
        return output[:3000] if output else "(empty)"
    except subprocess.TimeoutExpired:
        return "[playwright] 操作超时"
    except Exception as e:
        return f"[playwright] 异常: {e}"
