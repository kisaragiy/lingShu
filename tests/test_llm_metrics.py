"""LLM 调用指标接线 —— `record_llm_call` / `record_agent_call` 曾长期是死代码。

原来 `core/metrics.py` 定义了这两个函数，但 `grep -rn` 全仓零调用点 ——
`harness_llm_calls_total` / `harness_llm_tokens_total` / `harness_agent_calls_total`
**恒为 0**（"装了没通电"的典型）。Token 消耗是 Agent 成本控制的唯一依据，这条必须通。

判据（按「指标三问」）：
  ① 通电：真实调用路径上确实被调用
  ② 数据源可信：token 数取自上游 usage，而不是猜
  ③ 数字符合直觉：成功/失败分别计数，缓存命中不冒充 LLM 调用
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core import metrics  # noqa: E402
from agent_harness.core.pipeline import llm  # noqa: E402


def _val(tool_model: str, status: str) -> float:
    try:
        return metrics.LLM_CALLS_TOTAL.labels(tool_model, status)._value.get()
    except Exception:
        return 0.0


def _tokens(model: str, kind: str) -> float:
    try:
        return metrics.LLM_TOKENS_TOTAL.labels(model, kind)._value.get()
    except Exception:
        return 0.0


class _Resp:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def no_cache(monkeypatch):
    """关掉 LLM 缓存 —— 缓存命中不是真实 LLM 调用，不该计数。"""
    monkeypatch.setattr(llm, "_llm_cache_key", lambda *a, **kw: None)
    monkeypatch.setattr(llm, "_llm_cache_get", lambda *a, **kw: None)


def test_successful_local_call_is_counted(monkeypatch, no_cache):
    """本地 LLM 成功调用 → calls_total 计数 + token 拆分入账。"""
    monkeypatch.setattr(llm, "get_llm_config", lambda: {
        "model": "test-model", "api_url": "http://x/v1/chat/completions", "api_key": "k"})
    monkeypatch.setattr(llm, "_http_post", lambda *a, **kw: _Resp({
        "choices": [{"message": {"content": "hello"}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
    }))

    before_c = _val("test-model", "success")
    before_p = _tokens("test-model", "prompt")
    before_o = _tokens("test-model", "completion")

    text, tokens = llm.call_llama([{"role": "user", "content": "hi"}])

    assert text == "hello" and tokens == 19
    assert _val("test-model", "success") == before_c + 1, "LLM 调用没进指标（死代码回归？）"
    assert _tokens("test-model", "prompt") == before_p + 12, "prompt token 未入账"
    assert _tokens("test-model", "completion") == before_o + 7, "completion token 未入账"


def test_failed_local_call_is_counted_as_error(monkeypatch, no_cache):
    """LLM 不可达 → 必须记 error（否则监控里"零调用"，实际是全线失败）。"""
    monkeypatch.setattr(llm, "get_llm_config", lambda: {
        "model": "dead-model", "api_url": "http://127.0.0.1:1/v1", "api_key": "k"})

    def _boom(*a, **kw):
        raise ConnectionError("refused")

    monkeypatch.setattr(llm, "_http_post", _boom)

    before = _val("dead-model", "error")
    text, tokens = llm.call_llama([{"role": "user", "content": "hi"}])

    assert text == "" and tokens == 0
    assert _val("dead-model", "error") == before + 1, "LLM 失败未记 error —— 故障在监控里不可见"


def test_cache_hit_does_not_fake_llm_call(monkeypatch):
    """缓存命中不是 LLM 调用，不得计入 calls_total（防虚高）。"""
    monkeypatch.setattr(llm, "get_llm_config", lambda: {
        "model": "cached-model", "api_url": "http://x", "api_key": "k"})
    monkeypatch.setattr(llm, "_llm_cache_key", lambda *a, **kw: "k1")
    monkeypatch.setattr(llm, "_llm_cache_get", lambda *a, **kw: ("from-cache", 99))

    before = _val("cached-model", "success")
    text, tokens = llm.call_llama([{"role": "user", "content": "hi"}])

    assert text == "from-cache"
    assert _val("cached-model", "success") == before, "缓存命中被冒充成 LLM 调用（虚高）"


def test_total_only_usage_still_records_tokens(monkeypatch, no_cache):
    """上游只给 total_tokens 时也要入账（归到 completion 侧），不能丢。"""
    monkeypatch.setattr(llm, "get_llm_config", lambda: {
        "model": "total-only", "api_url": "http://x", "api_key": "k"})
    monkeypatch.setattr(llm, "_http_post", lambda *a, **kw: _Resp({
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"total_tokens": 33},
    }))

    before = _tokens("total-only", "completion")
    llm.call_llama([{"role": "user", "content": "hi"}])
    assert _tokens("total-only", "completion") == before + 33


def test_metric_failure_does_not_break_llm(monkeypatch, no_cache):
    """观测层故障绝不能影响 LLM 调用结果。"""
    monkeypatch.setattr(llm, "get_llm_config", lambda: {
        "model": "safe-model", "api_url": "http://x", "api_key": "k"})
    monkeypatch.setattr(llm, "_http_post", lambda *a, **kw: _Resp({
        "choices": [{"message": {"content": "still-works"}}], "usage": {"total_tokens": 5}}))

    def _explode(*a, **kw):
        raise RuntimeError("metrics backend down")

    monkeypatch.setattr(metrics, "record_llm_call", _explode)
    text, _ = llm.call_llama([{"role": "user", "content": "hi"}])
    assert text == "still-works", "指标采集故障把 LLM 调用结果改坏了"


# ── Agent 主链路（绕过 llm.py 的那条）────────────────

def test_supervisor_main_path_success_is_counted(monkeypatch):
    """supervisor._call_llm_full 是 Agent 主链路的 LLM 入口，它绕过 llm.py —— 必须单独接线。"""
    import requests

    from agent_harness.core.agents import supervisor

    from agent_harness.core.pipeline import llm as _llm
    monkeypatch.setattr(_llm, "get_llm_config", lambda: {
        "model": "unit-model", "api_url": "http://unit.test/v1", "api_key": "k"})
    monkeypatch.setattr(requests, "post", lambda *a, **kw: _Resp({
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}}))

    before = _val("unit-model", "success")
    content, fr = supervisor._call_llm_full([{"role": "user", "content": "hi"}])
    assert content == "ok" and fr == "stop"
    assert _val("unit-model", "success") == before + 1, "主链路 LLM 调用未进指标"


def test_supervisor_main_path_failure_is_counted(monkeypatch):
    """主链路 LLM 不可达必须记 error —— 实测日志 8+ 次异常而指标 error 为零（监控失明）。"""
    import requests

    from agent_harness.core.agents import supervisor

    def _boom(*a, **kw):
        raise ConnectionError("refused")

    from agent_harness.core.pipeline import llm as _llm
    monkeypatch.setattr(_llm, "get_llm_config", lambda: {
        "model": "unit-model", "api_url": "http://unit.test/v1", "api_key": "k"})
    monkeypatch.setattr(requests, "post", _boom)

    before = _val("unit-model", "error")
    content, fr = supervisor._call_llm_full([{"role": "user", "content": "hi"}])
    assert content == "" and fr == "error"
    assert _val("unit-model", "error") == before + 1, (
        "主链路 LLM 失败未进指标 —— 后端全挂时监控会显示「零调用」而不是「全线失败」"
    )
