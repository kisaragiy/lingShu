"""Config Manager — persistent configuration for 灵枢.

Stores user config in ~/.agent-harness/config.json.
Auto-discovers environment (paths, LLM backends, services).
Provides fix actions for one-click environment setup.
"""

import copy
import json
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# ─── Paths ───

CONFIG_DIR = Path(os.environ.get("HARNESS_CONFIG_DIR",
                                 os.path.expanduser("~/.agent-harness")))
CONFIG_PATH = CONFIG_DIR / "config.json"

# 目录权限只收紧一次（每进程一次 icacls 子进程，不在每次 load_config 里付这个成本）
_ACL_HARDENED = False

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

def _hard_dir_permissions() -> None:
    """把配置目录收紧到「仅当前用户」（失败只 warning，绝不阻断启动）。

    SEC-18 的两层防御：加密防「config.json 被拷走后直接可读」，ACL 防
    「同机其它账户读目录」。Windows 的 ACL 模型与 POSIX 不同、且不同文件系统
    （FAT/exFAT/网络盘）行为不一致，故一律 best-effort。
    """
    global _ACL_HARDENED
    if _ACL_HARDENED:
        return
    _ACL_HARDENED = True  # 先置位：失败也不反复重试（避免每次 load 都 spawn 子进程）
    try:
        if os.name == "posix":
            os.chmod(CONFIG_DIR, 0o700)
        elif sys.platform == "win32":
            user = os.environ.get("USERNAME") or ""
            if not user:
                return
            subprocess.run(
                ["icacls", str(CONFIG_DIR), "/inheritance:r",
                 "/grant:r", f"{user}:(OI)(CI)F", "SYSTEM:(OI)(CI)F"],
                capture_output=True, text=True, timeout=15, check=False,
            )
    except Exception as e:  # noqa: BLE001 —— 权限收紧失败不能阻断启动
        logger.warning("配置目录权限收紧失败（已忽略，不影响启动）：%s", e)


def _ensure_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _hard_dir_permissions()
    except Exception as e:  # noqa: BLE001 —— 双保险：权限逻辑异常不得冒泡
        logger.warning("配置目录权限收紧调用异常（已忽略）：%s", e)


def redact_config(cfg: dict) -> dict:
    """返回脱敏副本：api_key 绝不明文外泄，只暴露「是否已配置」。

    2026-09-10 评审发现：GET /v1/setup/config 曾在认证豁免名单里且原样返回
    llm.api_key —— 本机任意进程一条 curl 即可读到云 key（.exe 解压同理）。
    预览片段（前4后4）也会泄掉一半密钥，故只暴露布尔值。
    """
    import copy
    out = copy.deepcopy(cfg)
    llm = out.get("llm")
    if isinstance(llm, dict):
        key = llm.get("api_key") or ""
        llm["api_key"] = ""
        llm["has_api_key"] = bool(key)
    return out


def _decrypt_in_place(merged: dict) -> None:
    """把 `llm.api_key_enc` 解密回 `llm.api_key`（只在内存里，绝不回写磁盘）。

    解密失败（换机/换账户/密文损坏）**明确报错**并置空：静默返回空 key 会让
    上游以为「用户没配置」，进而静默降级到本地模型 —— 那是更糟的失败模式。
    """
    llm = merged.get("llm")
    if not isinstance(llm, dict):
        return
    token = llm.pop("api_key_enc", None)
    if not token:
        return
    from agent_harness.core.pipeline.keystore import KeystoreError, decrypt_key

    try:
        llm["api_key"] = decrypt_key(token)
    except KeystoreError as e:
        logger.error("%s", e)
        llm["api_key"] = ""


