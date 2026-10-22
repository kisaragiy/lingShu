"""BYOK 搜索 key 的 API 契约（真实用户填自己的 key）。

锁住四条不可退让的性质 —— 任何一条破了，要么泄密钥，要么用户以为存上了其实没存：
1. **明文绝不落盘**：只存 keystore 密文；响应与配置读取都不回显任何片段
2. **状态只报布尔**：`前4后4` 会泄掉一半密钥，与 `redact_config` 同口径
3. **清除 = 传空串**，且要真的从磁盘消失
4. **demo 能力闸 fail-closed**：公开 demo 里写服务器密钥一律拒绝

同时做一次端到端正向控制：存完之后 `get_search_config()` 必须能读回同一个 key
（否则「存上了但搜索用不到」= 又一个静默失败）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import agent_harness.apps.research.api as research_api  # noqa: E402
from agent_harness.core.pipeline import config_manager  # noqa: E402

KEY = "sk-unit-test-1234567890abcdef"
TAVILY_KEY = "tvly-unit-test-0987654321"


def _client() -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):  # noqa: ANN001
        request.state.user = {"id": "u_admin", "username": "admin", "role": "admin"}
        return await call_next(request)

    app.include_router(research_api.router)
    return TestClient(app)


@pytest.fixture()
def cfg_file(tmp_path, monkeypatch):
    """配置路径重定向到 tmp —— 绝不碰真实 ~/.agent-harness/config.json。"""
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"llm": {"model": "keep-me-not-a-key"}}), encoding="utf-8")
    monkeypatch.setattr(config_manager, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_manager, "CONFIG_PATH", cfg)
    # 环境变量兜底会污染「清除后应为未配置」的断言
    for name in ("DEEPSEEK_API_KEY", "HARNESS_DEEPSEEK_API", "TAVILY_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    # llm.api_key 兜底同理
    from agent_harness.core import config as core_config

    monkeypatch.setattr(core_config, "get_llm_config", lambda: {"api_key": ""})
    return cfg


def test_save_key_writes_ciphertext_only(cfg_file):
    r = _client().post("/v1/setup/search-key", json={"provider": "deepseek", "key": KEY})
    assert r.status_code == 200, r.text
    assert r.json()["configured"] is True

    raw = cfg_file.read_text(encoding="utf-8")
    assert KEY not in raw, "明文 key 落盘了"
    data = json.loads(raw)
    assert "api_key_enc" in data["search"], "密文没写进去"
    assert "api_key" not in data["search"], "明文 api_key 字段不该存在"
    # 其它配置段必须保留（别把用户 llm 配置写没）
    assert data["llm"]["model"] == "keep-me-not-a-key"


def test_status_exposes_boolean_only(cfg_file):
    c = _client()
    c.post("/v1/setup/search-key", json={"provider": "deepseek", "key": KEY})
    r = c.get("/v1/setup/search-key")
    body = r.text
    assert r.json()["deepseek"]["configured"] is True
    assert r.json()["tavily"]["configured"] is False
    # 任何片段都不得回显
    assert KEY not in body
    for frag in (KEY[:4], KEY[-4:], "enc"):
        assert frag not in body, f"状态接口泄露了 {frag!r}"


def test_get_config_hides_search_secret(cfg_file):
    c = _client()
    c.post("/v1/setup/search-key", json={"provider": "deepseek", "key": KEY})
    r = c.get("/v1/setup/config")
    assert r.status_code == 200
    search = r.json().get("search") or {}
    assert "api_key_enc" not in search and "api_key" not in search
    assert KEY not in r.text


def test_clear_key_removes_from_disk(cfg_file):
    c = _client()
    c.post("/v1/setup/search-key", json={"provider": "deepseek", "key": KEY})
    r = c.post("/v1/setup/search-key", json={"provider": "deepseek", "key": ""})
    assert r.status_code == 200 and r.json()["configured"] is False
    assert "api_key_enc" not in json.loads(cfg_file.read_text(encoding="utf-8"))["search"]
    assert c.get("/v1/setup/search-key").json()["deepseek"]["configured"] is False


def test_tavily_uses_separate_field(cfg_file):
    _client().post("/v1/setup/search-key", json={"provider": "tavily", "key": TAVILY_KEY})
    data = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert "tavily_key_enc" in data["search"]
    assert "api_key_enc" not in data["search"], "Tavily key 不该写进 DeepSeek 字段"


def test_short_or_whitespace_key_rejected(cfg_file):
    c = _client()
    assert c.post("/v1/setup/search-key", json={"provider": "deepseek", "key": "abc"}).status_code == 400
    assert c.post(
        "/v1/setup/search-key", json={"provider": "deepseek", "key": "sk-has space 123"}
    ).status_code == 400
    assert c.post("/v1/setup/search-key", json={"provider": "nope", "key": KEY}).status_code == 400


def test_demo_mode_denied(cfg_file, monkeypatch):
    """fail-closed：能力闸说不行就是不行（模拟 DISABLE_AUTH 的 demo）。"""
    monkeypatch.setattr(research_api, "_capability_denied", lambda cap: cap == "write_server_config")
    r = _client().post("/v1/setup/search-key", json={"provider": "deepseek", "key": KEY})
    assert r.status_code == 403
    assert "api_key_enc" not in (json.loads(cfg_file.read_text(encoding="utf-8")).get("search") or {})


def test_roundtrip_search_config_reads_back(cfg_file):
    """正向控制：存完之后搜索层必须真能拿到这个 key（否则等于没存）。"""
    _client().post("/v1/setup/search-key", json={"provider": "deepseek", "key": KEY})
    from agent_harness.core.config import get_search_config

    got = get_search_config()
    assert got["deepseek_key"] == KEY
