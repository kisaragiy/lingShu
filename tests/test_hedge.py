"""provider 对冲 / 降级（P2-4 ②）—— failover + 对冲决策 + 对冲执行，全离线确定性。

判据（对冲三问）：
  ① 主挂**自动 failover**（异常 → 顺次切下一个，全挂留痕不抛）；
  ② 过了 P95 **才**发第二副本（预算内完成绝不浪费一次调用）；
  ③ 全流程走可注入 Executor → 确定性、不触网、不靠 sleep 竞速。
"""
import concurrent.futures as cf
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core import hedge  # noqa: E402


# ── failover（顺序降级）───────────────────────────────────────

def test_failover_primary_ok():
    r = hedge.failover([("primary", lambda: "p"), ("b", lambda: "b")])
    assert r.ok and r.value == "p" and r.provider == "primary" and r.attempts == 1


def test_failover_switches_on_exception():
    def boom():
        raise ConnectionError("primary down")

    r = hedge.failover([("primary", boom), ("secondary", lambda: "s")])
    assert r.ok and r.value == "s" and r.provider == "secondary" and r.attempts == 2
    assert any("primary" in e for e in r.errors)


def test_failover_all_fail():
    def boom():
        raise RuntimeError("x")

    r = hedge.failover([("a", boom), ("b", boom)])
    assert r.ok is False and r.error == "all_providers_failed" and r.attempts == 2


def test_failover_on_all_failed():
    def boom():
        raise RuntimeError("x")

    r = hedge.failover([("a", boom)], on_all_failed=lambda: "cached")
    assert r.ok and r.value == "cached"


def test_failover_is_ok_predicate():
    r = hedge.failover([("a", lambda: None), ("b", lambda: "ok")])
    assert r.provider == "b"           # None 视为失败
    r2 = hedge.failover([("a", lambda: None)], is_ok=lambda v: True)
    assert r2.provider == "a" and r2.ok


# ── 对冲决策（纯函数）─────────────────────────────────────────

def test_should_hedge():
    assert hedge.should_hedge(1.0, 1.0) is True
    assert hedge.should_hedge(1.0, 1.5) is True
    assert hedge.should_hedge(1.0, 0.5) is False
    assert hedge.should_hedge(1.0, 1.5, factor=2.0) is False


# ── 对冲执行 ──────────────────────────────────────────────────

def _exec(futures):
    return hedge.FakeExecutor(futures)


def test_hedged_call_in_budget_uses_primary_only():
    ex = _exec([hedge._completed("p")])
    r = hedge.hedged_call(lambda: "p", lambda: "s", p95_s=1.0, executor=ex)
    assert r.ok and r.value == "p" and r.provider == "primary"
    assert r.hedged is False and r.attempts == 1
    assert len(ex.submitted) == 1          # 预算内完成 → 不发第二副本


def test_hedged_call_over_budget_fires_hedge_copy():
    pending: cf.Future = cf.Future()        # primary 迟迟不完成
    ex = _exec([pending, hedge._completed("s")])
    r = hedge.hedged_call(lambda: "p", lambda: "s", p95_s=0.02, executor=ex)
    assert r.ok and r.value == "s" and r.provider == "hedge"
    assert r.hedged is True and r.attempts == 2
    assert len(ex.submitted) == 2           # 超 P95 → 发了第二副本


def test_hedged_call_primary_error_failovers_not_hedge():
    ex = _exec([hedge._errored(ConnectionError("down")), hedge._completed("s")])
    r = hedge.hedged_call(lambda: "p", lambda: "s", p95_s=1.0, executor=ex)
    assert r.ok and r.value == "s" and r.provider == "hedge"
    assert r.hedged is False and r.attempts == 2
    assert any("primary" in e for e in r.errors)


def test_hedged_call_both_fail():
    ex = _exec([hedge._errored(ConnectionError("p")), hedge._errored(ConnectionError("s"))])
    r = hedge.hedged_call(lambda: "p", lambda: "s", p95_s=1.0, executor=ex)
    assert r.ok is False and r.error == "failover_exhausted" and r.attempts == 2


def test_hedged_call_real_thread_executor():
    # 走默认 _ThreadExecutor（真线程池）：primary 秒回 → 不回退、不对冲
    r = hedge.hedged_call(lambda: "p", lambda: "s", p95_s=1.0)
    assert r.ok and r.value == "p" and r.provider == "primary" and r.hedged is False


def test_provider_result_to_dict():
    d = hedge.ProviderResult(ok=True, value=1, provider="p", attempts=1).to_dict()
    assert d["ok"] is True and d["provider"] == "p" and "errors" in d
