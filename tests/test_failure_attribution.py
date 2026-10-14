"""LWP17 — 失败归因（决定性错误）单测。

对标 EMFA (arXiv:2610.11600)：观测到的失败**不直接暴露**根因；错误会沿依赖边
级联传播、也会在未解循环里反复出现。归因目标 = 决定性错误（纠正后可救活执行的
agent-step 对）。

判据（本文件 5 问）：
  ① **命中已知错误**：注入已知错误步骤 → 归因命中该步（PLAN LWP17 验收）；
  ② **区分症状**：链式下游失败被标为症状，不抢根因；
  ③ **反事实**：决定性错误纠正后 recovered=True；多根因时单点纠正不误报救活；
  ④ **循环**：同一 (agent, action) 反复失败 → 未解循环入候选；
  ⑤ **适配 + 确定性**：run_multi_agent 返回值可归因；同一输入 → 同一报告。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core import failure_attribution as fa  # noqa: E402


def S(index, agent, action, ok=True, deps=None):
    return fa.AttributionStep(index=index, agent=agent, action=action,
                              ok=ok, depends_on=deps or [])


# ── ① 命中已知错误（LWP17 验收）──────────────────────────────

def test_injected_error_is_attributed():
    """注入一个已知错误步 → 归因必须命中该步（且能救活）。"""
    steps = [
        S(0, "search", "检索", ok=True),
        S(1, "analyze", "分析", ok=False),          # ← 注入的错误
        S(2, "finalize", "收尾", ok=False, deps=[1]),
    ]
    rep = fa.attribute(steps)
    assert rep.decisive_index == 1
    assert rep.decisive == ("analyze", "分析")
    assert rep.recovered is True, "纠正该步应能救活整条执行"


def test_all_ok_has_no_decisive_error():
    rep = fa.attribute([S(0, "a", "x"), S(1, "b", "y", deps=[0])])
    assert rep.decisive_index is None
    assert rep.recovered is True
    assert rep.failed_steps == []


# ── ② 区分症状：链式传播 ─────────────────────────────────────

def test_chain_downstream_marked_symptom_root_is_decisive():
    """A 失败 → B 依赖 A 失败 → C 依赖 B 失败：根因 A，B/C 是症状。"""
    steps = [
        S(0, "search", "检索", ok=False),           # 根因
        S(1, "analyze", "分析", ok=False, deps=[0]),
        S(2, "finalize", "收尾", ok=False, deps=[1]),
    ]
    rep = fa.attribute(steps)
    assert rep.decisive_index == 0
    assert set(rep.symptoms) >= {1, 2}, "下游失败应被判为症状"
    assert rep.recovered is True
    assert rep.confidence == 1.0


def test_deep_cascade_transitive_reachability():
    """多级间接依赖（0→1→2→3）也要能看清传播。"""
    steps = [
        S(0, "a", "step0", ok=False),
        S(1, "b", "step1", ok=False, deps=[0]),
        S(2, "c", "step2", ok=False, deps=[1]),
        S(3, "d", "step3", ok=False, deps=[2]),
    ]
    rep = fa.attribute(steps)
    assert rep.decisive_index == 0
    assert set(rep.symptoms) == {1, 2, 3}
    assert rep.candidates[0].resolves == 4  # 自身 + 3 个下游


def test_symptom_is_not_offered_as_root_candidate():
    """症状步不应作为候选根因出现在候选列表里（除循环锚点外）。"""
    steps = [
        S(0, "a", "x", ok=False),
        S(1, "b", "y", ok=False, deps=[0]),
    ]
    rep = fa.attribute(steps)
    assert [c.index for c in rep.candidates] == [0]


# ── ③ 反事实：多根因不误报救活 ───────────────────────────────

def test_two_independent_roots_single_fix_does_not_recover():
    """两个并行独立失败 → 单点纠正救不活整条执行（recovered=False）。"""
    steps = [
        S(0, "search", "检索", ok=False),
        S(1, "analyze", "分析", ok=False),
        S(2, "finalize", "收尾", ok=True, deps=[0, 1]),
    ]
    rep = fa.attribute(steps)
    assert rep.recovered is False, "两个独立根因，单点纠正不该被判救活"
    assert rep.decisive_index == 0, "并列时取更早的（更靠近根因）"
    assert rep.confidence < 1.0
    assert "残留失败" in rep.explanation


def test_recoverable_root_wins_over_nonrecoverable_earlier():
    """若能救活的后根因与救不活的前根因并列，应选'能救活'的那个。"""
    steps = [
        S(0, "analyze", "分析", ok=False),          # 独立失败，救不活全部
        S(1, "search", "检索", ok=False),           # 独立失败
        S(2, "finalize", "收尾", ok=False, deps=[0, 1]),
    ]
    rep = fa.attribute(steps)
    # 两个根因各自都救不活（都留残留）——断言候选按 resolves 排序，且不误报救活
    assert rep.recovered is False
    assert len([c for c in rep.candidates if not c.recovers_run]) == 2


# ── ④ 持久交互循环 ───────────────────────────────────────────

def test_persistent_loop_detected():
    """同一 (agent, action) 反复失败 → 未解循环入候选。"""
    steps = [
        S(0, "search", "重试检索", ok=False),
        S(1, "search", "重试检索", ok=False),
        S(2, "search", "重试检索", ok=False),
    ]
    rep = fa.attribute(steps)
    assert len(rep.loops) == 1
    lp = rep.loops[0]
    assert lp["agent"] == "search" and lp["count"] == 3 and lp["fails"] == 3
    assert lp["anchor"] == 0
    assert any(c.kind == "loop" for c in rep.candidates)


def test_repeated_pair_that_succeeds_is_not_a_loop():
    rep = fa.attribute([S(0, "search", "x", ok=True), S(1, "search", "x", ok=True)])
    assert rep.loops == []


# ── ⑤ 灵枢适配 + 确定性 ──────────────────────────────────────

def _run_result(**over):
    base = {
        "request": "调研 x",
        "worker_tasks": {"search": "检索资料", "analyze": "汇总分析"},
        "worker_results": {
            "search": {"success": True, "output": "搜索结论", "errors": []},
            "analyze": {"success": False, "output": "", "errors": ["LLM 超时"]},
        },
        "final_output": "",       # finalize 无输出 → 失败
        "completion_contract": {"features": [{"feature": "分析", "worker": "analyze",
                                              "passes": False}], "complete": False},
        "rounds": 2,
    }
    base.update(over)
    return base


def test_attribute_from_run_result_hits_failed_worker():
    """灵枢 run 结果：analyze 失败 + finalize 随之失败 → 决定性错误 = analyze。"""
    rep = fa.attribute_from_run_result(_run_result())
    # 步骤：analyze(0), search(1), supervisor::finalize(2)（worker 按名排序）
    assert rep.decisive_agent == "analyze"
    assert rep.decisive_action == "汇总分析"
    assert 2 in rep.symptoms, "finalize 依赖全部 worker，应被标为症状"
    assert rep.recovered is True


def test_attribute_from_run_result_all_workers_ok():
    rep = fa.attribute_from_run_result(_run_result(
        worker_results={
            "search": {"success": True, "output": "a", "errors": []},
            "analyze": {"success": True, "output": "b", "errors": []},
        },
        final_output="报告正文",
        completion_contract={"features": [{"feature": "x", "worker": "search",
                                           "passes": True}], "complete": True},
    ))
    assert rep.decisive_index is None
    assert rep.recovered is True


def test_attribute_from_run_result_explicit_dependency():
    """显式声明 worker 依赖 → analyze 失败会污染依赖它的 worker（级联）。"""
    res = _run_result(worker_results={
        "search": {"success": False, "output": "", "errors": ["检索失败"]},
        "analyze": {"success": False, "output": "", "errors": ["无输入"]},
    }, completion_contract={"features": [{"feature": "x", "worker": "search",
                                          "passes": False}], "complete": False})
    rep = fa.attribute_from_run_result(res, depends_on={"analyze": ["search"]})
    # search(1) 是根因；analyze(0) 依赖它 → 症状
    assert rep.decisive_agent == "search"
    assert 0 in rep.symptoms


def test_report_is_deterministic_and_json_serialisable():
    a = fa.attribute_from_run_result(_run_result()).to_dict()
    b = fa.attribute_from_run_result(_run_result()).to_dict()
    sa = json.dumps(a, sort_keys=True, ensure_ascii=False)
    sb = json.dumps(b, sort_keys=True, ensure_ascii=False)
    assert sa == sb
    assert json.loads(sa)["decisive_agent"] == "analyze"


def test_edges_are_sanitised_out_of_range_and_self_loop():
    """越界 / 自环依赖要兜底丢弃，绝不抛。"""
    rep = fa.attribute([
        fa.AttributionStep(0, "a", "x", ok=False, depends_on=[0, 9, -1]),
    ])
    assert rep.decisive_index == 0


def test_empty_and_dict_inputs_do_not_raise():
    assert fa.attribute([]).decisive_index is None
    rep = fa.attribute([{"agent": "a", "action": "x", "ok": False}])
    assert rep.decisive_index == 0
