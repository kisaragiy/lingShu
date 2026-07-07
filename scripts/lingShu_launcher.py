"""灵枢 (LingShu) — Application Launcher

Entry point for the packaged .exe.
Handles port detection, browser launch, logging, and graceful shutdown.
"""

import os
import sys
import socket
import webbrowser
import logging
import threading
import time
from pathlib import Path


# ─── Paths ───

def _app_dir() -> Path:
    """Get the application directory (works for both dev and PyInstaller)."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


APP_DIR = _app_dir()
LOG_DIR = APP_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)


# ─── Logging ───

def setup_logging():
    # Force UTF-8 for console output
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
    if hasattr(sys.stderr, 'reconfigure'):
        try:
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
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
    return logging.getLogger("lingShu")


logger = setup_logging()


# ─── Port detection ───

def find_free_port(start: int = 8788, max_attempts: int = 20) -> int:
    """Find an available port starting from `start`."""
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

def run_server(port: int):
    """Start the FastAPI server on the given port."""
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
    server = uvicorn.Server(config)
    server.run()


# ─── Open browser after server starts ───

def _wait_and_open(url: str, timeout: int = 8):
    """Wait for the server to be ready, then open browser."""
    for i in range(timeout):
        time.sleep(1)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        try:
            s.connect(("127.0.0.1", int(url.split(":")[-1])))
            s.close()
            logger.info("Server ready! Opening browser: %s", url)
            webbrowser.open(url)
            return
        except (socket.timeout, ConnectionRefusedError, OSError):
            continue
    logger.warning("Server did not become ready within %d seconds", timeout)


# ─── Main ───

def main():
    print("")
    print("  == LingShu Agent ==")
    print("  " + ("-" * 40))
    logger.info("Starting LingShu...")

    try:
        port = find_free_port()
    except RuntimeError as e:
        logger.error("Port detection failed: %s", e)
        input("按 Enter 退出...")
        sys.exit(1)

    url = "http://127.0.0.1:%d" % port
    logger.info("URL: %s", url)

    # Open browser in background
    threading.Thread(target=_wait_and_open, args=(url,), daemon=True).start()

    try:
        run_server(port)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error("Fatal error: %s", e, exc_info=True)
        input("发生错误，按 Enter 退出...")

    logger.info("Goodbye.")


if __name__ == "__main__":
    main()
