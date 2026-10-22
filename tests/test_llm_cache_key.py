"""ENG-24：LLM 缓存 key 必须覆盖所有「改变响应」的请求参数。

背景：`thinking_payload()` / `_thinking_mode()` 决定请求体里是否带
`{"thinking":{"type":"disabled"}}`，并连带把 max_tokens 抬到 THINKING_MIN_BUDGET
下限。两条路径的响应不同，但 `_llm_cache_key` 原来只吃
(model, messages, system_prompt, temperature) —— 先跑 thinking=off 再跑 on
（或反之）时，第二次直接命中第一次的缓存，返回**别的配置**的结果。

本文件锁住：
  1. thinking on/off → 不同 key（显式传参 + 走环境两种入口都锁）
  2. 同输入 → 同 key（可缓存性）
  3. temperature / max_tokens 变化 → 不同 key（防回归）
  4. 调用点真的把 thinking 传进了 key（端到端：off 之后 on 不得命中缓存）

全部用例不发真实 HTTP 请求。
"""
import pytest

from agent_harness.core.pipeline import llm

MSGS = [{"role": "user", "content": "hello"}]


@pytest.fixture(autouse=True)
def clean_cache(monkeypatch):
    """每个用例从空缓存 + 默认 thinking 开始。"""
    llm._LLM_CACHE.clear()
    monkeypatch.delenv(llm.THINKING_MODE_ENV, raising=False)
    yield
    llm._LLM_CACHE.clear()


# ─── 1. thinking 维度 ──────────────────────────────────────────────────────

def test_key_differs_by_explicit_thinking():
    off = llm._llm_cache_key("m", MSGS, "sys", 0.3, thinking="off", max_tokens=512)
    on = llm._llm_cache_key("m", MSGS, "sys", 0.3, thinking="on", max_tokens=512)
    assert off != on, "thinking 开关改变请求体，缓存结果不可互换"


def test_key_differs_by_env_thinking(monkeypatch):
    """不显式传参时 key 也必须跟当前环境一致（防调用方漏传）。"""
    monkeypatch.setenv(llm.THINKING_MODE_ENV, "off")
    off = llm._llm_cache_key("m", MSGS, "sys", 0.3)
    monkeypatch.setenv(llm.THINKING_MODE_ENV, "on")
    on = llm._llm_cache_key("m", MSGS, "sys", 0.3)
    assert off != on


# ─── 2. 稳定性 ─────────────────────────────────────────────────────────────

def test_key_stable_for_identical_input():
    a = llm._llm_cache_key("m", MSGS, "sys", 0.3, thinking="on", max_tokens=4096)
    b = llm._llm_cache_key("m", MSGS, "sys", 0.3, thinking="on", max_tokens=4096)
    assert a == b, "同输入必须同 key，否则缓存永不命中"


def test_key_ignores_temperature_rounding_noise():
    assert llm._llm_cache_key("m", MSGS, "s", 0.30) == llm._llm_cache_key("m", MSGS, "s", 0.30001)


# ─── 3. 其它影响输出的参数 ─────────────────────────────────────────────────

def test_key_differs_by_temperature():
    assert llm._llm_cache_key("m", MSGS, "s", 0.30) != llm._llm_cache_key("m", MSGS, "s", 0.70)


def test_key_differs_by_max_tokens():
    a = llm._llm_cache_key("m", MSGS, "s", 0.3, thinking="on", max_tokens=4096)
    b = llm._llm_cache_key("m", MSGS, "s", 0.3, thinking="on", max_tokens=8192)
    assert a != b, "预算不同 → 输出可截断位置不同，不能共用缓存"


def test_key_differs_by_model():
    assert llm._llm_cache_key("model-a", MSGS, "s", 0.3) != llm._llm_cache_key("model-b", MSGS, "s", 0.3)


# ─── 4. 调用点接线（端到端，不打网络）─────────────────────────────────────

class _Resp:
    status_code = 200

    def __init__(self, content):
        self._content = content

    def json(self):
        return {
            "choices": [{"message": {"content": self._content}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 3},
        }


def _stub_llm(monkeypatch, answers):
    """把 call_llama 的外围依赖全部换成桩，记录真实发出的请求次数。"""
    sent = []

    def _post(session, url, payload, headers=None, timeout=0):
        sent.append(payload)
        return _Resp(answers[min(len(sent) - 1, len(answers) - 1)])

    monkeypatch.setattr(llm, "_http_post", _post)
    monkeypatch.setattr(llm, "get_llm_config", lambda: {"api_url": "http://stub", "model": "qwen3"})
    monkeypatch.setattr(llm, "ledger_bump", lambda *a, **k: None)
    monkeypatch.setattr(llm, "_record_llm_metrics", lambda *a, **k: None)
    monkeypatch.setattr(llm, "_emit_llm_envelope", lambda *a, **k: None)
    return sent


def test_call_llama_does_not_reuse_cache_across_thinking_modes(monkeypatch):
    sent = _stub_llm(monkeypatch, ["答案-off", "答案-on"])

    monkeypatch.setenv(llm.THINKING_MODE_ENV, "off")
    first, _ = llm.call_llama(MSGS, max_tokens=512)
    monkeypatch.setenv(llm.THINKING_MODE_ENV, "on")
    second, _ = llm.call_llama(MSGS, max_tokens=512)

    assert len(sent) == 2, f"两种 thinking 配置必须各走一次真实请求，实际 {len(sent)} 次"
    assert (first, second) == ("答案-off", "答案-on")
    assert "thinking" in sent[0] and "thinking" not in sent[1]


def test_call_llama_second_identical_call_hits_cache(monkeypatch):
    sent = _stub_llm(monkeypatch, ["答案"])
    monkeypatch.setenv(llm.THINKING_MODE_ENV, "off")
    first, _ = llm.call_llama(MSGS, max_tokens=512)
    second, _ = llm.call_llama(MSGS, max_tokens=512)
    assert len(sent) == 1, "同配置同消息第二次应命中缓存（缓存本身没被改坏）"
    assert first == second == "答案"
