"""P0-5 工具返回值语义校验（anomaly gate）—— 测试。

验证：
  * **默认零规则**：不注册校验器时 validate_result 与旧行为一致（不改变现有回路）；
  * 注册语义校验器后，**HTTP 200 但语义已坏**的返回值（负库存/越界/类型/单调性）
    被判 error/warning，**不静默下发**；
  * 校验器可挂可摘、可全局（"*"）、不适用即跳过（不误杀）；
  * 校验器自身抛异常 → fail-closed（判异常，不静默放过）。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core.tools.registry import validate_result  # noqa: E402
from agent_harness.core.tools.semantic import (  # noqa: E402
    ANOMALY_PREFIX,
    clear_semantic_validators,
    matches_type,
    monotonic,
    non_negative,
    numeric_range,
    one_of,
    register_semantic_validator,
    run_semantic_validators,
    unregister_semantic_validator,
)


def _ok(data):
    return {"success": True, "error": None, "data": data}


@pytest.fixture(autouse=True)
def _clean_validators():
    """每个用例前后清空规则，保证测试互不污染（也证明默认态）。"""
    clear_semantic_validators()
    yield
    clear_semantic_validators()


# ─── 默认零规则：不改变现有行为 ───

def test_no_rules_is_noop_ok():
    """没注册任何规则时，正常结果照旧 passed（默认态不影响现有回路）。"""
    r = validate_result("inventory", _ok("库存 5 件"))
    assert r["passed"] and r["severity"] == "ok"
    assert run_semantic_validators("inventory", _ok(123)) is None


def test_no_rules_preserves_basic_verdicts():
    """默认态下基础判据逐条保持（降级词 → error；空 → warning）。"""
    assert validate_result("search", _ok("后端不可用，搜索结果不可用"))["severity"] == "error"
    assert validate_result("t", _ok(None))["severity"] == "warning"
    assert validate_result("t", _ok([]))["severity"] == "warning"


def test_negative_inventory_would_be_ok_without_rules():
    """对照：负库存在**无规则**时会被判 ok —— 正是要堵的雪崩源头。"""
    assert validate_result("inventory", _ok(-3))["passed"] is True


# ─── 负值 / 范围 ───

def test_non_negative_flags_negative():
    register_semantic_validator("inventory", non_negative())
    r = validate_result("inventory", _ok(-3))
    assert r["passed"] is False
    assert r["severity"] == "error"
    assert r.get("anomaly") is True
    assert r["reason"].startswith(ANOMALY_PREFIX)


def test_non_negative_allows_zero_and_positive():
    register_semantic_validator("inventory", non_negative())
    assert validate_result("inventory", _ok(0))["passed"] is True
    assert validate_result("inventory", _ok([0, 1, 2]))["passed"] is True


def test_non_negative_flags_in_list():
    register_semantic_validator("inventory", non_negative())
    r = validate_result("inventory", _ok([10, -1, 4]))
    assert r["passed"] is False and r["anomaly"] is True


def test_numeric_range_flags_out_of_range():
    register_semantic_validator("price", numeric_range(lo=0, hi=100))
    assert validate_result("price", _ok(101))["passed"] is False
    assert validate_result("price", _ok(-0.5))["passed"] is False
    assert validate_result("price", _ok(50))["passed"] is True


# ─── 不适用即跳过（防误杀）───

def test_non_applicable_data_is_skipped():
    """数值校验器不评判字符串/异构数据 —— 不适用即跳过，绝不误杀。"""
    register_semantic_validator("misc", non_negative())
    assert validate_result("misc", _ok("一切正常的一段文本"))["passed"] is True
    assert validate_result("misc", _ok([1, "x", 2]))["passed"] is True


# ─── 类型 / 白名单 ───

def test_matches_type():
    register_semantic_validator("rows", matches_type(list))
    assert validate_result("rows", _ok({"a": 1}))["passed"] is False
    assert validate_result("rows", _ok([1, 2]))["passed"] is True


def test_one_of_rejects_stale_value():
    """枚举/状态契约：陈旧缓存串出的非法状态码应被拦。"""
    register_semantic_validator("order_status", one_of({"paid", "shipped", "done"}))
    assert validate_result("order_status", _ok("refunded_old"))["passed"] is False
    assert validate_result("order_status", _ok("shipped"))["passed"] is True


# ─── 单调性 ───

def test_monotonic_flags_regression():
    register_semantic_validator("cumulative", monotonic("nondecreasing"))
    assert validate_result("cumulative", _ok([1, 2, 3, 2]))["passed"] is False
    assert validate_result("cumulative", _ok([1, 2, 2, 3]))["passed"] is True


def test_monotonic_skips_short_sequence():
    register_semantic_validator("ts", monotonic("nondecreasing"))
    assert validate_result("ts", _ok([5]))["passed"] is True


# ─── 全局 "/*" 规则 + 注册表管理 ───

def test_wildcard_rule_applies_to_all_tools():
    register_semantic_validator("*", non_negative())
    assert validate_result("anything_a", _ok(-1))["passed"] is False
    assert validate_result("anything_b", _ok([-1]))["passed"] is False


def test_unregister_and_clear():
    register_semantic_validator("t", non_negative())
    assert validate_result("t", _ok(-1))["passed"] is False
    unregister_semantic_validator("t")
    assert validate_result("t", _ok(-1))["passed"] is True

    register_semantic_validator("t2", non_negative())
    clear_semantic_validators()
    assert validate_result("t2", _ok(-1))["passed"] is True


def test_replace_rule_set():
    register_semantic_validator("t", non_negative())
    register_semantic_validator("t", numeric_range(lo=0, hi=10), replace=True)
    r = validate_result("t", _ok(99))
    assert r["passed"] is False
    assert "范围" in r["reason"]


# ─── 自定义校验器 + fail-closed ───

def test_custom_validator_returning_str():
    register_semantic_validator("t", lambda res: "库存为负" if res["data"] < 0 else None)
    assert validate_result("t", _ok(-1))["passed"] is False
    assert validate_result("t", _ok(1))["passed"] is True


def test_validator_exception_is_fail_closed():
    """校验器自身炸了 → 判异常（fail-closed），不静默放过。"""
    def _boom(_res):
        raise RuntimeError("校验器内部错误")

    register_semantic_validator("t", _boom)
    r = validate_result("t", _ok(1))
    assert r["passed"] is False
    assert r["severity"] == "error"
    assert r["anomaly"] is True
    assert "fail-closed" in r["reason"]


def test_semantic_gate_only_runs_after_basic_pass():
    """基础判据未过时不应再跑语义校验（顺序：结构 → 语义）。"""
    called = {"n": 0}

    def _spy(_res):
        called["n"] += 1
        return None

    register_semantic_validator("t", _spy)
    # data 为空 → 基础判 warning，短路，不应触发语义校验器
    assert validate_result("t", _ok(None))["severity"] == "warning"
    assert called["n"] == 0
