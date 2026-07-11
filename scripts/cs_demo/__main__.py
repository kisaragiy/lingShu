"""CS Demo 独立启动器 — 用于 PyInstaller 打包的最小启动器。

双击运行，自动找可用端口并打开浏览器。
"""
import os
import sys
import webbrowser
import threading
import socket
import time

HOST = "127.0.0.1"


def find_free_port(start=8788, max_tries=20):
    for port in range(start, start + max_tries):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind((HOST, port))
            s.close()
            return port
        except OSError:
            continue
    return start


def open_browser(url):
    time.sleep(2.0)
    webbrowser.open(url)


def main():
    port = find_free_port()
    url = f"http://{HOST}:{port}/cs-demo"

    print("╔════════════════════════════════════╗")
    print("║    灵枢智能客服 — CS Demo          ║")
    print("║    LingShu AI Customer Service     ║")
    print("╚════════════════════════════════════╝")
    print(f"\n  启动中... (port={port})")

    threading.Thread(target=open_browser, args=(url,), daemon=True).start()

    import uvicorn
    from cs_demo import app
    uvicorn.run(app, host=HOST, port=port, log_level="info")


if __name__ == "__main__":
    main()
