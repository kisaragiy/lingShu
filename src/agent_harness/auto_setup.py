"""Auto setup — detect Ollama, pull default model, configure LLM backend.

Called at startup if no LLM backend is configured.
Tries to be invisible: no output unless something goes wrong.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

# ── 默认模型 ──
DEFAULT_MODEL = "qwen3:1.7b"

# ── Ollama 探测地址 ──
_OLLAMA_CANDIDATES = [
    "http://127.0.0.1:11434",
    "http://localhost:11434",
    # WSL 桥接 (通过 model_proxy 转发)
    "http://172.18.9.126:11434",
    # model_proxy 统一入口
    "http://127.0.0.1:8081",
]

# ── Ollama WSL 启动命令 ──
_WSL_START_CMD = [
    "wsl", "bash", "-c",
    "nohup ollama serve > /dev/null 2>&1 &"
]


def _probe_ollama(url: str, timeout: int = 3) -> bool:
    """Check if Ollama is reachable at the given URL."""
    try:
        req = urllib.request.Request(f"{url}/api/tags", method="GET")
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.status == 200
    except Exception:
        return False


def _find_ollama() -> str | None:
    """Scan known addresses for a running Ollama instance."""
    for url in _OLLAMA_CANDIDATES:
        if _probe_ollama(url):
            return url
    return None


def _model_available(ollama_url: str, model: str) -> bool:
    """Check if model is already pulled."""
    try:
        req = urllib.request.Request(f"{ollama_url}/api/tags", method="GET")
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())
        for m in data.get("models", []):
            if m["name"] == model or m["name"].startswith(model + ":"):
                return True
        return False
    except Exception:
        return False


def _pull_model(ollama_url: str, model: str) -> bool:
    """Pull a model from Ollama. Shows progress. May take minutes."""
    print(f"  📥 正在下载模型 {model}（首次约 1-3 分钟）...")
    print(f"     此操作仅执行一次，后续启动秒开。")
    sys.stdout.flush()

    try:
        payload = json.dumps({"model": model, "stream": True}).encode()
        req = urllib.request.Request(
            f"{ollama_url}/api/pull",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=600)
        # Show progress from streaming response
        last_percent = 0
        for line in resp:
            try:
                chunk = json.loads(line)
                if "completed" in chunk and "total" in chunk:
                    pct = int(chunk["completed"] / max(chunk["total"], 1) * 100)
                    if pct > last_percent or pct == 100:
                        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
                        print(f"  \r  下载中 [{bar}] {pct}%", end="", flush=True)
                        last_percent = pct
            except json.JSONDecodeError:
                pass
        print()
        return True
    except Exception as e:
        print(f"  ❌ 下载失败: {e}")
        return False


def auto_setup() -> str | None:
    """Auto-detect and configure local LLM backend.

    Returns the configured LLAMA_API URL if successful, None otherwise.
    """
    print("  🔍 未检测到 LLM 配置，尝试自动设置...")

    ollama_url = _find_ollama()

    if ollama_url is None:
        # Try starting Ollama via WSL
        print("  ⏳ Ollama 未运行，尝试启动...")
        try:
            subprocess.run(
                _WSL_START_CMD,
                capture_output=True, timeout=10,
            )
            time.sleep(4)
            ollama_url = _find_ollama()
        except Exception:
            pass

    if ollama_url is None:
        print("  ❌ Ollama 未安装或未运行。")
        print("    请安装 Ollama: https://ollama.com")
        print("    或配置云端 API（如 DeepSeek）。")
        return None

    print(f"  ✅ 检测到 Ollama: {ollama_url}")

    # Check if default model exists
    if not _model_available(ollama_url, DEFAULT_MODEL):
        print(f"  ⚠️  未找到模型 {DEFAULT_MODEL}")
        ok = _pull_model(ollama_url, DEFAULT_MODEL)
        if not ok:
            print("  ❌ 模型下载失败，请手动运行: ollama pull qwen3:1.7b")
            return None
    else:
        print(f"  ✅ 模型 {DEFAULT_MODEL} 已存在")

    # Configure the LLAMA_API to use Ollama's OpenAI-compatible endpoint
    # Ollama >= 0.1.26 has /v1/chat/completions
    llama_api = f"{ollama_url}/v1/chat/completions"

    # Set environment variable for this process
    os.environ["HARNESS_LLAMA_API"] = llama_api
    os.environ["HARNESS_MODEL_LLAMA"] = DEFAULT_MODEL

    print(f"  ✅ 已配置: {llama_api}")
    print(f"  ✅ 默认模型: {DEFAULT_MODEL}")
    return llama_api
