"""
Circuit-breaker wiring tests (Day 3-4 hardening).

Proves the previously-dead breaker paths now actually fire:
- token budget trips when fed
- no-progress trips when fed
- graph_multi feeds + checks the breaker mid-loop (finalize on trip)
- LLM retry (_http_post) retries network errors then gives up cleanly
"""
import importlib

import pytest

from agent_harness.core.graph import graph_multi as gm
from agent_harness.core.pipeline.circuit_breaker import CircuitBreaker
from agent_harness.core.pipeline import llm as llm_mod
from agent_harness.core.pipeline.llm import _http_post


# ─── CircuitBreaker unit behavior ───

def test_token_budget_trips_when_fed():
    cb = CircuitBreaker(max_tokens=100)
    assert cb.check()["tripped"] is False
    cb.add_tokens(150)
    r = cb.check()
    assert r["tripped"] is True
    assert any("token 超预算" in x for x in r["reasons"])


def test_no_progress_trips_when_fed():
    cb = CircuitBreaker(max_no_progress=3)
    cb.record_output("same")
    cb.record_output("same")
    assert cb.check()["tripped"] is False
    cb.record_output("same")
    r = cb.check()
    assert r["tripped"] is True
    assert any("无变化" in x for x in r["reasons"])


def test_reset_clears_trip():
    cb = CircuitBreaker(max_tokens=10)
    cb.add_tokens(999)
    assert cb.check()["tripped"] is True
    cb.reset()
    assert cb.check()["tripped"] is False


# ─── graph_multi feeding helpers ───

def _reset_ledger():
    llm_mod._LLM_TOKEN_LEDGER["total"] = 0


def test_feed_breaker_tokens_incremental():
    _reset_ledger()
    cb = CircuitBreaker(max_tokens=10**9)
    gm._feed_breaker_tokens(cb)
    assert cb.tokens_used == 0
    llm_mod.ledger_bump(100)
    gm._feed_breaker_tokens(cb)
    assert cb.tokens_used == 100
    # No new tokens → no double count
    gm._feed_breaker_tokens(cb)
    assert cb.tokens_used == 100


def test_feed_breaker_output_feeds_no_progress():
    cb = CircuitBreaker(max_tokens=10**9, max_no_progress=3)
    gm._feed_breaker_output(cb, "search", "结果A")
    gm._feed_breaker_output(cb, "search", "结果A")
    gm._feed_breaker_output(cb, "search", "结果A")
    assert cb.check()["tripped"] is True


def test_supervisor_route_finalizes_when_tripped():
    cb = CircuitBreaker(max_tokens=10)
    cb.add_tokens(50)
    state = {"circuit_breaker": cb, "all_done": False, "round": 0,
             "request": "x", "plan": [], "current_step": 0, "results": [],
             "errors": [], "retry_count": 0, "final_output": "",
             "conversation_history": [], "goal": "", "stop_conditions": [],
             "loop_state_path": "", "iteration_count": 0,
             "stop_conditions_met": False, "should_finalize": False,
             "enable_review": False, "review_passed": False,
             "review_feedback": "", "validator_fixes": [], "trace_id": "",
             "trace_steps": []}
    assert gm.supervisor_route(state) == "finalize"


# ─── LLM retry wiring ───

class _FakeResp:
    status_code = 200

    def json(self):
        return {"choices": [{"message": {"content": "ok"}}],
                "usage": {"total_tokens": 5}}


class _FakeSession:
    """Raises ConnectionError `fails` times, then returns a 200 response."""

    def __init__(self, fails=0):
        self.fails = fails
        self.attempts = 0

    def post(self, *args, **kwargs):
        self.attempts += 1
        if self.attempts <= self.fails:
            raise ConnectionError("network down")
        return _FakeResp()


def test_http_post_retries_then_succeeds():
    sess = _FakeSession(fails=2)
    resp = _http_post(sess, "http://llm.local", {"model": "x"})
    assert resp.status_code == 200
    assert sess.attempts == 3  # fail, fail, ok


def test_http_post_gives_up_after_max_attempts():
    sess = _FakeSession(fails=99)
    with pytest.raises(ConnectionError):
        _http_post(sess, "http://llm.local", {"model": "x"})
    assert sess.attempts == 3  # exactly max_attempts, no infinite retry


def test_call_llama_returns_empty_after_retries_exhausted(monkeypatch):
    """Local LLM unreachable → clean empty result, never raises."""
    def _boom(session, url, payload, headers=None, timeout=120):
        raise ConnectionError("unreachable")
    monkeypatch.setattr(llm_mod, "_http_post", _boom)
    text, tokens = llm_mod.call_llama(
        [{"role": "user", "content": "circuit-breaker-wiring-unique-xyz"}],
        system_prompt="", max_tokens=16,
    )
    assert text == ""
    assert tokens == 0
