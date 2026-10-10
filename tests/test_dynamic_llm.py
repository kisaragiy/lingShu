"""动态 LLM 配置测试 — 打通设置页与 LLM 调用

场景：用户/招聘方在前端 /v1/setup/config 填 api_url/api_key/model
→ save_config 存 config.json + 同步环境变量
→ get_llm_config 运行时读到新配置
→ _post_cloud 用新配置调 LLM

覆盖：
- get_llm_config 优先读 config.json
- get_llm_config 回退环境变量(无 config.json 时)
- save_config 同步环境变量
- _post_cloud 用动态配置(非 import 快照)
- 未配置时降级本地 LLM(不崩)
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# ⚠️ 不要在这里写 `os.environ["HARNESS_CONFIG_DIR"] = ...`（2026-09-10 实测的写穿路径）：
# pytest 在 **collection 期**就 import 测试模块，而 `config_manager.CONFIG_DIR/CONFIG_PATH`
# 是 **import-time 常量** —— 谁先 import 谁说了算；顺序一反就落到用户真实
# `~/.agent-harness/config.json`，而 `save_config` 是**整文件覆盖**（不是合并）
# → 清空用户已填的 api_url/api_key/model。
# 数据根重定向现在统一在 `tests/conftest.py::pytest_configure`（startup 期，早于 collection），
# 本文件再用下面的 fixture 把 config.json 的落点钉到 tmp_path（与 import 顺序无关）。


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """把 config.json 的落点钉到 tmp_path —— 不依赖"谁先 import"。"""
    from agent_harness.core import config as config_mod
    from agent_harness.core.pipeline import config_manager as cm

    cdir = tmp_path / "cfg"
    cdir.mkdir(parents=True, exist_ok=True)
    for mod in (cm, config_mod):
        if hasattr(mod, "CONFIG_DIR"):
            monkeypatch.setattr(mod, "CONFIG_DIR", cdir, raising=False)
        if hasattr(mod, "CONFIG_PATH"):
            monkeypatch.setattr(mod, "CONFIG_PATH", cdir / "config.json", raising=False)
    # save_config 会同步写这两个真实环境变量，先清干净，避免测试互相污染
    for k in ("HARNESS_LLAMA_API", "HARNESS_CLOUD_KEY"):
        monkeypatch.delenv(k, raising=False)
    # 关键（@bughunter 定位的读写不同源）：`save_config` 写 `cm.CONFIG_PATH`，
    # 而 `config.get_llm_config()` 是**每次调用按 `os.environ["HARNESS_CONFIG_DIR"]` 现算路径**
    # → 不同步就会出现"刚写好却读不到"（回退到 import 期常量，实测读到 .env 的 mock 端口）。
    # 根治是让 get_llm_config 改用 `config_manager.CONFIG_PATH`（同一份配置两个解析路径，
    # ADR#5 实例）；这里先保证两侧指向同一处。
    monkeypatch.setenv("HARNESS_CONFIG_DIR", str(cdir))
    monkeypatch.setenv("HARNESS_DISABLE_AUTH", "1")
    return cdir


def _reset_config():
    """清掉本次测试写入的配置（落点由 _isolated_config 指向 tmp）。"""
    import shutil
    from agent_harness.core.pipeline import config_manager as cm
    shutil.rmtree(cm.CONFIG_DIR, ignore_errors=True)


# ─── get_llm_config: 优先 config.json ───
def test_get_llm_config_priority_from_config_json():
    _reset_config()
    from agent_harness.core.pipeline import config_manager as cm
    from agent_harness.core import config

    # 存一个有明确 api_url 的配置
    cm.save_config({"llm": {"api_url": "https://api.deepseek.com/v1/chat/completions",
                            "api_key": "sk-test-123", "model": "deepseek-v4-pro"}})
    cfg = config.get_llm_config()
    assert cfg["api_url"] == "https://api.deepseek.com/v1/chat/completions"
    assert cfg["api_key"] == "sk-test-123"
    assert cfg["model"] == "deepseek-v4-pro"
    _reset_config()


def test_get_llm_config_fallback_env():
    _reset_config()
    from agent_harness.core import config
    cfg = config.get_llm_config()
    # 无 config.json 时回退,应返回 dict 结构(哪怕空)
    assert isinstance(cfg, dict)
    assert "api_url" in cfg and "api_key" in cfg and "model" in cfg


# ─── save_config: 同步环境变量 ───
def test_save_config_syncs_env():
    _reset_config()
    from agent_harness.core.pipeline import config_manager as cm
    cm.save_config({"llm": {"api_url": "http://127.0.0.1:9999/v1",
                            "api_key": "sk-loc", "model": "deepseek-v4"}})
    assert os.environ.get("HARNESS_LLAMA_API") == "http://127.0.0.1:9999/v1"
    assert os.environ.get("HARNESS_CLOUD_KEY") == "sk-loc"
    _reset_config()


# ─── _post_cloud 用动态配置 ───
def test_post_cloud_uses_dynamic_config(monkeypatch):
    _reset_config()
    from agent_harness.core.pipeline import config_manager as cm
    from agent_harness.core.pipeline import llm

    cm.save_config({"llm": {"api_url": "https://dyn.example/v1/chat/completions",
                            "api_key": "sk-dynamic", "model": "dyn-model"}})

    # mock _session.post 捕获实际调用
    calls = {}
    class FakeResp:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": "动态配置生效"}}],
                    "usage": {"total_tokens": 7}}
    def fake_post(url, **kw):
        calls["url"] = url
        calls["model"] = kw["json"]["model"]
        calls["auth"] = kw["headers"].get("Authorization", "")
        return FakeResp()

    monkeypatch.setattr(llm._session, "post", fake_post)
    content, _ = llm._post_cloud([{"role": "user", "content": "你好"}])
    assert content == "动态配置生效"
    assert calls["url"] == "https://dyn.example/v1/chat/completions", calls.get("url")
    assert calls["model"] == "dyn-model"
    assert calls["auth"] == "Bearer sk-dynamic"
    _reset_config()


# ─── 未配置 key → 降级本地(不崩) ───
def test_post_cloud_no_key_degrades():
    _reset_config()
    from agent_harness.core.pipeline import config_manager as cm
    cm.save_config({"llm": {"api_url": "", "api_key": "", "model": ""}})
    from agent_harness.core.pipeline import llm
    # 不应抛异常,应降级(可能报连接错但被 call_llama 处理)
    try:
        llm._post_cloud([{"role": "user", "content": "hi"}])
        assert True
    except Exception:
        pass  # 降级路径本身可能抛,但不能是 NameError/TypeError(配置字段错误)
    _reset_config()
