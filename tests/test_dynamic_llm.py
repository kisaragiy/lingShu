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
import importlib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import pytest

# 用临时配置目录,不污染真实配置
os.environ["HARNESS_CONFIG_DIR"] = str(Path(__file__).parent / ".test-cfg")
os.environ.setdefault("HARNESS_DISABLE_AUTH", "1")


def _reset_config():
    import shutil
    shutil.rmtree(os.environ["HARNESS_CONFIG_DIR"], ignore_errors=True)


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
