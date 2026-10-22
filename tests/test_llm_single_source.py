"""配置单一事实源 + 答案/思考通道分离。

背景：同一条调研链上有多层 LLM 调用各自读 `config` 模块常量（import 快照），
导致进程启动后用户改配置不生效；另有 3 处 `content or reasoning[-500:]`
把思考轨迹尾段当答案返回。实测一次 demo 里同时出现 mock/18888/qwen3.5:9b
三种后端，报告里真实分析与占位内容混在一起。

本文件锁定两条不变式：
  1. 端点与模型只从 `runtime_endpoint()` 取（每次调用重读配置）
  2. 答案只从 `extract_answer()` 取（思考轨迹永不冒充答案）
"""
import requests

from agent_harness.core.pipeline import llm

FAKE_CFG = {"api_url": "http://unit.test/v1/chat/completions", "model": "unit-model"}


class _FakeResp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _resp(content="", reasoning="", finish="stop"):
    return {
        "choices": [{
            "message": {"content": content, "reasoning_content": reasoning},
            "finish_reason": finish,
        }],
        "usage": {"total_tokens": 1},
    }


# ─── 配置单一事实源 ───

def test_runtime_endpoint_rereads_config_every_call(monkeypatch):
    monkeypatch.setattr(llm, "get_llm_config", lambda: dict(FAKE_CFG))
    assert llm.runtime_endpoint() == (FAKE_CFG["api_url"], "unit-model")

    # 用户中途改了设置：下一次调用必须看到新值（不能吃 import 快照）
    monkeypatch.setattr(
        llm, "get_llm_config",
        lambda: {"api_url": "http://changed/v1/chat/completions", "model": "m2"},
    )
    assert llm.runtime_endpoint() == ("http://changed/v1/chat/completions", "m2")


def test_runtime_endpoint_empty_when_unconfigured(monkeypatch):
    monkeypatch.setattr(llm, "get_llm_config", lambda: {})
    assert llm.runtime_endpoint() == ("", "")


# ─── 答案与思考通道分离 ───

def test_extract_answer_returns_content():
    assert llm.extract_answer(_resp(content="答案正文", reasoning="想了很久")) == "答案正文"


def test_extract_answer_never_promotes_reasoning():
    """预算被思考吃光时返回空 —— 不把思考尾段当答案。"""
    assert llm.extract_answer(_resp(content="", reasoning="x" * 900, finish="length"), 2048) == ""


def test_extract_answer_empty_when_both_empty():
    assert llm.extract_answer(_resp()) == ""
    assert llm.extract_answer({}) == ""


# ─── 思考开关 ───

def test_thinking_payload_off_disables(monkeypatch):
    monkeypatch.setenv("HARNESS_LLM_THINKING", "off")
    assert llm.thinking_payload() == {"thinking": {"type": "disabled"}}


def test_thinking_payload_on_omits_field(monkeypatch):
    monkeypatch.setenv("HARNESS_LLM_THINKING", "on")
    assert llm.thinking_payload() == {}


# ─── 调用点：主链路不再吃快照、不拿思考当答案 ───

def test_supervisor_uses_runtime_model_and_ignores_reasoning(monkeypatch):
    monkeypatch.setattr(llm, "get_llm_config", lambda: dict(FAKE_CFG))
    monkeypatch.setenv("HARNESS_LLM_THINKING", "off")
    seen = {}

    def fake_post(url, **kw):
        seen["url"] = url
        seen["payload"] = kw.get("json")
        return _FakeResp(_resp(content="", reasoning="y" * 900, finish="length"))

    monkeypatch.setattr(requests, "post", fake_post)

    from agent_harness.core.agents import supervisor

    content, _finish = supervisor._call_llm_full(
        [{"role": "user", "content": "hi"}], system_prompt="sys"
    )
    assert seen["url"] == FAKE_CFG["api_url"]
    assert seen["payload"]["model"] == "unit-model"
    assert content == ""


def test_graph_uses_runtime_model_and_ignores_reasoning(monkeypatch):
    monkeypatch.setattr(llm, "get_llm_config", lambda: dict(FAKE_CFG))
    seen = {}

    def fake_post(url, **kw):
        seen["url"] = url
        seen["payload"] = kw.get("json")
        return _FakeResp(_resp(content="", reasoning="z" * 900, finish="length"))

    monkeypatch.setattr(requests, "post", fake_post)

    from agent_harness.core.graph import graph

    assert graph._call_llm([{"role": "user", "content": "hi"}], "sys") == ""
    assert seen["url"] == FAKE_CFG["api_url"]
    assert seen["payload"]["model"] == "unit-model"
