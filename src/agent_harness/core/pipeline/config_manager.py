"""Config Manager — persistent configuration for 灵枢.

Stores user config in ~/.agent-harness/config.json.
Auto-discovers environment (paths, LLM backends, services).
Provides fix actions for one-click environment setup.
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

# ─── Paths ───

CONFIG_DIR = Path(os.environ.get("HARNESS_CONFIG_DIR",
                                 os.path.expanduser("~/.agent-harness")))
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "setup_complete": True,
    "llm": {
        "backend": "model_proxy",
        "api_url": "http://127.0.0.1:8081/v1/chat/completions",
        "api_key": "",
        "model": "deepseek-v4",
    },
    "paths": {
        "llama_cpp": "",
        "comfyui": "",
        "workspace": "",
    },
    "services": {
        "searxng": False,
        "comfyui": False,
    },
    "ui": {
        "theme": "dark",
        "language": "zh",
    },
}


# ─── Config IO ───

def _ensure_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """Load config from disk, merging with defaults."""
    _ensure_dir()
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                user = json.load(f)
            merged = {**DEFAULT_CONFIG, **user}
            merged["llm"] = {**DEFAULT_CONFIG["llm"], **(user.get("llm", {}))}
            merged["paths"] = {**DEFAULT_CONFIG["paths"], **(user.get("paths", {}))}
            return merged
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULT_CONFIG)


def save_config(config: dict) -> dict:
    """Save config to disk."""
    _ensure_dir()
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    return config


def ensure_setup_complete():
    """Mark setup as complete with default config (one-click skip)."""
    cfg = load_config()
    cfg["setup_complete"] = True
    cfg["llm"]["backend"] = "model_proxy"
    cfg["llm"]["api_url"] = "http://127.0.0.1:8081/v1/chat/completions"
    cfg["llm"]["model"] = "deepseek-v4"
    save_config(cfg)
    return cfg


# ─── Path helpers ───

def has_chinese(path: str) -> bool:
    """Check if a path contains Chinese characters."""
    return any('一' <= c <= '鿿' or '\u3000' <= c <= '〿' for c in path)


def find_llama_cpp() -> str | None:
    """Detect llama.cpp directory."""
    candidates = [
        r"C:\llama\llama.cpp",
        r"C:\Users\zwq\Downloads\llama.cpp",
        os.path.expanduser("~/Downloads/llama.cpp"),
        os.path.expanduser("~/llama.cpp"),
    ]
    for d in candidates:
        p = Path(d)
        if p.exists() and any(p.glob("llama-server*")):
            return str(p.resolve())
    return None


def find_comfyui() -> str | None:
    """Detect ComfyUI directory."""
    candidates = [
        r"C:\DrawingLive\ComfyUI",
        r"C:\ComfyUI",
        os.path.expanduser("~/ComfyUI"),
    ]
    for d in candidates:
        p = Path(d)
        if p.exists() and (p / "main.py").exists():
            return str(p.resolve())
    return None


# ─── Port checking ───

def check_port(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a port is listening."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        s.connect((host, port))
        s.close()
        return True
    except (TimeoutError, ConnectionError, OSError):
        return False


# ─── Environment checks ───

def check_llm_backend(backend: str = "auto") -> dict:
    """Check which LLM backends are available."""
    results = {}

    results["llamacpp"] = {
        "available": check_port(8080),
        "port": 8080,
        "endpoint": "http://127.0.0.1:8080/v1/chat/completions",
        "label": "llama.cpp (本地)",
    }

    results["model_proxy"] = {
        "available": check_port(8081),
        "port": 8081,
        "endpoint": "http://127.0.0.1:8081/v1/chat/completions",
        "label": "Model Proxy (路由·推荐)",
    }

    results["ollama"] = {
        "available": check_port(11434, "172.18.9.126"),
        "port": 11434,
        "endpoint": "http://172.18.9.126:11434/api/generate",
        "label": "Ollama (WSL)",
    }

    dsk_key = os.environ.get("DEEPSEEK_API_KEY", "")
    results["deepseek"] = {
        "available": bool(dsk_key),
        "endpoint": "https://api.deepseek.com",
        "label": "DeepSeek Flash (云端)",
        "key_configured": bool(dsk_key),
    }

    results["hermes"] = {
        "available": check_port(8642),
        "port": 8642,
        "label": "Hermes Agent",
    }

    return results


def check_services() -> dict:
    """Check all auxiliary services."""
    return {
        "searxng": {
            "available": check_port(4000),
            "port": 4000,
            "label": "SearXNG 搜索引擎",
            "endpoint": "http://127.0.0.1:4000",
        },
        "comfyui": {
            "available": check_port(8188),
            "port": 8188,
            "label": "ComfyUI 绘画",
            "endpoint": "http://127.0.0.1:8188",
        },
        "gateway": {
            "available": check_port(18789),
            "port": 18789,
            "label": "OpenClaw Gateway",
        },
        "open_webui": {
            "available": check_port(3000),
            "port": 3000,
            "label": "Open WebUI",
            "endpoint": "http://127.0.0.1:3000",
        },
    }


def check_paths() -> list[dict]:
    """Check all key paths for issues."""
    results = []

    paths_to_check = [
        ("llama.cpp 目录", find_llama_cpp()),
        ("ComfyUI 目录", find_comfyui()),
        ("工作目录", os.getcwd()),
        ("用户目录", os.path.expanduser("~")),
        ("Python 路径", sys.executable),
    ]

    for label, path in paths_to_check:
        entry = {"label": label, "path": str(path) if path else "",
                 "exists": bool(path), "has_chinese": False}
        if path:
            entry["has_chinese"] = has_chinese(str(path))
        results.append(entry)

    return results


def test_llm_connection(endpoint: str, model: str = "", api_key: str = "") -> dict:
    """Test whether an LLM endpoint is reachable and responds."""
    import requests as req_lib
    try:
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model": model or "test",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 5,
        }
        r = req_lib.post(endpoint, json=payload, headers=headers, timeout=10)
        if r.status_code == 200:
            return {"reachable": True, "status": r.status_code, "error": ""}
        return {"reachable": False, "status": r.status_code, "error": r.text[:100]}
    except Exception as e:
        return {"reachable": False, "status": 0, "error": str(e)}


def full_env_check() -> dict:
    """Run all environment checks in one call (parallel)."""
    import concurrent.futures

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            "llm_backends": pool.submit(check_llm_backend),
            "services": pool.submit(check_services),
            "paths": pool.submit(check_paths),
        }
        for name, future in futures.items():
            try:
                results[name] = future.result(timeout=8)
            except concurrent.futures.TimeoutError:
                results[name] = {"error": "timeout"}
            except Exception as e:
                results[name] = {"error": str(e)}

    results["python_version"] = sys.version
    results["platform"] = sys.platform
    return results


# ═══════════════════════════════════════════════
# FIX ACTIONS — one-click environment repair
# ═══════════════════════════════════════════════

def fix_action(action: str) -> dict:
    """Execute a fix action and return result.

    Actions:
      - start_model_proxy: Launch model_proxy.py as subprocess
      - start_ollama: Start Ollama via WSL
      - start_searxng: Start SearXNG Docker container
      - start_llamacpp: Start llama-server
      - auto_configure: Full automatic configuration
    """
    action_map = {
        "start_model_proxy": _fix_start_model_proxy,
        "start_ollama": _fix_start_ollama,
        "start_searxng": _fix_start_searxng,
        "start_llamacpp": _fix_start_llamacpp,
        "auto_configure": _fix_auto_configure,
    }

    fn = action_map.get(action)
    if not fn:
        return {"success": False, "error": f"Unknown action: {action}"}

    try:
        return fn()
    except Exception as e:
        return {"success": False, "error": str(e)}


def _fix_start_model_proxy() -> dict:
    """Start model_proxy.py as a background process."""
    candidates = [
        os.path.expanduser("~/Downloads/llama.cpp/model_proxy.py"),
        r"C:\Users\zwq\Downloads\llama.cpp\model_proxy.py",
    ]
    for path in candidates:
        if os.path.isfile(path):
            # Check if already running
            if check_port(8081):
                return {"success": True, "message": "Model Proxy 已在运行", "port": 8081}
            # Launch
            subprocess.Popen(
                [sys.executable, path],
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Wait for it
            for _ in range(15):
                time.sleep(1)
                if check_port(8081):
                    return {"success": True, "message": "Model Proxy 已启动", "port": 8081}
            return {"success": False, "error": "Model Proxy 启动超时"}
    return {"success": False, "error": "未找到 model_proxy.py"}


def _fix_start_ollama() -> dict:
    """Start Ollama via WSL."""
    if check_port(11434, "172.18.9.126"):
        return {"success": True, "message": "Ollama 已在运行"}
    try:
        subprocess.run(
            ["wsl", "-d", "Ubuntu-22.04", "bash", "-lc", "nohup ollama serve > /dev/null 2>&1 &"],
            timeout=10,
            capture_output=True,
        )
        for _ in range(10):
            time.sleep(2)
            if check_port(11434, "172.18.9.126"):
                return {"success": True, "message": "Ollama 已启动"}
        return {"success": False, "error": "Ollama 启动超时"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _fix_start_searxng() -> dict:
    """Start SearXNG Docker container."""
    if check_port(4000):
        return {"success": True, "message": "SearXNG 已在运行"}
    try:
        # Try starting existing container first
        r = subprocess.run(
            ["docker", "start", "searxng"],
            timeout=15, capture_output=True, text=True,
        )
        if r.returncode == 0:
            for _ in range(10):
                time.sleep(2)
                if check_port(4000):
                    return {"success": True, "message": "SearXNG 已启动"}
        # If no existing container, create one
        r = subprocess.run(
            ["docker", "run", "-d", "--name", "searxng",
             "-p", "4000:8080", "searxng/searxng:latest"],
            timeout=30, capture_output=True, text=True,
        )
        if r.returncode == 0:
            for _ in range(15):
                time.sleep(2)
                if check_port(4000):
                    return {"success": True, "message": "SearXNG 容器已创建并启动"}
        return {"success": False, "error": r.stderr[:200] or "Docker 命令失败"}
    except FileNotFoundError:
        return {"success": False, "error": "Docker 未安装"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _fix_start_llamacpp() -> dict:
    """Start llama-server."""
    if check_port(8080):
        return {"success": True, "message": "llama-server 已在运行"}
    llm_dir = find_llama_cpp()
    if not llm_dir:
        return {"success": False, "error": "未找到 llama.cpp 目录"}
    try:
        subprocess.Popen(
            [os.path.join(llm_dir, "llama-server.exe"),
             "-m", os.path.join(llm_dir, "models", "Qwen3.6-35B-A3B-Abliterated-Heretic-Q4_K_M.gguf"),
             "-ngl", "99", "--no-mmap", "--ctx-size", "131072",
             "--host", "127.0.0.1", "--port", "8080"],
            cwd=llm_dir,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        for _ in range(30):
            time.sleep(3)
            if check_port(8080):
                return {"success": True, "message": "llama-server 已启动", "port": 8080}
        return {"success": False, "error": "llama-server 启动超时"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _fix_auto_configure() -> dict:
    """Full automatic configuration — detect best available backend, save config.

    Strategy:
      1. Check available backends (model_proxy, llamacpp, ollama, deepseek)
      2. Pick the first available one
      3. Save config with that backend
      4. If none available, mark complete anyway (error will show in chat)
    """
    results = {"steps": [], "llm_configured": False, "services_started": []}

    backends = check_llm_backend()
    preferred_order = ["model_proxy", "llamacpp", "ollama", "deepseek"]

    chosen = None
    for name in preferred_order:
        info = backends.get(name, {})
        if info.get("available"):
            chosen = {"name": name, "endpoint": info.get("endpoint", ""), "label": info.get("label", name)}
            results["steps"].append({
                "action": f"detect_{name}",
                "success": True,
                "message": "{} 可用".format(info.get("label", name)),
            })
            break
        results["steps"].append({
            "action": f"detect_{name}",
            "success": False,
            "message": "{} 不可用".format(info.get("label", name)),
        })

    if chosen:
        cfg = load_config()
        cfg["llm"]["backend"] = chosen["name"]
        cfg["llm"]["api_url"] = chosen["endpoint"]
        cfg["llm"]["model"] = "deepseek-v4"
        cfg["setup_complete"] = True
        save_config(cfg)
        results["llm_configured"] = True
        results["chosen"] = chosen["label"]
    else:
        ensure_setup_complete()
        results["llm_configured"] = False
        results["warning"] = "未检测到可用的 LLM 后端。请确保 model_proxy / llama.cpp / Ollama 在运行，或在设置中手动配置。"

    return results
