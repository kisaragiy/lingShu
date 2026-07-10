#!/usr/bin/env python3
"""灵枢智能客服 Demo — 一键启动脚本。

用法:
    python run_cs_demo.py        # 启动 + 自动打开浏览器
    python run_cs_demo.py --no-open  # 仅启动，不打开浏览器

依赖:
    pip install agent-harness    # 或从项目根目录运行
"""
import os
import sys
import webbrowser
import threading
import time

HOST = "127.0.0.1"
PORT = 8788
URL = f"http://{HOST}:{PORT}/cs-demo"


def _open_browser():
    """Open browser after server is ready."""
    time.sleep(2.5)
    webbrowser.open(URL)
    print(f"\n  🎧 灵枢智能客服 Demo 已启动!")
    print(f"  📍 {URL}")
    print(f"  按 Ctrl+C 停止服务\n")


def main():
    # Parse args
    no_open = "--no-open" in sys.argv

    # Import server module
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    os.environ.setdefault("HARNESS_API_HOST", HOST)
    os.environ.setdefault("HARNESS_API_PORT", str(PORT))

    print("╔════════════════════════════════════╗")
    print("║    灵枢智能客服 — CS Demo          ║")
    print("║    LingShu AI Customer Service     ║")
    print("╚════════════════════════════════════╝")
    print(f"\n  启动中... (host={HOST}, port={PORT})")

    # Open browser in background
    if not no_open:
        threading.Thread(target=_open_browser, daemon=True).start()

    # Start the FastAPI server with uvicorn
    import uvicorn
    from src.agent_harness.api_fastapi import app

    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
