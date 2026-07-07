"""Config Manager — persistent configuration for 灵枢.

Stores user config in ~/.agent-harness/config.json.
Auto-discovers environment (paths, LLM backends, services).
"""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Optional

# ─── Paths ───

CONFIG_DIR = Path(os.environ.get("HARNESS_CONFIG_DIR", 
                                 os.path.expanduser("~/.agent-harness")))
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "setup_complete": False,
    "llm": {
        "backend": "auto",        # "auto" | "llamacpp" | "ollama" | "deepseek" | "openai"
        "api_url": "",
        "api_key": "",
        "model": "",
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
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
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


# ─── Path helpers ───

def has_chinese(path: str) -> bool:
    """Check if a path contains Chinese characters."""
    for c in path:
        if '\u4e00' <= c <= '\u9fff' or '\u3000' <= c <= '\u303f':
            return True
    return False


def find_llama_cpp() -> Optional[str]:
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


def find_comfyui() -> Optional[str]:
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
    s.settimeout(2)
    try:
        s.connect((host, port))
        s.close()
        return True
    except (socket.timeout, ConnectionError, OSError):
        return False


# ─── Environment checks ───

def check_llm_backend(backend: str = "auto") -> dict:
    """Check which LLM backends are available."""
    results = {}

    # llama-server
    results["llamacpp"] = {
        "available": check_port(8080),
        "port": 8080,
        "endpoint": "http://127.0.0.1:8080/v1/chat/completions",
        "label": "llama.cpp (本地)",
    }

    # Model proxy
    results["model_proxy"] = {
        "available": check_port(8081),
        "port": 8081,
        "endpoint": "http://127.0.0.1:8081/v1/chat/completions",
        "label": "Model Proxy (路由)",
    }

    # Ollama
    results["ollama"] = {
        "available": check_port(11434, "172.18.9.126"),
        "port": 11434,
        "endpoint": "http://172.18.9.126:11434/api/generate",
        "label": "Ollama (WSL)",
    }

    # DeepSeek API key check
    dsk_key = os.environ.get("DEEPSEEK_API_KEY", "")
    results["deepseek"] = {
        "available": bool(dsk_key),
        "endpoint": "https://api.deepseek.com",
        "label": "DeepSeek Flash (云端)",
        "key_configured": bool(dsk_key),
    }

    # Hermes
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
        entry = {"label": label, "path": str(path) if path else "", "exists": False, "has_chinese": False}
        if path:
            entry["exists"] = True
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
    """Run all environment checks in one call."""
    return {
        "llm_backends": check_llm_backend(),
        "services": check_services(),
        "paths": check_paths(),
        "python_version": sys.version,
        "platform": sys.platform,
    }
