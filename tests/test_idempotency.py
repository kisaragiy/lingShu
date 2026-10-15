"""P0-3（L 部分）幂等 key + 决策/执行分离 —— 测试。

覆盖：
  * IdempotencyStore：内存/文件持久化、is_executed/get/mark/reset；
  * run_once：同 key 只执行一次（重放上次结果）；
  * call_idempotent：外部写操作前查 key，已执行则**跳过工具调用**；
  * DecisionExecutor：**重试不重问 LLM**（决策只跑一次、重放持久化决策），
    副作用幂等（同 effect_key 只执行一次）。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core.idempotency import (  # noqa: E402
    DecisionExecutor,
    IdempotencyStore,
    call_idempotent,
    run_once,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """本文件会把测试工具注册进全局 TOOL_REGISTRY，用完必须清掉，
    否则污染 test_tool_metadata 等按注册表全量断言的用例。"""
    from agent_harness.core.tools.registry import TOOL_REGISTRY

    before = set(TOOL_REGISTRY)
    yield
    for k in list(TOOL_REGISTRY):
        if k not in before:
            del TOOL_REGISTRY[k]


# ─── IdempotencyStore ───

def test_store_memory_basic():
    s = IdempotencyStore()
    assert s.is_executed("k") is False
    s.mark("k", {"id": 1})
    assert s.is_executed("k") is True
    assert "k" in s
    assert s.get("k") == {"id": 1}
    assert s.get_record("k")["value"] == {"id": 1}
    assert len(s) == 1
    s.reset()
    assert s.is_executed("k") is False


def test_store_persists_to_file(tmp_path):
    p = tmp_path / "idem.json"
    s1 = IdempotencyStore(p)
    s1.mark("order:1", {"charged": True})
    # 新实例从磁盘读回 —— 跨进程/跨重试存活
    s2 = IdempotencyStore(p)
    assert s2.is_executed("order:1") is True
    assert s2.get("order:1") == {"charged": True}


def test_store_missing_file_is_empty(tmp_path):
    s = IdempotencyStore(tmp_path / "nope.json")
    assert s.is_executed("x") is False


# ─── run_once ───

def test_run_once_executes_single_time():
    s = IdempotencyStore()
    calls = {"n": 0}

    def _fn(x):
        calls["n"] += 1
        return x * 2

    assert run_once(s, "k", _fn, 21) == 42
    # 第二次直接重放，不再调用 _fn
    assert run_once(s, "k", _fn, 999) == 42
    assert calls["n"] == 1


# ─── call_idempotent（外部写操作落点）───

def _register_counter_tool(name):
    from agent_harness.core.tools.registry import TOOL_REGISTRY, register_tool

    state = {"n": 0}

    def _impl(**kwargs):
        state["n"] += 1
        return {"sent_to": kwargs.get("to"), "counter": state["n"]}

    register_tool(name, _impl, {"type": "object"}, privilege="read-only")
    assert name in TOOL_REGISTRY
    return state


def test_call_idempotent_skips_second_write():
    name = "_idem_send_test"
    state = _register_counter_tool(name)
    store = IdempotencyStore()

    r1 = call_idempotent(name, "mail:order-42", store=store, to="a@b.com")
    assert r1.get("success") and state["n"] == 1

    # 同 key 再发 —— 实际是"重试"，必须跳过工具调用（防发第二封信）
    r2 = call_idempotent(name, "mail:order-42", store=store, to="a@b.com")
    assert state["n"] == 1, "同 idempotency key 重复执行了副作用"
    assert r2.get("idempotent_replay") is True
    assert r2["data"] == r1["data"]


def test_call_idempotent_distinct_keys_execute():
    name = "_idem_send_test2"
    state = _register_counter_tool(name)
    store = IdempotencyStore()
    call_idempotent(name, "mail:1", store=store, to="a")
    call_idempotent(name, "mail:2", store=store, to="b")
    assert state["n"] == 2


def test_call_idempotent_failure_not_marked():
    """失败的写操作不登记 —— 允许重试（未产生确定副作用）。"""
    from agent_harness.core.tools.registry import register_tool

    state = {"n": 0}

    def _boom(**_kwargs):
        state["n"] += 1
        raise RuntimeError("network down")

    register_tool("_idem_fail_test", _boom, {"type": "object"}, privilege="read-only")
    store = IdempotencyStore()

    r1 = call_idempotent("_idem_fail_test", "k1", store=store)
    assert r1.get("success") is False
    assert store.is_executed("k1") is False
    call_idempotent("_idem_fail_test", "k1", store=store)
    assert state["n"] == 2, "失败调用被错误登记 → 丢失了重试机会"


# ─── DecisionExecutor：决策/执行分离 ───

def test_retry_replays_decision_never_reasks_llm():
    """执行重试时**不重问 LLM**：decide_fn 只跑一次，重放持久化决策。"""
    ex = DecisionExecutor()
    decide_calls = {"n": 0}
    seen = []

    def decide_fn():
        decide_calls["n"] += 1
        return f"DECISION-{decide_calls['n']}"

    attempts = {"n": 0}

    def execute_fn(decision):
        attempts["n"] += 1
        seen.append(decision)
        if attempts["n"] < 3:
            raise TimeoutError("响应超时（副作用可能已发生）")
        return "SENT"

    result = ex.run("decide:k", "effect:k", decide_fn, execute_fn, max_attempts=3)
    assert result == "SENT"
    assert decide_calls["n"] == 1, "重试时重问了 LLM（决策被重复推理）"
    assert attempts["n"] == 3
    assert seen == ["DECISION-1", "DECISION-1", "DECISION-1"], "重试未重放同一决策"


def test_effect_idempotent_on_replay():
    """副作用成功后，同 effect_key 再调 → 重放，不再执行。"""
    ex = DecisionExecutor()
    decide_calls = {"n": 0}
    exec_calls = {"n": 0}

    def decide_fn():
        decide_calls["n"] += 1
        return "D"

    def execute_fn(_decision):
        exec_calls["n"] += 1
        return "SENT"

    ex.run("dk", "ek", decide_fn, execute_fn)
    ex.run("dk", "ek", decide_fn, execute_fn)
    assert decide_calls["n"] == 1
    assert exec_calls["n"] == 1


def test_execute_non_retryable_propagates_without_marking():
    ex = DecisionExecutor()

    def execute_fn(_decision):
        raise ValueError("业务错误，不该重试")

    with pytest.raises(ValueError):
        ex.execute("ek", execute_fn, "D", max_attempts=3)
    assert ex.effects.is_executed("ek") is False


def test_decision_persisted_replayed_by_new_executor(tmp_path):
    """决策持久化到文件后，换一个 executor 实例也应重放（不重问）。"""
    dpath = tmp_path / "decisions.json"
    epath = tmp_path / "effects.json"
    decide_calls = {"n": 0}
    exec_calls = {"n": 0}

    def decide_fn():
        decide_calls["n"] += 1
        return "D"

    def execute_fn(_d):
        exec_calls["n"] += 1
        return "SENT"

    ex1 = DecisionExecutor(IdempotencyStore(dpath), IdempotencyStore(epath))
    ex1.run("dk", "ek", decide_fn, execute_fn)

    ex2 = DecisionExecutor(IdempotencyStore(dpath), IdempotencyStore(epath))
    ex2.run("dk", "ek", decide_fn, execute_fn)
    assert decide_calls["n"] == 1
    assert exec_calls["n"] == 1


def test_execute_fails_after_max_attempts():
    ex = DecisionExecutor()

    def execute_fn(_d):
        raise TimeoutError("always")

    with pytest.raises(TimeoutError):
        ex.execute("ek2", execute_fn, "D", max_attempts=2)
    assert ex.effects.is_executed("ek2") is False
