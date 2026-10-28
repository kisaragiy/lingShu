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


def get_llm_config() -> dict:
    """运行时动态读取 LLM 配置 —— 优先读 config.json(用户运行时填的)，回退环境变量常量。

    解决"设置页填 key 但 LLM 调用不读它"的脱节：llm.py 用本函数而非 import 快照常量，
    使 /v1/setup/config 保存的 api_url/api_key/model 能在下次请求即时生效（无需重启）。
    返回: {"api_url": str, "api_key": str, "model": str}
    """
    import json as _json

    # 1) 优先 config.json (用户运行时配置)
    # 路径**唯一来源** = config_manager.CONFIG_PATH（ADR#5）。此前这里按 os.environ 现算，
    # 而 save_config 写的是 config_manager 的 import 期常量 → 同一份配置两条解析路径，
    # 任何只重定向其一的环境（测试夹具、按 env 定制部署）都会"刚写完读不到"、
    # 静默回退到启动快照（实测：回退到 .env 里的 mock 端口）。函数内 import 避免循环依赖。
    from agent_harness.core.pipeline.config_manager import CONFIG_PATH as config_path
    if config_path.exists():
        try:
            with open(config_path, encoding="utf-8") as _f:
                cfg = _json.load(_f)
            llm = cfg.get("llm", {})
            url = llm.get("api_url") or ""
            key = llm.get("api_key") or ""
            if not key and llm.get("api_key_enc"):
                # SEC-18：磁盘上只有密文，解密回内存（失败时不静默冒充「已配置」）
                try:
                    from agent_harness.core.pipeline.keystore import decrypt_key
                    key = decrypt_key(llm["api_key_enc"])
                except Exception:  # noqa: BLE001
                    key = ""
            model = llm.get("model") or ""
            if url:  # config.json 有明确 api_url 时优先
                return {"api_url": url, "api_key": key, "model": model,
                        "backend": llm.get("backend") or "auto"}
        except (_json.JSONDecodeError, OSError):
            pass

    # 2) 回退环境变量常量 (启动配置)
    return {
        "api_url": DEEPSEEK_API or LLAMA_API,
        "api_key": CLOUD_API_KEY,
        "model": MODEL_DEEPSEEK or MODEL_LLAMA,
    }


def get_search_config() -> dict:
    """搜索后端配置（BYOK —— 用户填自己的 key）。

    优先级（每个后端独立）：
      1. config.json 的 `search.api_key(_enc)` —— 前端设置里填的，复用 SEC-18 keystore 加密落盘
      2. `llm.api_key` —— 同一个 DeepSeek key 通常兼作搜索 key
      3. 环境变量 `DEEPSEEK_API_KEY` / `HARNESS_DEEPSEEK_API` / `TAVILY_API_KEY`

    为什么要独立一段而不是复用 llm：本地 ollama 用户没有 `llm.api_key`，
    但他们仍可能想用云搜索——反过来说，填了搜索 key 也不该被当成模型 key 用。
    全部为空时返回空串，由调用方明确跳过（记 skip 诊断），绝不伪装成「搜到 0 条」。
    """
    import json as _json

    deepseek_key = ""
    tavily_key = ""
    try:
        from agent_harness.core.pipeline.config_manager import CONFIG_PATH as config_path
        if config_path.exists():
            with open(config_path, encoding="utf-8") as _f:
                cfg = _json.load(_f)
            s = cfg.get("search") or {}
            k = s.get("api_key") or ""
            if not k and s.get("api_key_enc"):
                try:
                    from agent_harness.core.pipeline.keystore import decrypt_key
                    k = decrypt_key(s["api_key_enc"])
                except Exception:  # noqa: BLE001  机器身份变了 → 当未配置，提示重填
                    k = ""
            deepseek_key = k
            tavily_key = s.get("tavily_key") or ""
            if not tavily_key and s.get("tavily_key_enc"):
                try:
                    from agent_harness.core.pipeline.keystore import decrypt_key
                    tavily_key = decrypt_key(s["tavily_key_enc"])
                except Exception:  # noqa: BLE001
                    tavily_key = ""
    except (_json.JSONDecodeError, OSError):
        pass

    if not deepseek_key:
        deepseek_key = (get_llm_config() or {}).get("api_key") or ""
    if not deepseek_key:
        deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "") or os.environ.get("HARNESS_DEEPSEEK_API", "")
    if not tavily_key:
        tavily_key = os.environ.get("TAVILY_API_KEY", "")

    return {"deepseek_key": deepseek_key, "tavily_key": tavily_key}
