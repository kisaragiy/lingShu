"""Worker 层 LLM 调用的配置来源与思考轨迹纪律。

实测背景：一次 demo 调研里同时出现 mock / 127.0.0.1:18888 / qwen3.5:9b 三种后端——
因为 workers.py 用 config 模块的 import 快照（启动那一刻的 .env），而 llm.py 已改为
运行时 get_llm_config()。同一进程两套配置来源 → 报告混着真实与占位内容。
同时 workers.py 还保留着 `content or reasoning[-500:]`：答案为空时拿思考轨迹冒充答案。
"""

import pytest

from agent_harness.core.agents import workers


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


def _openai_body(content="", reasoning="", finish="stop"):
    msg = {"role": "assistant", "content": content}
    if reasoning:
        msg["reasoning_content"] = reasoning
    return {
        "choices": [{"message": msg, "finish_reason": finish}],
        "usage": {"total_tokens": 42},
    }


@pytest.fixture
def capture(monkeypatch):
    """Capture the outgoing request; never touch the network."""
    calls = []

    def fake_post(url, json=None, **kwargs):
        calls.append({"url": url, "payload": json})

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr(workers, "ledger_bump", lambda n: None)
    monkeypatch.delenv("HARNESS_LLM_THINKING", raising=False)

    def respond(body, status=200):
        monkeypatch.setattr("requests.post", lambda url, json=None, **kw: (
            calls.append({"url": url, "payload": json}) or _Resp(body, status)))
        return calls

    return respond, calls


def _configure(monkeypatch, url="http://cfg.local/v1/chat/completions", model="cfg-model"):
    import agent_harness.core.config as cfg
    monkeypatch.setattr(cfg, "get_llm_config",
                        lambda: {"api_url": url, "api_key": "k", "model": model})


def test_endpoint_and_model_come_from_runtime_config(monkeypatch, capture):
    """回归：曾用 import 快照（.env 里的 mock / 18888），设置页配置被忽略。"""
    respond, calls = capture
    _configure(monkeypatch, "http://cfg.local/v1/chat/completions", "cfg-model")
    respond(_openai_body(content="答案"))

    out = workers._call_llm([{"role": "user", "content": "hi"}], "sys", max_tokens=256)

    assert out == "答案"
    assert calls[-1]["url"] == "http://cfg.local/v1/chat/completions"
    assert calls[-1]["payload"]["model"] == "cfg-model"


def test_missing_config_does_not_call_llm(monkeypatch, capture):
    respond, calls = capture
    _configure(monkeypatch, "", "")
    respond(_openai_body(content="不该被调用"))

    assert workers._call_llm([{"role": "user", "content": "hi"}]) == ""
    assert calls == []


def test_empty_content_does_not_return_reasoning_as_answer(monkeypatch, capture):
    """ENG-23 同类缺陷：曾 content 空就取 reasoning 尾 500 字当答案。"""
    respond, _ = capture
    _configure(monkeypatch)
    respond(_openai_body(content="", reasoning="我先想想……最终应该是一百万辆", finish="length"))

    assert workers._call_llm([{"role": "user", "content": "hi"}], max_tokens=64) == ""


def test_thinking_disabled_by_default(monkeypatch, capture):
    respond, calls = capture
    _configure(monkeypatch)
    respond(_openai_body(content="ok"))

    workers._call_llm([{"role": "user", "content": "hi"}])

    assert calls[-1]["payload"]["thinking"] == {"type": "disabled"}


def test_thinking_on_removes_disabled_and_raises_budget(monkeypatch, capture):
    respond, calls = capture
    _configure(monkeypatch)
    monkeypatch.setenv("HARNESS_LLM_THINKING", "on")
    respond(_openai_body(content="ok"))

    workers._call_llm([{"role": "user", "content": "hi"}], max_tokens=64)

    payload = calls[-1]["payload"]
    assert "thinking" not in payload
    assert payload["max_tokens"] >= 4096
