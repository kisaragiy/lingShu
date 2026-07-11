"""CS Demo 独立启动器 — 用于 PyInstaller 打包。

最小化启动，只加载 CS Demo 所需的路由和依赖。
"""
import os
import sys
import webbrowser
import threading
import time

HOST = "127.0.0.1"
PORT = 8788  # fallback, will find available

def _find_free_port(start=8788, max_tries=20):
    import socket
    for port in range(start, start + max_tries):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind((HOST, port))
            s.close()
            return port
        except OSError:
            continue
    return start  # give up, let uvicorn handle it

def _open_browser(url):
    time.sleep(2.5)
    webbrowser.open(url)
    print(f"\n  🎧 灵枢智能客服 Demo 已启动!")
    print(f"  📍 {url}")
    print(f"  关闭本窗口即可停止服务\n")

def main():
    port = _find_free_port()
    url = f"http://{HOST}:{port}/cs-demo"

    os.environ.setdefault("HARNESS_API_HOST", HOST)
    os.environ.setdefault("HARNESS_API_PORT", str(port))

    print("╔════════════════════════════════════╗")
    print("║    灵枢智能客服 — CS Demo          ║")
    print("║    LingShu AI Customer Service     ║")
    print("╚════════════════════════════════════╝")
    print(f"\n  启动中... (port={port})")

    threading.Thread(target=_open_browser, args=(url,), daemon=True).start()

    import uvicorn
    from src.agent_harness.api_fastapi import app
    uvicorn.run(app, host=HOST, port=port, log_level="info")

if __name__ == "__main__":
    main()
