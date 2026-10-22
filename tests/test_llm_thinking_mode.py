"""思考模式（thinking）策略回归锁。

背景：Qwen3 / R1 等思考模型在 OpenAI 兼容端点上有两种失败姿势——
  ① content 恒空、全部输出落在 reasoning 字段（ollama /v1 对 qwen3 系就是这样）；
  ② 思考把 max_tokens 吃光，答案一个 token 都没轮到，finish_reason=length。

错误修法是「全局禁思考」；业界做法是「按需开 + 给够预算 + 识别预算不足后重试」。
本文件锁住这套行为：开思考时不发 disabled 字段、预算自动抬到下限、content 空且
finish=length 时加预算重试一次、重试仍失败既不冒充答案也不写缓存。
"""
import pytest

from agent_harness.core.pipeline import llm


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setattr(llm, "_llm_cache_key", lambda *a, **k: None)
    monkeypatch.setattr(llm, "ledger_bump", lambda *a, **k: None)
    monkeypatch.setattr(llm, "_record_llm_metrics", lambda *a, **k: None)
    monkeypatch.setattr(llm, "_emit_llm_envelope", lambda *a, **k: None)
    monkeypatch.setattr(llm, "get_llm_config", lambda: {"api_url": "http://stub", "model": "qwen3"})
    monkeypatch.delenv(llm.THINKING_MODE_ENV, raising=False)
    return monkeypatch


def _payload(content="", reasoning="", finish="stop", reasoning_field="reasoning_content"):
    msg = {"content": content}
    if reasoning:
        msg[reasoning_field] = reasoning
    return {"choices": [{"message": msg, "finish_reason": finish}], "usage": {"total_tokens": 7}}


# ─── 开关：不是全局禁 ──────────────────────────────────────────────────────

def test_default_mode_sends_thinking_disabled(env):
    seen = {}

    def _post(session, url, payload, headers=None, timeout=0):
        seen.update(payload)
        return _Resp(_payload("答案"))

    env.setattr(llm, "_http_post", _post)
    text, _ = llm.call_llama([{"role": "user", "content": "hi"}], max_tokens=512)
    assert text == "答案"
    assert seen["thinking"] == {"type": "disabled"}, "默认应显式关思考（不改变既有部署行为）"


def test_thinking_on_omits_disabled_and_raises_budget(env):
    env.setenv(llm.THINKING_MODE_ENV, "on")
    seen = {}

    def _post(session, url, payload, headers=None, timeout=0):
        seen.update(payload)
        return _Resp(_payload("答案"))

    env.setattr(llm, "_http_post", _post)
    llm.call_llama([{"role": "user", "content": "hi"}], max_tokens=512)
    assert "thinking" not in seen, "开思考时不该再发 disabled —— 那等于全局禁思考"
    assert seen["max_tokens"] >= llm.THINKING_MIN_BUDGET, \
        f"开思考必须给够预算，否则思考吃光 token 答案出不来: {seen['max_tokens']}"


def test_thinking_on_accepts_env_truthy_variants(env):
    for value in ("on", "1", "true", "YES"):
        env.setenv(llm.THINKING_MODE_ENV, value)
        assert llm._thinking_mode() == "on", value
    env.setenv(llm.THINKING_MODE_ENV, "off")
    assert llm._thinking_mode() == "off"


# ─── 字段兼容 ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field", ["reasoning_content", "reasoning", "thinking"])
def test_reasoning_field_names_all_recognized(field):
    content, reasoning, finish, cacheable = llm._extract_openai_content(
        _payload("答案", "思考", reasoning_field=field), 100)
    assert (content, reasoning, finish, cacheable) == ("答案", "思考", "stop", True)


# ─── 预算不足 → 重试一次 ───────────────────────────────────────────────────

def test_budget_exhausted_by_thinking_retries_with_bigger_budget(env):
    calls = []

    def _post(session, url, payload, headers=None, timeout=0):
        calls.append(payload["max_tokens"])
        if len(calls) == 1:
            return _Resp(_payload("", "想了很久", finish="length"))   # 预算被思考吃光
        return _Resp(_payload("最终答案"))

    env.setattr(llm, "_http_post", _post)
    text, _ = llm.call_llama([{"role": "user", "content": "hi"}], max_tokens=512)
    assert text == "最终答案"
    assert len(calls) == 2, f"应重试一次，实际调用 {len(calls)} 次"
    assert calls[1] > calls[0], f"重试必须加预算: {calls}"
    assert calls[1] <= llm.THINKING_RETRY_CAP


def test_retry_twice_still_empty_returns_empty_without_caching(env):
    cached = []
    env.setattr(llm, "_llm_cache_key", lambda *a, **k: "k1")
    env.setattr(llm, "_llm_cache_set", lambda *a, **k: cached.append(a))
    env.setattr(llm, "_llm_cache_get", lambda *a, **k: None)
    env.setattr(llm, "_http_post",
                lambda *a, **k: _Resp(_payload("", "一直思考", finish="length")))

    text, _ = llm.call_llama([{"role": "user", "content": "hi"}], max_tokens=512)
    assert text == "", "思考过程不能冒充答案"
    assert cached == [], "没产出答案的结果绝不能写缓存（会污染后续请求）"


def test_budget_retry_stops_at_cap(env):
    calls = []
    env.setattr(llm, "_http_post", lambda s, u, p, headers=None, timeout=0:
                calls.append(p["max_tokens"]) or _Resp(_payload("", "x" * 10, finish="length")))
    llm.call_llama([{"role": "user", "content": "hi"}], max_tokens=llm.THINKING_RETRY_CAP)
    assert calls == [llm.THINKING_RETRY_CAP], "已到上限就不该再无意义重试"


def test_length_without_reasoning_does_not_retry(env):
    """普通模型截断（没有思考轨迹）不是思考预算问题 —— 不该盲目重试烧钱。"""
    calls = []
    env.setattr(llm, "_http_post", lambda s, u, p, headers=None, timeout=0:
                calls.append(1) or _Resp(_payload("半截答案", "", finish="length")))
    text, _ = llm.call_llama([{"role": "user", "content": "hi"}], max_tokens=64)
    assert text == "半截答案"
    assert len(calls) == 1
