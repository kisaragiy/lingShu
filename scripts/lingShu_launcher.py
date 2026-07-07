"""灵枢 (LingShu) — Desktop Application Launcher

Launches a native Windows window with the 灵枢 frontend embedded.
No browser needed. Server runs in background, window handles shutdown.
"""

import os
import sys
import socket
import logging
import threading
import time
import json
from pathlib import Path


# ─── Force UTF-8 for console output (avoids GBK encoding errors) ───
if hasattr(sys.stdout, 'reconfigure'):
    try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception: pass
if hasattr(sys.stderr, 'reconfigure'):
    try: sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception: pass


# ─── Paths ───

def _app_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

APP_DIR = _app_dir()
LOG_DIR = APP_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)


# ─── Logging ───

log_file = LOG_DIR / "lingShu.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(str(log_file), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("lingShu")


# ─── Port detection ───

def find_free_port(start: int = 8788, max_attempts: int = 20) -> int:
    for port in range(start, start + max_attempts):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        try:
            s.connect(("127.0.0.1", port))
            s.close()
            logger.info("Port %d in use, trying next...", port)
        except (socket.timeout, ConnectionRefusedError, OSError):
            s.close()
            return port
    raise RuntimeError("No available port found")


# ─── Server runner ───

_server = None
_server_port = None
_server_ready = threading.Event()


def run_server(port: int):
    """Start the FastAPI server on the given port. Runs in background thread."""
    global _server_port
    _server_port = port
    os.environ["HARNESS_API_PORT"] = str(port)

    import uvicorn
    from agent_harness.api_fastapi import app

    logger.info("Starting 灵枢 server on port %d...", port)

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )
    global _server
    _server = uvicorn.Server(config)
    _server_ready.set()  # Signal that server is about to start
    _server.run()


def wait_for_server(timeout: int = 15) -> bool:
    """Wait for the HTTP server to respond."""
    port = _server_port or 8788
    for i in range(timeout):
        time.sleep(1)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        try:
            s.connect(("127.0.0.1", port))
            s.close()
            logger.info("Server ready on port %d", port)
            return True
        except:
            continue
    return False


def stop_server():
    """Gracefully stop the server."""
    global _server
    if _server:
        logger.info("Shutting down server...")
        _server.should_exit = True


# ─── Window (pywebview) ───

def create_window(port: int):
    """Create the native application window."""
    import webview

    url = "http://127.0.0.1:%d" % port

    # Window settings
    window = webview.create_window(
        title="灵枢 — LingShu Agent",
        url=url,
        width=1100,
        height=750,
        min_size=(800, 600),
        resizable=True,
        fullscreen=False,
        text_select=True,
        confirm_close=True,
    )

    # Run the window (blocking — returns when window closes)
    webview.start(
        debug=False,
        http_server=False,  # pywebview does NOT need to serve — our FastAPI does
        private_mode=False,
        storage_path=str(APP_DIR / "webview_data"),
    )


# ─── Splash / Loading helper ───

def show_loading_screen(port: int):
    """Show a simple loading HTML while server starts."""
    # This is a fallback: if the page takes too long, pywebview will show a blank
    # Instead, we wait for the server before creating the window
    pass


# ─── Main ───

def main():
    print("")
    print("  灵枢 — LingShu Agent")
    print("  " + ("-" * 40))
    logger.info("Starting...")

    # 1. Find available port
    try:
        port = find_free_port()
    except RuntimeError as e:
        logger.error("Port detection failed: %s", e)
        input("端口不可用，按 Enter 退出...")
        sys.exit(1)

    # 2. Start server in background
    t = threading.Thread(target=run_server, args=(port,), daemon=True)
    t.start()

    # 3. Wait for server to be ready
    logger.info("Waiting for server...")
    if not wait_for_server(timeout=20):
        logger.error("Server failed to start within 20 seconds")
        input("服务启动超时，按 Enter 退出...")
        sys.exit(1)

    # 4. Open native window
    logger.info("Opening application window...")
    try:
        create_window(port)
    except Exception as e:
        logger.error("Window error: %s", e, exc_info=True)
        # Fallback: open in browser
        import webbrowser
        logger.info("Falling back to browser...")
        webbrowser.open("http://127.0.0.1:%d" % port)
        input("按 Enter 退出...")

    # 5. Cleanup after window closes
    stop_server()
    logger.info("Application closed.")


if __name__ == "__main__":
    main()
