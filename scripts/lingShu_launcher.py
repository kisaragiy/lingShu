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


# ─── Window config persistence ───

WINDOW_CONFIG_FILE = APP_DIR / "window_config.json"


def _load_window_config() -> dict:
    """Load saved window position/size."""
    try:
        if WINDOW_CONFIG_FILE.exists():
            with open(WINDOW_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_window_config(width: int, height: int, x: int, y: int):
    """Save window position/size to disk."""
    try:
        with open(WINDOW_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"width": width, "height": height, "x": x, "y": y}, f)
    except Exception:
        pass


# ─── Window (pywebview) ───

def create_window(port: int):
    """Create the native application window."""
    import webview

    url = "http://127.0.0.1:%d" % port

    # Restore previous window geometry
    cfg = _load_window_config()
    w = cfg.get("width", 1100)
    h = cfg.get("height", 750)
    wx = cfg.get("x")
    wy = cfg.get("y")

    # Window settings
    window = webview.create_window(
        title="灵枢 — AI 调研助手",
        url=url,
        width=w,
        height=h,
        x=wx,
        y=wy,
        min_size=(800, 600),
        resizable=True,
        fullscreen=False,
        text_select=True,
        confirm_close=True,
        easy_drag=False,  # allow normal window dragging for position tracking
    )

    # Run the window (blocking — returns when window closes)
    webview.start(
        debug=False,
        http_server=False,
        private_mode=False,
        storage_path=str(APP_DIR / "webview_data"),
    )

    # Save window position on close
    try:
        _save_window_config(window.width, window.height, window.x, window.y)
    except Exception:
        pass


# ─── Splash / Loading helper ───

def show_loading_screen(port: int):
    """Show a simple loading HTML while server starts."""
    # This is a fallback: if the page takes too long, pywebview will show a blank
    # Instead, we wait for the server before creating the window
    pass


def _ensure_llm_configured() -> None:
    """无 LLM 配置时，后台尝试自动检测本地 Ollama（不阻塞窗口打开）。

    2026-09-10 修正：此前 auto_setup 只挂在 CLI 入口（main.py），而打包版
    的入口是本 launcher —— 结果「无 key 也能用本地模型」这条路在实现上
    根本不存在（评审 D2）。
    """
    def _task():
        try:
            from agent_harness.core.pipeline import config_manager as cm
            cfg = cm.load_config()
            llm = cfg.get("llm") or {}
            if (llm.get("api_key") or "").strip():
                return                                   # 已有云 key
            if (llm.get("backend") or "") == "ollama" and llm.get("api_url"):
                return                                   # 已配本地
            from agent_harness.auto_setup import auto_setup
            url = auto_setup()
            logger.info("auto_setup 结果: %s", url)
        except Exception as e:
            logger.warning("auto_setup 跳过: %s", e)
    threading.Thread(target=_task, daemon=True).start()


def _pause(msg: str) -> None:
    """等待用户确认。打包为 GUI 程序（无控制台）时 stdin 不可用，静默跳过。"""
    try:
        if not sys.stdin or not sys.stdin.isatty():
            return
        input(msg)
    except (EOFError, OSError, ValueError):
        pass


def _fail(msg: str) -> None:
    """致命错误提示：优先弹窗（无控制台用户唯一能看到的方式），退化到日志。"""
    logger.error(msg)
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("灵枢启动失败", msg)
        root.destroy()
    except Exception:
        print(msg, file=sys.stderr)
    sys.exit(1)


# ─── Main ───

def main():
    print("")
    print("  灵枢 — AI 调研助手")
    print("  " + ("-" * 40))
    logger.info("Starting...")

    # 1. Find available port
    try:
        port = find_free_port()
    except RuntimeError as e:
        _fail("端口不可用，无法启动服务。请关闭占用端口的程序后重试。\n\n详细信息：%s" % e)

    # 2. Start server in background
    t = threading.Thread(target=run_server, args=(port,), daemon=True)
    t.start()

    # 2.5 无配置时后台自动探测本地 LLM（不阻塞窗口）
    _ensure_llm_configured()

    # 3. Wait for server to be ready
    logger.info("Waiting for server...")
    if not wait_for_server(timeout=20):
        _fail("服务启动超时（20 秒内未就绪）。请查看日志文件后重试。")

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
        _pause("已在浏览器中打开，按 Enter 退出...")

    # 5. Cleanup after window closes
    stop_server()
    logger.info("Application closed.")


if __name__ == "__main__":
    main()
