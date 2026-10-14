"""LWP18 — core 模块评测单测。

对标 MESH-Harness (arXiv:2610.05300)：每个功能模块有**显式角色专属接口**，同角色的
不同实现可被**替换**。本测试验证灵枢 ``core/module_eval.py`` 的判据（PLAN LWP18 验收）：

  ① **接口成套**：四个角色都登记了行为探针；适配器满足对应 Protocol（可替换）。
  ② **真实现全过**：灵枢真实四模块 → 全部探针通过（score == 1.0）。
  ③ **差分判据（核心验收）**：喂「好实现」与「坏实现」，评测**能区分好坏**。
  ④ **探针是行为判据**：坏实现被评出**具体**失败探针（不是身份判断）。
  ⑤ **确定性**：同一输入 → 同一报告；``to_dict`` 可 JSON 序列化。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core import module_eval as me  # noqa: E402


# ── 坏实现（deliberately-wrong，用来验证评测能区分）───────────────────────


class BadRetry:
    """无重试：原样返回函数（瞬态失败不恢复）。"""

    def wrap(self, fn, *, max_attempts: int, base_delay: float = 0.0):
        return fn


class BadDegradation:
    """不降级：永远返回 None。"""

    def run(self, capability, *, preferred_fn=None, fallback_fns=None,
            on_failure=None, **kwargs):
        return None


class BadBudget:
    """不升级：永远 ok、永远可继续。"""

    def new_budget(self, soft_limit: int):
        return {"soft_limit": soft_limit}

    def add(self, budget, tokens: int) -> str:
        return "ok"

    def can_continue(self, budget) -> bool:
        return True


class BadCircuitBreaker:
    """不熔断：永远 tripped=False。"""

    def new_breaker(self, max_tokens: int = 100_000, max_no_progress: int = 5):
        return object()

    def add_tokens(self, breaker, n: int) -> None:
        pass

    def record_output(self, breaker, output: str) -> None:
        pass

    def tripped(self, breaker) -> bool:
        return False


class BadBundle:
    retry = BadRetry()
    degradation = BadDegradation()
    budget = BadBudget()
    circuit_breaker = BadCircuitBreaker()


# ── ① 接口成套 ────────────────────────────────────────────────

def test_registry_covers_four_roles():
    assert set(me.PROBE_REGISTRY) == {"retry", "degradation", "budget", "circuit_breaker"}


def test_real_adapters_satisfy_role_protocols():
    """角色专属接口是显式的：真实适配器必须结构上满足对应 Protocol。"""
    mods = me.LingShuModules()
    assert isinstance(mods.retry, me.RetryModule)
    assert isinstance(mods.degradation, me.DegradationModule)
    assert isinstance(mods.budget, me.BudgetModule)
    assert isinstance(mods.circuit_breaker, me.CircuitBreakerModule)


# ── ② 真实现全过 ──────────────────────────────────────────────

def test_real_modules_pass_all_probes():
    report = me.evaluate_all(me.LingShuModules())
    assert report.passed is True, report.to_dict()
    assert report.score == 1.0
    for m in report.modules:
        assert m.passed, (m.module, m.failed_probes)


def test_each_real_module_scores_one():
    mods = me.LingShuModules()
    for name in me.PROBE_REGISTRY:
        rep = me.evaluate_module(name, getattr(mods, name))
        assert rep.score == 1.0, (name, rep.failed_probes)


# ── ③ 差分判据（核心验收）────────────────────────────────────

def test_eval_distinguishes_good_from_bad():
    """同一套评测：好实现全过、坏实现未全过 → 能区分。"""
    good = me.evaluate_all(me.LingShuModules())
    bad = me.evaluate_all(BadBundle())
    assert me.distinguishes(good, bad) is True
    assert bad.passed is False
    assert bad.score < good.score


# ── ④ 探针是行为判据：失败定位到具体探针 ──────────────────────

def test_bad_retry_fails_transient_and_exhaustion_but_passes_passthrough():
    rep = me.evaluate_module("retry", BadRetry())
    fails = set(rep.failed_probes)
    assert "retry.transient_recovery" in fails
    assert "retry.exhaustion_reraises" in fails
    # 不可重试直通在「无重试」下依然是 1 次 → 通过（说明不是身份判断）
    assert "retry.non_retryable_passthrough" not in fails
    assert 0 < rep.score < 1


def test_bad_degradation_fails_preferred_fallback_and_exhausted():
    rep = me.evaluate_module("degradation", BadDegradation())
    fails = set(rep.failed_probes)
    assert {"degradation.preferred_wins",
            "degradation.fallback_recovers",
            "degradation.exhausted_is_degraded_result",
            "degradation.on_failure_used"} <= fails
    assert rep.score == 0.0


def test_bad_budget_fails_on_escalation_only():
    rep = me.evaluate_module("budget", BadBudget())
    fails = set(rep.failed_probes)
    assert "budget.ok_below_warn" not in fails          # 永远 ok → 这条过得去
    assert {"budget.warn_on_threshold",
            "budget.ask_at_soft_limit",
            "budget.stop_is_terminal"} <= fails


def test_bad_circuit_breaker_fails_on_trip_only():
    rep = me.evaluate_module("circuit_breaker", BadCircuitBreaker())
    fails = set(rep.failed_probes)
    assert "circuit_breaker.healthy_no_trip" not in fails
    assert {"circuit_breaker.no_progress_trips",
            "circuit_breaker.token_overrun_trips"} <= fails


# ── ⑤ 确定性 + 可序列化 ───────────────────────────────────────

def test_report_is_deterministic_and_json_serialisable():
    a = me.evaluate_all(me.LingShuModules()).to_dict()
    b = me.evaluate_all(me.LingShuModules()).to_dict()
    assert json.dumps(a, sort_keys=True, ensure_ascii=False) == \
           json.dumps(b, sort_keys=True, ensure_ascii=False)
    parsed = json.loads(json.dumps(a))
    assert parsed["passed"] is True
    assert len(parsed["modules"]) == 4


def test_unknown_module_raises():
    import pytest
    with pytest.raises(KeyError):
        me.evaluate_module("does_not_exist", me.LingShuModules())
