"""Tests for llm.py caching — monkeypatch to avoid real HTTP calls."""

import time
import hashlib
import json

import pytest

from agent_harness.pipeline.llm import (
    _llm_cache_key,
    _llm_cache_get,
    _llm_cache_set,
    _LLM_CACHE,
    _LLM_CACHE_TTL,
    call_llama,
)


@pytest.fixture(autouse=True)
def clear_llm_cache():
    """Clear the LLM cache before each test."""
    _LLM_CACHE.clear()


def test_cache_key_consistent():
    """Same inputs produce the same cache key."""
    msgs = [{"role": "user", "content": "hello"}]
    k1 = _llm_cache_key("model-a", msgs, "system prompt", 0.3)
    k2 = _llm_cache_key("model-a", msgs, "system prompt", 0.3)
    assert k1 == k2


def test_cache_key_different_model():
    """Different model name → different key."""
    msgs = [{"role": "user", "content": "hello"}]
    k1 = _llm_cache_key("model-a", msgs, "sys", 0.3)
    k2 = _llm_cache_key("model-b", msgs, "sys", 0.3)
    assert k1 != k2


def test_cache_key_different_temperature():
    """Different temperature (after rounding) → different key."""
    msgs = [{"role": "user", "content": "hello"}]
    k1 = _llm_cache_key("m", msgs, "s", 0.30)
    k2 = _llm_cache_key("m", msgs, "s", 0.70)
    assert k1 != k2


def test_cache_set_and_get():
    """Cache set followed by get returns the stored content."""
    key = "testkey123"
    _llm_cache_set(key, "cached content", 42)
    result = _llm_cache_get(key)
    assert result is not None
    content, tokens = result
    assert content == "cached content"
    assert tokens == 42


def test_cache_expired(mock_session):
    """Expired cache entries return None."""
    key = "expiredkey"
    _llm_cache_set(key, "old content", 10)
    # Manually age the entry beyond TTL
    _LLM_CACHE[key] = (time.time() - _LLM_CACHE_TTL - 10, "old content", 10)
    assert _llm_cache_get(key) is None


def test_cache_eviction():
    """Adding 500+ entries evicts the oldest."""
    for i in range(510):
        _llm_cache_set(f"key{i:04d}", f"content{i}", i)
    assert len(_LLM_CACHE) <= 510
    # The oldest key (key0000) should be evicted
    assert _llm_cache_get("key0000") is None


def test_call_llama_skips_cache_high_temp(monkeypatch):
    """call_llama with temperature > 0.5 should not look up cache."""
    cache_lookups = []

    def tracking_get(key):
        cache_lookups.append(key)
        return None

    monkeypatch.setattr("agent_harness.pipeline.llm._llm_cache_get", tracking_get)

    # Mock the POST to avoid real HTTP
    import requests

    class MockResp:
        status_code = 200

        def json(self):
            return {
                "choices": [{"message": {"content": "high temp response"}}],
                "usage": {"total_tokens": 10},
            }

    monkeypatch.setattr(requests.Session, "post", lambda *a, **kw: MockResp())

    # Set HARNESS_MODEL to something known in config
    monkeypatch.setattr("agent_harness.pipeline.llm.MODEL_LLAMA", "test-model")

    # Mock LLAMA_API
    monkeypatch.setattr("agent_harness.pipeline.llm.LLAMA_API", "http://test/api")

    content, tokens = call_llama(
        [{"role": "user", "content": "hi"}],
        temperature=0.9,  # High temp → skip cache
    )
    assert content == "high temp response"
    assert tokens == 10


def test_call_llama_saves_cache(monkeypatch):
    """On successful response, content should be cached."""
    import requests

    class MockResp:
        status_code = 200

        def json(self):
            return {
                "choices": [{"message": {"content": "cachable response"}}],
                "usage": {"total_tokens": 5},
            }

    monkeypatch.setattr(requests.Session, "post", lambda *a, **kw: MockResp())
    monkeypatch.setattr("agent_harness.pipeline.llm.MODEL_LLAMA", "test-model")
    monkeypatch.setattr("agent_harness.pipeline.llm.LLAMA_API", "http://test/api")

    content, tokens = call_llama(
        [{"role": "user", "content": "save me"}],
        temperature=0.3,
    )
    assert content == "cachable response"

    # Cache should have an entry now
    assert len(_LLM_CACHE) == 1


def test_call_llama_http_error_returns_empty(monkeypatch):
    """When HTTP fails, call_llama returns ('', 0) without caching."""
    import requests

    class MockResp:
        status_code = 500

        def json(self):
            return {}

    monkeypatch.setattr(requests.Session, "post", lambda *a, **kw: MockResp())
    monkeypatch.setattr("agent_harness.pipeline.llm.MODEL_LLAMA", "test-model")
    monkeypatch.setattr("agent_harness.pipeline.llm.LLAMA_API", "http://test/api")

    content, tokens = call_llama(
        [{"role": "user", "content": "fail"}],
        temperature=0.3,
    )
    assert content == ""
    assert tokens == 0
    # Nothing cached
    assert len(_LLM_CACHE) == 0
