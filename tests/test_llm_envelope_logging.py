"""可复现信封的**接线**测试（P1-1 + P1-4）。

验证两条独立的 LLM 调用路径都落了信封：
  * `pipeline/llm.py::call_llama` / `_post_cloud`（业务模块入口）
  * `agents/supervisor.py::_call_llm_full`（Agent 主链路，绕过 llm.py）

并且：信封同时带旧键与 `gen_ai.*` 键；观测层故障绝不影响 LLM 调用结果。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core import agent_log  # noqa: E402
from agent_harness.core import reproducibility as repro  # noqa: E402
from agent_harness.core.pipeline import llm  # noqa: E402
from agent_harness.core.agents import supervisor  # noqa: E402


class _Resp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def no_cache(monkeypatch):
    """关掉 LLM 缓存（缓存命中不产生真实调用）。"""
    monkeypatch.setattr(llm, "_llm_cache_key", lambda *a, **kw: None)
    monkeypatch.setattr(llm, "_llm_cache_get", lambda *a, **kw: None)


@pytest.fixture
def capture(monkeypatch):
    """捕获对 agent_log.log_llm_envelope 的调用（不改其行为，仅记录参数）。"""
    calls = []

    def rec(session_id, envelope, usage=None):
        calls.append({"session_id": session_id, "envelope": envelope, "usage": usage})

    monkeypatch.setattr(agent_log, "log_llm_envelope", rec)
    return calls


# ── 业务模块路径（llm.py）────────────────────────────────

def test_call_llama_records_envelope(monkeypatch, no_cache, capture):
    monkeypatch.setattr(llm, "get_llm_config", lambda: {
        "model": "test-model", "api_url": "http://x/v1/chat/completions", "api_key": "k"})
    monkeypatch.setattr(llm, "_http_post", lambda *a, **kw: _Resp({
        "choices": [{"message": {"content": "hello"}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
        "model": "test-model-v2", "system_fingerprint": "fp_1",
    }))

    llm.call_llama([{"role": "user", "content": "hi"}], system_prompt="sys")

    assert capture, "本地 LLM 调用未落可复现信封"
    env = capture[-1]["envelope"]
    assert env["model"] == "test-model"
    assert env["model_version"] == "test-model-v2"
    assert env["system_fingerprint"] == "fp_1"
    assert env["temperature"] == 0.3
    assert len(env["prompt_hash"]) == 64, "prompt_hash 应由稳定哈希算出"


def test_call_llama_envelope_has_otel_keys(monkeypatch, no_cache, capture):
    monkeypatch.setattr(llm, "get_llm_config", lambda: {
        "model": "test-model", "api_url": "http://x", "api_key": "k"})
    monkeypatch.setattr(llm, "_http_post", lambda *a, **kw: _Resp({
        "choices": [{"message": {"content": "hello"}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
    }))

    llm.call_llama([{"role": "user", "content": "hi"}])

    rec = repro.envelope_record(capture[-1]["envelope"], capture[-1]["usage"])
    assert rec["gen_ai.request.model"] == "test-model"
    assert rec["gen_ai.request.temperature"] == 0.3
    assert rec["gen_ai.usage.input_tokens"] == 12
    assert rec["gen_ai.usage.output_tokens"] == 7


def test_call_llama_records_envelope_on_failure(monkeypatch, no_cache, capture):
    """LLM 不可达也要落信封 —— 事故现场正是失败那次，缺了就无法复现。"""
    monkeypatch.setattr(llm, "get_llm_config", lambda: {
        "model": "dead-model", "api_url": "http://127.0.0.1:1/v1", "api_key": "k"})

    def _boom(*a, **kw):
        raise ConnectionError("refused")

    monkeypatch.setattr(llm, "_http_post", _boom)

    text, _ = llm.call_llama([{"role": "user", "content": "hi"}])
    assert text == ""
    assert capture, "失败的 LLM 调用未落信封（事故无法复现）"
    assert capture[-1]["envelope"]["model"] == "dead-model"


# ── Agent 主链路（supervisor.py）────────────────────────

def test_supervisor_records_envelope(monkeypatch, capture):
    import requests

    monkeypatch.setattr(requests, "post", lambda *a, **kw: _Resp({
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }))

    content, fr = supervisor._call_llm_full([{"role": "user", "content": "hi"}],
                                            system_prompt="sys")
    assert content == "ok" and fr == "stop"
    assert capture, "Agent 主链路 LLM 调用未落信封"
    env = capture[-1]["envelope"]
    assert env["model"] == supervisor.MODEL_LLAMA
    assert len(env["prompt_hash"]) == 64


def test_supervisor_records_envelope_on_failure(monkeypatch, capture):
    import requests

    def _boom(*a, **kw):
        raise ConnectionError("refused")

    monkeypatch.setattr(requests, "post", _boom)
    supervisor._call_llm_full([{"role": "user", "content": "hi"}])
    assert capture


# ── 观测层故障隔离 ───────────────────────────────────────

def test_envelope_logging_failure_does_not_break_llm(monkeypatch, no_cache):
    """信封落盘失败绝不能改变 LLM 调用结果（与指标同一条原则）。"""
    monkeypatch.setattr(llm, "get_llm_config", lambda: {
        "model": "safe-model", "api_url": "http://x", "api_key": "k"})
    monkeypatch.setattr(llm, "_http_post", lambda *a, **kw: _Resp({
        "choices": [{"message": {"content": "still-works"}}], "usage": {"total_tokens": 5}}))

    def _explode(*a, **kw):
        raise RuntimeError("log backend down")

    monkeypatch.setattr(agent_log, "log_llm_envelope", _explode)
    text, _ = llm.call_llama([{"role": "user", "content": "hi"}])
    assert text == "still-works"


# ── 端到端：真正写进会话日志 ─────────────────────────────

def test_envelope_lands_in_session_jsonl(monkeypatch, no_cache):
    """设好会话上下文后，信封确实写进 agent_logs/<session>.jsonl。"""
    sid = "envelope-e2e-session"
    agent_log.clear_logs(sid)
    token = agent_log.set_current_session(sid)
    try:
        monkeypatch.setattr(llm, "get_llm_config", lambda: {
            "model": "e2e-model", "api_url": "http://x", "api_key": "k"})
        monkeypatch.setattr(llm, "_http_post", lambda *a, **kw: _Resp({
            "choices": [{"message": {"content": "hi"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}}))
        llm.call_llama([{"role": "user", "content": "hi"}])
    finally:
        agent_log._current_session.reset(token)

    events = [e for e in agent_log.get_logs(sid) if e["type"] == "llm_call"]
    assert events, "信封未写入会话日志"
    d = events[0]["data"]
    assert d["model"] == "e2e-model"                 # 旧键仍在
    assert d["gen_ai.request.model"] == "e2e-model"  # 新键也在（P1-4）
    assert d["gen_ai.usage.input_tokens"] == 2
    agent_log.clear_logs(sid)


def test_log_llm_envelope_no_session_does_not_error():
    """无会话上下文（session_id=None 且 contextvar 未设）不得报错。"""
    env = repro.reproducibility_envelope(model="m")
    agent_log.log_llm_envelope(None, env, {"prompt_tokens": 1})
