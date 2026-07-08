"""Configuration — LLM endpoints, thresholds, paths.

Security: DO NOT hardcode credentials here. All secrets must come from
environment variables. The CLOUD_API_KEY default "sk-local" is a placeholder
that triggers a startup warning — real deployments must set HARNESS_CLOUD_KEY.
"""

import os
import sys
from pathlib import Path

# ─── LLM API endpoints ───
LLAMA_API = os.environ.get("HARNESS_LLAMA_API", "http://127.0.0.1:8081/v1/chat/completions")
OLLAMA_API = os.environ.get("HARNESS_OLLAMA_API", "http://172.18.9.126:11434/api/generate")
DEEPSEEK_API = os.environ.get("HARNESS_DEEPSEEK_API", "http://127.0.0.1:9000/v1/chat/completions")
CLOUD_API_DIRECT = os.environ.get("HARNESS_CLOUD_API", "http://127.0.0.1:9099/v1/chat/completions")
CLOUD_API_KEY = os.environ.get("HARNESS_CLOUD_KEY", "sk-local")  # placeholder, see check_config()

# ─── Model names ───
MODEL_LLAMA = os.environ.get("HARNESS_MODEL_LLAMA", "deepseek-v4")
MODEL_DEEPSEEK = os.environ.get("HARNESS_MODEL_DEEPSEEK", "deepseek-v4-pro")

# ─── Paths ───
HARNESS_DIR = Path(__file__).resolve().parent
MEMORY_DIR = Path(os.environ.get("HARNESS_MEMORY_DIR", HARNESS_DIR.parent.parent / "memory"))
SKILLS_DIR = Path(os.environ.get("HARNESS_SKILLS_DIR", HARNESS_DIR.parent.parent / "skills"))

# ─── Orchestration limits ───
MAX_RETRIES = int(os.environ.get("HARNESS_MAX_RETRIES", "2"))
MAX_ITERATIONS = int(os.environ.get("HARNESS_MAX_ITERATIONS", "10"))
MAX_TOKENS_PER_TASK = int(os.environ.get("HARNESS_MAX_TOKENS", "100000"))
MAX_WALL_TIME = int(os.environ.get("HARNESS_MAX_TIME", "600"))
MAX_NO_PROGRESS = int(os.environ.get("HARNESS_MAX_NO_PROGRESS", "5"))

# ─── Multi-agent settings ───
MAX_WORKER_CONCURRENCY = int(os.environ.get("HARNESS_MAX_WORKERS", "3"))
SUPERVISOR_MAX_ROUNDS = int(os.environ.get("HARNESS_SUPERVISOR_ROUNDS", "3"))


# ─── Config validation — called once at startup ───

_CONFIG_WARNINGS: list[str] = []


def check_config() -> list[str]:
    """Check config for security issues. Returns warning list (not fatal)."""
    global _CONFIG_WARNINGS
    warnings = []

    # Check cloud API key placeholder
    raw_key = os.environ.get("HARNESS_CLOUD_KEY", "")
    if not raw_key or raw_key == "sk-local":
        warnings.append(
            "HARNESS_CLOUD_KEY 使用默认占位符 'sk-local'。DeepSeek API 调用将失败。"
            "设置: export HARNESS_CLOUD_KEY=sk-xxx"
        )

    # Check if API is exposed to network
    api_host = os.environ.get("HARNESS_API_HOST", "127.0.0.1")
    if api_host == "0.0.0.0":
        warnings.append(
            "HARNESS_API_HOST=0.0.0.0 — API 暴露到局域网。"
            "建议配合 API token 使用或设为 127.0.0.1"
        )

    _CONFIG_WARNINGS = warnings
    return warnings


def print_config_warnings():
    """Print config warnings to stderr at startup."""
    if not _CONFIG_WARNINGS:
        check_config()
    for w in _CONFIG_WARNINGS:
        print("[config] ⚠️  %s" % w, file=sys.stderr)
