"""
Day 6-7 可靠性指标压测 — 熔断触发率 / 降级成功率 / 重试效果

用真实模块（graph_multi 喂熔断 helper + degradation 链 + retry）在受控故障注入下测量：
  - CircuitBreaker: 正常流不误触 / token 超预算必触 / 连续无进展必触（经 _feed_* 真实接线）
  - Degradation: 首选故障时降级链成功率
  - Retry: 网络抖动下重试后的有效成功率

用法（repo 根，项目 venv）:
  ./.venv/Scripts/python scripts/reliability_metrics.py
"""
from __future__ import annotations

import json
import random
import time

from agent_harness.core.pipeline.circuit_breaker import CircuitBreaker
from agent_harness.core.graph import graph_multi as gm
from agent_harness.core.pipeline import llm as llm_mod
from agent_harness.core.degradation import DegradedResult, call_with_degradation
from agent_harness.core.retry import with_retry


def _reset_ledger() -> None:
    llm_mod._LLM_TOKEN_LEDGER["total"] = 0


# ─── 1. CircuitBreaker 触发率（走 graph_multi 真实喂入接线） ───

def run_breaker_scenario(name, per_run_tokens, tokens_per_feed, no_progress_outputs,
                         max_tokens, max_no_progress, n_runs) -> dict:
    """Simulate `n_runs` agent runs; each run feeds `per_run_tokens` in chunks
    + `no_progress_outputs` identical outputs. Return trip rate."""
    tripped = 0
    trip_reasons = []
    for _ in range(n_runs):
        _reset_ledger()
        cb = CircuitBreaker(max_tokens=max_tokens, max_no_progress=max_no_progress)
        # simulate LLM calls bumping the ledger, then graph feeding per round
        fed = 0
        while fed < per_run_tokens:
            chunk = min(tokens_per_feed, per_run_tokens - fed)
            llm_mod.ledger_bump(chunk)
            fed += chunk
            gm._feed_breaker_tokens(cb)
            # simulate a worker round output
            gm._feed_breaker_output(cb, "search", no_progress_outputs)
            if gm._breaker_tripped(cb):
                tripped += 1
                trip_reasons.extend(cb.check().get("reasons", []))
                break
    return {
        "scenario": name, "runs": n_runs, "tripped": tripped,
        "trip_rate_pct": round(100.0 * tripped / n_runs, 1),
        "sample_reasons": list(dict.fromkeys(trip_reasons))[:3],
    }


# ─── 2. Degradation 降级成功率 ───

def run_degradation_scenario(name, fail_rate, n=100) -> dict:
    ok = 0
    used_fallback = 0
    for _ in range(n):
        def preferred(**kw):
            if random.random() < fail_rate:
                raise ConnectionError("primary down")
            return "primary_result"

        def fallback(**kw):
            return "fallback_result"

        r = call_with_degradation(name, preferred_fn=preferred, fallback_fns=[fallback],
                                  on_failure=lambda **kw: DegradedResult(name, "all_down"))
        if r == "fallback_result":
            used_fallback += 1
            ok += 1
        elif r == "primary_result":
            ok += 1
        # DegradedResult == False → failure
    return {
        "scenario": f"degradation_{name}", "trials": n,
        "primary_fail_rate_pct": fail_rate * 100,
        "success_rate_pct": round(100.0 * ok / n, 1),
        "fallback_used_pct": round(100.0 * used_fallback / n, 1),
    }


# ─── 3. Retry 重试效果 ───

def run_retry_scenario(fail_rate, n=200) -> dict:
    @with_retry(max_attempts=3, base_delay=0.01, max_delay=0.1)
    def flaky_call():
        if random.random() < fail_rate:
            raise ConnectionError("flaky")
        return "ok"

    ok = 0
    for _ in range(n):
        try:
            r = flaky_call()
            if r == "ok":
                ok += 1
        except ConnectionError:
            pass
    return {
        "scenario": f"retry_fail_{fail_rate}",
        "trials": n, "effective_success_rate_pct": round(100.0 * ok / n, 1),
    }


def main() -> None:
    random.seed(20260904)
    results = []

    t0 = time.time()
    # 1a. 正常流（token 够、输出各异）→ 不应误触（触发率 0%）
    tripped = 0
    for i in range(100):
        _reset_ledger()
        cb = CircuitBreaker(max_tokens=100_000, max_no_progress=5)
        fed = 0
        while fed < 5000:
            llm_mod.ledger_bump(500)
            fed += 500
            gm._feed_breaker_tokens(cb)
            gm._feed_breaker_output(cb, "search", f"结果-{i}-{fed}")  # 输出各异
            if gm._breaker_tripped(cb):
                tripped += 1
                break
    results.append({"scenario": "normal_flow_no_false_trip", "runs": 100,
                    "tripped": tripped, "trip_rate_pct": tripped,
                    "note": "每轮输出各异 → 应 0% 误触"})

    # 1b. token 超预算 → 100% 触发
    results.append(run_breaker_scenario(
        "token_over_budget_must_trip", per_run_tokens=200_000, tokens_per_feed=10_000,
        no_progress_outputs="out", max_tokens=100_000, max_no_progress=5, n_runs=50))

    # 1c. 连续无进展（同一输出）→ 100% 触发
    results.append(run_breaker_scenario(
        "no_progress_must_trip", per_run_tokens=1_000, tokens_per_feed=100,
        no_progress_outputs="SAME", max_tokens=100_000, max_no_progress=5, n_runs=50))

    # 2. degradation
    results.append(run_degradation_scenario("primary_down", fail_rate=1.0, n=100))
    results.append(run_degradation_scenario("flaky_30", fail_rate=0.3, n=100))

    # 3. retry
    results.append(run_retry_scenario(0.2, n=200))
    results.append(run_retry_scenario(0.5, n=200))

    report = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_s": round(time.time() - t0, 1),
        "results": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    main()
