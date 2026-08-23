"""
Configuration — LLM endpoints, thresholds, paths.

Security: DO NOT hardcode credentials here. All secrets must come from
environment variables or .env file. See .env.example for required vars.
"""
import os
import sys
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ── Load .env file if present ──
_env_path = Path(__file__).resolve()
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


class Settings(BaseSettings):
    """集中配置 — 带 Pydantic 类型验证。来源：.env > 环境变量 > 默认值。"""

    model_config = SettingsConfigDict(
        env_prefix="HARNESS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── LLM API ──
    llama_api: str = Field(default="", description="llama.cpp API 地址")
    ollama_api: str = Field(default="", description="Ollama API 地址")
    deepseek_api: str = Field(default="", description="DeepSeek API 地址")
    cloud_api: str = Field(default="", description="API 聚合地址")
    cloud_key: str = Field(default="", description="云端 API Key")

    # ── Model names ──
    model_llama: str = Field(default="deepseek-v4")
    model_deepseek: str = Field(default="deepseek-v4-pro")

    # ── Orchestration ──
    max_retries: int = Field(default=2, ge=0, le=10)
    max_iterations: int = Field(default=10, ge=1, le=100)
    max_tokens: int = Field(default=100000, ge=1000, le=1_000_000)
    max_time: int = Field(default=600, ge=30, le=3600)
    max_no_progress: int = Field(default=5, ge=1, le=20)

    # ── Multi-agent ──
    max_workers: int = Field(default=3, ge=1, le=10)
    supervisor_rounds: int = Field(default=3, ge=1, le=10)

    # ── Auth ──
    disable_auth: bool = Field(default=False)

    @field_validator("disable_auth", mode="before")
    @classmethod
    def coerce_bool(cls, v: object) -> bool:
        """Coerce string values like '***', 'true', 'false' to bool."""
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            v_lower = v.strip().lower()
            if v_lower in ("true", "1", "yes", "on"):
                return True
            if v_lower in ("false", "0", "no", "off", "", "***"):
                return False
            return bool(v)
        return bool(v)

    # ── Paths ──
    memory_dir: Optional[str] = None
    skills_dir: Optional[str] = None

    def check(self) -> None:
        """启动时校验——至少一个 LLM 后端已配置。"""
        configured = []
        if self.llama_api:
            configured.append(f"llama.cpp ({self.llama_api})")
        if self.deepseek_api:
            configured.append(f"DeepSeek ({self.deepseek_api})")
        if self.cloud_api:
            configured.append(f"云 API ({self.cloud_api})")
        if not configured:
            print("=" * 60)
            print("❌ 未配置任何 LLM 后端！")
            print()
            print("请设置以下环境变量之一（或在 .env 中）：")
            print("  HARNESS_LLAMA_API=http://127.0.0.1:8080/v1")
            print("  HARNESS_DEEPSEEK_API=https://api.deepseek.com/v1")
            print("  HARNESS_CLOUD_API=<你的 API 代理地址>")
            print("=" * 60)
            sys.exit(1)
        print(f"✅ 配置校验通过 — {len(configured)} 个 LLM 后端")


# ── 实例化配置 ──
_settings = Settings()

LLAMA_API = _settings.llama_api
OLLAMA_API = _settings.ollama_api
DEEPSEEK_API = _settings.deepseek_api
CLOUD_API_DIRECT = _settings.cloud_api
CLOUD_API_KEY = _settings.cloud_key
MODEL_LLAMA = _settings.model_llama
MODEL_DEEPSEEK = _settings.model_deepseek
MAX_RETRIES = _settings.max_retries
MAX_ITERATIONS = _settings.max_iterations
MAX_TOKENS_PER_TASK = _settings.max_tokens
MAX_WALL_TIME = _settings.max_time
MAX_NO_PROGRESS = _settings.max_no_progress
MAX_WORKER_CONCURRENCY = _settings.max_workers
SUPERVISOR_MAX_ROUNDS = _settings.supervisor_rounds
DISABLE_AUTH = _settings.disable_auth
HARNESS_DIR = Path(__file__).resolve().parent
MEMORY_DIR = Path(_settings.memory_dir or HARNESS_DIR.parent.parent / "memory")
SKILLS_DIR = Path(_settings.skills_dir or HARNESS_DIR.parent.parent / "skills")


def require_config() -> None:
    """兼容旧接口。"""
    _settings.check()
