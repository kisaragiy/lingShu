"""P1-7：终止条件可组合（对齐 AutoGen TerminationCondition 的最小实现）。

原实现把 token / 墙钟 / 无进展三种判据写死在 CircuitBreaker 里、route 里是一串
if；这里验证「条件可单独测、可组合、接入后行为等价」。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core.pipeline.termination import (  # noqa: E402
    AllOf,
    AnyOf,
    CallbackCondition,
    StateFlag,
    StopReason,
    TokenLimit,
    WallTime,
)
from agent_harness.core.graph import graph_multi as gm  # noqa: E402


# ─── 单条件 ───

def test_token_limit_hits_only_over_threshold():
    assert TokenLimit(lambda: 50, 100)({}) is None
    reason = TokenLimit(lambda: 100, 100)({})
    assert reason is not None and reason.name == "token_limit" and reason.hard is True


def test_wall_time_hits_only_over_threshold():
    assert WallTime(lambda: 1.0, 60)({}) is None
    assert WallTime(lambda: 60, 60)({}).name == "wall_time"


def test_state_flag_truthy_and_equals():
    assert StateFlag("no_progress_tripped")({"no_progress_tripped": False}) is None
    assert StateFlag("no_progress_tripped")({"no_progress_tripped": True}).name == "no_progress_tripped"
    assert StateFlag("feasibility", equals="infeasible")({"feasibility": "feasible"}) is None
    assert StateFlag("feasibility", equals="infeasible")({"feasibility": "infeasible"}) is not None


def test_callback_condition_accepts_stop_reason_or_bool():
    assert CallbackCondition("x", lambda s: False, "不会命中")({}) is None
    assert CallbackCondition("x", lambda s: True, "布尔命中")({}).message == "布尔命中"
    explicit = StopReason("x", "自定义停因")
    assert CallbackCondition("x", lambda s: explicit, "ignored")({}) is explicit


# ─── 组合器 ───

def test_any_of_short_circuits_in_priority_order():
    order = []

    def probe(name, hit):
        def _p(_state):
            order.append(name)
            return hit
        return CallbackCondition(name, _p, f"{name} 命中")

    combo = AnyOf(probe("first", False), probe("second", True), probe("third", True))
    reason = combo({})
    assert reason is not None and reason.name == "second"
    assert order == ["first", "second"], "AnyOf 没有短路：后面的条件不该被求值"


def test_any_of_none_hit_returns_none():
    combo = AnyOf(TokenLimit(lambda: 0, 100), WallTime(lambda: 0.0, 10))
    assert combo({}) is None


def test_all_of_requires_every_condition():
    token = TokenLimit(lambda: 200, 100)      # 命中
    time_ok = WallTime(lambda: 1.0, 10)       # 未命中
    assert AllOf(token, time_ok)({}) is None, "AllOf 在未全命中时不应停"
    assert AllOf(token, WallTime(lambda: 20.0, 10))({}).name == "wall_time"


def test_composed_reason_carries_name_and_hardness():
    combo = AnyOf(TokenLimit(lambda: 999, 100), StateFlag("no_progress_tripped"))
    reason = combo({})
    assert reason.hard is True
    combo2 = AnyOf(StateFlag("no_progress_tripped"))
    assert combo2({"no_progress_tripped": True}).hard is False


# ─── 真接线：route 行为等价 ───

def _route(**state):
    return gm.supervisor_route({"round": 0, "all_done": False, **state})


def test_route_finalizes_on_no_progress():
    assert _route(no_progress_tripped=True) == "finalize"


def test_route_finalizes_on_infeasible():
    assert _route(feasibility="infeasible") == "finalize"


def test_route_finalizes_on_max_rounds():
    assert gm.supervisor_route({"round": 999, "all_done": False}) == "finalize"


def test_route_replans_when_nothing_hit():
    assert _route() == "replan"


def test_route_finalizes_on_tripped_breaker():
    from agent_harness.core.pipeline.circuit_breaker import CircuitBreaker

    cb = CircuitBreaker()
    cb.add_tokens(10 ** 6)  # 直接撞 token 硬阈值
    cb.check()
    assert _route(circuit_breaker=cb) == "finalize"


def test_conditions_tuple_covers_four_yaml_known_causes():
    names = [c.name for c in gm.termination_conditions().conditions]
    assert names == ["circuit_breaker", "token_budget", "no_progress", "infeasible"]