def _migrate_legacy_plaintext(user: dict) -> dict:
    """一次性迁移：config.json 里若还有明文 `llm.api_key` → 加密成 `api_key_enc` 并重写。

    SEC-18 之前写出的配置都是明文；读一次就顺手收口，用户无感。
    """
    llm = user.get("llm")
    if not isinstance(llm, dict):
        return user
    plaintext = llm.pop("api_key", "") or ""
    llm.pop("has_api_key", None)  # 派生字段，不落盘
    if not plaintext:
        return user

    from agent_harness.core.pipeline.keystore import encrypt_key

    try:
        llm["api_key_enc"] = encrypt_key(plaintext)
    except Exception as e:  # noqa: BLE001 —— 加密不可用时退回内存态，绝不写明文
        logger.error("llm.api_key 加密失败，本次不落盘 key：%s", e)
        # key 已从 user 里 pop 出来，失败必须放回内存，否则用户当场失去 key
        llm["api_key"] = plaintext
        return user

    try:
        CONFIG_PATH.write_text(
            json.dumps(user, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("已迁移 config.json：llm.api_key 明文 → api_key_enc 密文（SEC-18）")
    except OSError as e:
        logger.warning("迁移后写回 config.json 失败（key 仍在内存可用）：%s", e)
    return user


def load_config() -> dict:
    """Load config from disk, merging with defaults.

    SEC-18：`llm.api_key_enc` 在这里解密回内存的 `llm.api_key`；老格式的明文
    `llm.api_key` 会被就地迁移成密文（一次性）。
    """
    _ensure_dir()
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                user = json.load(f)
            user = _migrate_legacy_plaintext(user)
            merged = {**DEFAULT_CONFIG, **user}
            merged["llm"] = {**DEFAULT_CONFIG["llm"], **(user.get("llm", {}))}
            merged["paths"] = {**DEFAULT_CONFIG["paths"], **(user.get("paths", {}))}
            _decrypt_in_place(merged)
            return merged
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULT_CONFIG)


def save_config(config: dict) -> dict:
    """Save config to disk.

    SEC-18：落盘前把 `llm.api_key` 换成 Fernet 密文 `llm.api_key_enc`。
    明文只留在内存 dict / `HARNESS_CLOUD_KEY`（进程内），**永不写入 config.json**。
    """
    _ensure_dir()
    to_write = copy.deepcopy(config)
    llm_out = to_write.get("llm")
    if isinstance(llm_out, dict):
        plaintext = llm_out.pop("api_key", "") or ""
        llm_out.pop("has_api_key", None)  # 派生字段，不落盘（否则会变成陈旧状态）
        if plaintext:
            from agent_harness.core.pipeline.keystore import encrypt_key

            try:
                llm_out["api_key_enc"] = encrypt_key(plaintext)
            except Exception as e:  # noqa: BLE001 —— 加密不可用时不落明文，宁可不存
                logger.error("llm.api_key 加密失败，本次不把 key 写入磁盘：%s", e)
                llm_out.pop("api_key_enc", None)
        else:
            llm_out.pop("api_key_enc", None)  # 用户清空 key → 旧密文必须一并清掉

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(to_write, f, ensure_ascii=False, indent=2)
    # 同步 LLM 配置到环境变量,使其他读环境变量的路径(如 workers.py)也能运行时生效
    llm = config.get("llm", {})
    if llm.get("api_url"):
        os.environ["HARNESS_LLAMA_API"] = llm["api_url"]
    if llm.get("api_key"):
        os.environ["HARNESS_CLOUD_KEY"] = llm["api_key"]
    if llm.get("model"):
        os.environ["HARNESS_MODEL_LLAMA"] = llm["model"]
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
        "available": check_port(11434),
        "port": 11434,
        "endpoint": "http://127.0.0.1:11434/api/generate",
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
    # 打包态没有 Python 解释器：sys.executable 是 lingShu.exe，
    # 用它去启动 .py 脚本只会再拉起一个灵枢实例。直接拒绝，让用户手动启动。
    if getattr(sys, "frozen", False):
        return {"success": False,
                "error": "打包版不支持自动拉起 model_proxy，请手动启动后重试"}
    candidates = [
        os.path.expanduser("~/Downloads/llama.cpp/model_proxy.py"),
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
    if check_port(11434):
        return {"success": True, "message": "Ollama 已在运行"}
    try:
        subprocess.run(
            ["wsl", "-d", "Ubuntu-22.04", "bash", "-lc", "nohup ollama serve > /dev/null 2>&1 &"],
            timeout=10,
            capture_output=True,
        )
        for _ in range(10):
            time.sleep(2)
            if check_port(11434):
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
