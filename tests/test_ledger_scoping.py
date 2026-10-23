"""P0-1: 进程级 token 台账 vs run 级熔断器 —— 水位线必须锚在 run 起飞点。

回归的故障形态：``pipeline.llm._LLM_TOKEN_LEDGER`` 是进程级累计（所有 run 共享），
而 ``CircuitBreaker`` / ``TokenBudget`` 每个 run 新建。若水位线从 0 起算，首次喂数
会把**历史所有 run 的 token** 算进本次 → 进程累计一旦超过 MAX_TOKENS_PER_TASK，
之后每个新任务开局即硬熔断（进 OPEN 且 _open_hard=True，永久停、不冷却），
外层表现为「空报告」。进程连续使用数小时后必现。

修复方式：创建 run 级 breaker / budget 时把 ``_ledger_total`` 锚在当前全局累计上，
使喂数 delta 恒等于本 run 的真实消耗。

注：这些测试**故意不重置全局台账**（除了 autouse fixture 的前后清理），
因为「历史消耗存在」正是被测场景 —— 原测试靠 _reset_ledger() 规避，所以没抓到。
"""

import pytest

from agent_harness.core.graph import graph_multi as gm
from agent_harness.core.pipeline import llm as llm_mod
from agent_harness.core.pipeline.circuit_breaker import MAX_TOKENS_PER_TASK

PRIOR_RUN_TOKENS = MAX_TOKENS_PER_TASK * 2  # 前序任务烧掉的两倍阈值


@pytest.fixture(autouse=True)
def _clean_ledger():
    """用例前后清理进程台账，避免污染其他测试。"""
    llm_mod._LLM_TOKEN_LEDGER["total"] = 0
    yield
    llm_mod._LLM_TOKEN_LEDGER["total"] = 0


def test_new_breaker_ignores_prior_ledger():
    """前序任务已烧 200k（远超阈值），新 run 的熔断器不得开局即停。"""
    llm_mod._LLM_TOKEN_LEDGER["total"] = PRIOR_RUN_TOKENS

    cb = gm._new_circuit_breaker()
    gm._feed_breaker_tokens(cb)

    assert cb.tokens_used == 0, "历史 run 的 token 不该被算进本次"
    assert cb.check()["tripped"] is False, "新任务不该被历史消耗熔断"


def test_new_breaker_counts_only_own_tokens():
    """锚定之后，本 run 的真实消耗必须被完整、不重复地计入。"""
    llm_mod._LLM_TOKEN_LEDGER["total"] = PRIOR_RUN_TOKENS
    cb = gm._new_circuit_breaker()

    llm_mod.ledger_bump(1_500)
    gm._feed_breaker_tokens(cb)
    assert cb.tokens_used == 1_500

    llm_mod.ledger_bump(500)
    gm._feed_breaker_tokens(cb)
    assert cb.tokens_used == 2_000, "增量必须累加，不能重复计旧值"


def test_new_budget_scoped_too():
    """预算与熔断器共用台账，同一个坑必须一起修。"""
    llm_mod._LLM_TOKEN_LEDGER["total"] = PRIOR_RUN_TOKENS

    budget = gm._new_token_budget()
    level = gm._feed_budget({"token_budget": budget})

    assert budget.used == 0
    assert level.value == "ok", f"历史消耗不该把新会话推进到 {level}"

    llm_mod.ledger_bump(2_000)
    gm._feed_budget({"token_budget": budget})
    assert budget.used == 2_000


def test_breaker_still_trips_within_a_run():
    """作用域修复不得把真正的熔断能力改没了。"""
    cb = gm._new_circuit_breaker()

    llm_mod.ledger_bump(MAX_TOKENS_PER_TASK + 1)
    gm._feed_breaker_tokens(cb)

    assert cb.tokens_used == MAX_TOKENS_PER_TASK + 1
    assert cb.check()["tripped"] is True


def test_two_sequential_runs_do_not_accumulate():
    """连跑三个任务：每个任务各自从 0 起算，第三个任务不受前两个拖累。"""
    for i in range(3):
        cb = gm._new_circuit_breaker()
        llm_mod.ledger_bump(40_000)  # 单次 40k，累加后全局 120k 已超阈值
        gm._feed_breaker_tokens(cb)

        assert cb.tokens_used == 40_000, f"第 {i + 1} 个任务只该看到自己的 40k"
        assert cb.check()["tripped"] is False, f"第 {i + 1} 个任务被前序消耗误熔断"
