"""Configuration — LLM endpoints, thresholds, paths.

Security: DO NOT hardcode credentials here. All secrets must come from
environment variables or .env file. See .env.example for required vars.

IMPORTANT: The startup script WILL EXIT with an error if critical config
is missing. This is intentional — it prevents casual copiers from running
the app without understanding what they're doing.
"""
import os
import sys
from pathlib import Path

# ── Load .env file if present ──
_env_path = Path(__file__).resolve()
# 向上找 .env（从 core/config.py 到项目根目录）
for _ in range(6):
    _env_path = _env_path.parent
    if (_env_path / ".env").exists():
        _env_path = _env_path / ".env"
        break
if _env_path.exists() and _env_path.is_file():
    with open(_env_path, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _key, _val = _line.split("=", 1)
            _key = _key.strip()
            _val = _val.strip().strip("\"'")
            if _key and _val and not os.environ.get(_key):
                os.environ[_key] = _val

# ─── LLM API endpoints ───
# Must be configured via env vars or .env. No magic defaults — if unset,
# the startup guard will print instructions and exit.
LLAMA_API = os.environ.get("HARNESS_LLAMA_API", "")
OLLAMA_API = os.environ.get("HARNESS_OLLAMA_API", "")
DEEPSEEK_API = os.environ.get("HARNESS_DEEPSEEK_API", "")
CLOUD_API_DIRECT = os.environ.get("HARNESS_CLOUD_API", "")
CLOUD_API_KEY = os.environ.get("HARNESS_CLOUD_KEY", "")

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

# ─── Auth bypass (opt-in, not default) ───
# Set HARNESS_DISABLE_AUTH=1 to allow unauthenticated local-only API access.
# By default, all /v1/* endpoints require JWT or X-API-Key.
DISABLE_AUTH = os.environ.get("HARNESS_DISABLE_AUTH", "").lower() in ("1", "true", "yes")


# ═══════════════════════════════════════════
# STARTUP GUARD — fatal if config incomplete
# ═══════════════════════════════════════════

def require_config() -> None:
    """Check critical config at startup. Exits with error + instructions if missing.

    This is the first line of defense against casual copiers: without reading
    the docs and setting up at least one LLM backend, the app won't start.
    """
    errors: list[str] = []

    # At least one LLM backend must be configured
    backends = [
        ("HARNESS_LLAMA_API (本地 llama.cpp)", LLAMA_API),
        ("HARNESS_DEEPSEEK_API (云端)", DEEPSEEK_API),
        ("HARNESS_CLOUD_API (API 聚合)", CLOUD_API_DIRECT),
    ]
    configured = [name for name, val in backends if val]
    if not configured:
        errors.append(
            "未配置任何 LLM 后端！\n"
            "请至少设置一个后端地址：\n"
            "  export HARNESS_LLAMA_API=http://127.0.0.1:8081/v1/chat/completions\n"
            "  export HARNESS_DEEPSEEK_API=https://api.deepseek.com/v1/chat/completions\n"
            "或复制 .env.example 为 .env 并编辑：\n"
            "  cp .env.example .env\n"
        )

    # Cloud API key: warn if using DeepSeek/cloud without a key
    if DEEPSEEK_API and not CLOUD_API_KEY:
        errors.append(
            "设置了 HARNESS_DEEPSEEK_API 但未设置 HARNESS_CLOUD_KEY。\n"
            "   export HARNESS_CLOUD_KEY=sk-xxx\n"
        )

    if not errors:
        return

    # ─── Fatal: print error + setup guide ───
    border = "=" * 60
    print(f"\n{border}", file=sys.stderr)
    print("  ❌ 灵枢 (LingShu Agent) — 配置不完整", file=sys.stderr)
    print(f"  {border}", file=sys.stderr)
    for e in errors:
        for line in e.strip().split("\n"):
            print(f"  {line}", file=sys.stderr)
    print(f"  {border}", file=sys.stderr)
    print("  配置完成后重新启动。", file=sys.stderr)
    print(f"  {border}\n", file=sys.stderr)
    sys.exit(1)
