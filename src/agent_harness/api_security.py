"""API security module — token auth, config validation, audit logging

Provides:
- API token generation / persistence / validation
- Auth middleware factory for FastAPI
- Audit log for sensitive operations
"""

import hmac
import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

# ─── Token Storage ───

HARNESS_DIR = Path(os.environ.get("HARNESS_DATA_DIR",
    Path.home() / ".agent-harness"))
TOKEN_FILE = HARNESS_DIR / "api_token.txt"


def _ensure_dir():
    HARNESS_DIR.mkdir(parents=True, exist_ok=True)


def load_or_generate_token() -> str:
    """Load existing token or generate a new one on first startup."""
    _ensure_dir()
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text("utf-8").strip()
        if token:
            return token
    # Generate a new 256-bit token as hex (64 chars)
    token = secrets.token_hex(32)
    TOKEN_FILE.write_text(token, "utf-8")
    return token


def reset_token() -> str:
    """Regenerate the API token (invalidates all existing sessions)."""
    token = secrets.token_hex(32)
    TOKEN_FILE.write_text(token, "utf-8")
    return token


def validate_token(request_token: str | None, stored_token: str) -> bool:
    """Constant-time comparison of tokens to prevent timing attacks."""
    if not request_token or not stored_token:
        return False
    return hmac.compare_digest(request_token.encode(), stored_token.encode())


# ─── Config Audit ───

CONFIG_WARNINGS: list[str] = []


def audit_config() -> list[str]:
    """Check environment config for security weaknesses. Returns warning list."""
    warnings = []

    # Check cloud API key
    cloud_key = os.environ.get("HARNESS_CLOUD_KEY", "")
    if not cloud_key or cloud_key == "sk-local":
        warnings.append(
            "HARNESS_CLOUD_KEY 未设置或使用默认值 'sk-local'。"
            "请设置环境变量 HARNESS_CLOUD_KEY=sk-xxx"
        )

    # Check for other dangerous defaults
    if os.environ.get("HARNESS_API_HOST", "127.0.0.1") == "0.0.0.0":
        warnings.append(
            "HARNESS_API_HOST=0.0.0.0 — API 暴露到局域网，"
            "建议设为 127.0.0.1 或启用 API 认证"
        )

    return warnings


# ─── Audit Log ───

AUDIT_DIR = HARNESS_DIR / "audit"


def log_audit(tool_name: str, source: str, args: dict, result: str = "", duration_ms: float = 0):
    """Write a structured audit log entry for tool invocations."""
    _ensure_dir()
    AUDIT_DIR.mkdir(exist_ok=True)
    entry = {
        "ts": datetime.now(UTC).strftime("%Y%m%d_%H%M%S%f"),
        "tool": tool_name,
        "source": source,
        "args": {k: str(v)[:200] for k, v in args.items()},
        "result": result[:200],
        "duration_ms": round(duration_ms, 1),
    }
    path = AUDIT_DIR / f"audit_{entry['ts']}.json"
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
