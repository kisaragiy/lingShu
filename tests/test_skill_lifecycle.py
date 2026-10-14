"""LWP19 — 技能生命周期单测。

对标 SkillForge (arXiv:2610.09832)：skill 库是**动态种群**，每条 skill 带 fitness 沿
``trial → active → stable → retired`` 迁移；retired **不再进入检索**。本测试验证灵枢
``core/skill_lifecycle.py``（PLAN LWP19 验收）：

  ① **四态 + 加载语义**：只有 retired 不可加载；retired 是终态。
  ② **fitness（Eq. 6）**：使用量不足取中性先验；足够时取 C/U。
  ③ **迁移规则（Eq. 9）**：trial→active、active→stable、active→retired 各自命中。
  ④ **迟滞（δ_retire<δ_demote<δ_stable）**：stable 先降级回 active，不直接退役。
  ⑤ **代际保护（Eq. 8）**：代际 0 用更高的 n_protect 门槛。
  ⑥ **验收**：低质量 skill 被标记 retired **且不再加载**（PLAN LWP19）。
  ⑦ **复用现有元数据**：SKILL.md frontmatter → 记录；缺字段兜底。
  ⑧ **建议不自动淘汰**：只产候选清单、状态不变；mutation 带标记。
  ⑨ **确定性 + 可序列化**；策略阈值校验。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core import skill_lifecycle as sl  # noqa: E402

P = sl.LifecyclePolicy
S = sl.SkillState


def rec(name, usage=0, success=0, generation=0, state=S.TRIAL):
    return sl.SkillRecord(name, usage=usage, success=success,
                          generation=generation, state=state)


# ── ① 四态 + 加载语义 ────────────────────────────────────────

def test_four_states_and_only_retired_unloadable():
    assert set(S) == {S.TRIAL, S.ACTIVE, S.STABLE, S.RETIRED}
    assert S.TRIAL.loadable and S.ACTIVE.loadable and S.STABLE.loadable
    assert S.RETIRED.loadable is False


def test_retired_is_terminal():
    r = rec("x", usage=100, success=0, state=S.RETIRED)
    assert sl.next_state(r) is S.RETIRED


# ── ② fitness（Eq. 6）────────────────────────────────────────

def test_fitness_uses_neutral_prior_below_warm():
    p = P(n_warm=3)
    # 使用量 < n_warm → 不轻信经验值，取中性先验（一次失败不判死）
    fresh = rec("a", usage=1, success=0)
    assert sl.fitness(fresh, p) == p.f_default


def test_fitness_is_success_rate_at_or_above_warm():
    p = P(n_warm=3)
    assert sl.fitness(rec("a", usage=10, success=7), p) == 0.7
    assert sl.fitness(rec("a", usage=4, success=1), p) == 0.25


def test_record_episode_counts_usage_and_success():
    r = rec("a", usage=0, success=0)
    sl.record_episode(r, success=True)
    sl.record_episode(r, success=False)
    assert (r.usage, r.success) == (2, 1)


# ── ③ 迁移规则（Eq. 9）───────────────────────────────────────

def test_trial_promotes_to_active_on_min_usage():
    p = P(n_promote=1)
    assert sl.next_state(rec("a", usage=0), p) is S.TRIAL
    assert sl.next_state(rec("a", usage=1), p) is S.ACTIVE


def test_active_promotes_to_stable_on_high_fitness_and_usage():
    p = P(delta_stable=0.7, n_stable=5)
    good = rec("a", usage=5, success=4, state=S.ACTIVE)   # f=0.8 ≥ 0.7, U≥5
    assert sl.next_state(good, p) is S.STABLE
    # 使用量不足 → 即使 fitness 高也停在 active
    not_enough = rec("a", usage=2, success=2, state=S.ACTIVE)
    assert sl.next_state(not_enough, p) is S.ACTIVE


def test_active_retires_on_low_fitness_and_enough_usage():
    p = P(delta_retire=0.3, n_retire=5)
    bad = rec("a", usage=5, success=1, generation=1, state=S.ACTIVE)  # f=0.2<0.3, U≥5
    assert sl.next_state(bad, p) is S.RETIRED
    # 使用量不足 → 不退役（样本太少）
    few = rec("a", usage=2, success=0, generation=1, state=S.ACTIVE)
    assert sl.next_state(few, p) is not S.RETIRED


# ── ④ 迟滞 ───────────────────────────────────────────────────

def test_stable_demotes_before_retire_hysteresis():
    """fitness 落在 [δ_retire, δ_demote) → stable 只降级回 active，不直接退役。"""
    p = P(delta_retire=0.3, delta_demote=0.5, delta_stable=0.7, n_retire=5)
    drifting = rec("a", usage=10, success=4, state=S.STABLE)   # f=0.4
    assert sl.next_state(drifting, p) is S.ACTIVE


def test_policy_rejects_non_monotone_thresholds():
    import pytest
    with pytest.raises(ValueError):
        P(delta_retire=0.6, delta_demote=0.5, delta_stable=0.7)


# ── ⑤ 代际保护（Eq. 8）───────────────────────────────────────

def test_generation_zero_needs_higher_retire_bar():
    p = P(n_retire=5, n_protect=15)
    # 同为低 fitness：非原生(U=5) 退役，蒸馏原生(U=5) 因未过 n_protect=15 而受保护
    native = rec("a", usage=5, success=1, generation=0, state=S.ACTIVE)
    mutant = rec("b", usage=5, success=1, generation=1, state=S.ACTIVE)
    assert p.retire_bar(0) == 15 and p.retire_bar(1) == 5
    assert sl.next_state(mutant, p) is S.RETIRED
    assert sl.next_state(native, p) is S.ACTIVE
    # 原生 skill 用够量后同样可退役
    native_used = rec("a", usage=20, success=2, generation=0, state=S.ACTIVE)
    assert sl.next_state(native_used, p) is S.RETIRED


# ── ⑥ 验收：低质量 skill 被标记 retired 且不再加载（PLAN LWP19）──

def test_acceptance_low_quality_retired_and_not_loaded():
    good = rec("search", usage=10, success=9, state=S.ACTIVE)
    bad = rec("legacy_scrape", usage=10, success=1, generation=1, state=S.ACTIVE)  # f=0.1
    rep = sl.assess([good, bad], P(delta_retire=0.3, n_retire=5))

    assert "legacy_scrape" in [r.name for r in rep.retired]
    loadable_names = [r.name for r in rep.loadable]
    assert "search" in loadable_names
    assert "legacy_scrape" not in loadable_names        # ← 不再加载
    assert sl.is_loadable(bad) is True                   # 原对象未被就地改（纯函数）
    assert rep.compactness == 0.5


def test_advance_does_not_mutate_input_records():
    r = rec("a", usage=10, success=1, state=S.ACTIVE)
    sl.advance([r], P())
    assert r.state is S.ACTIVE


# ── ⑦ 复用现有 skill 元数据 ──────────────────────────────────

def test_from_skill_meta_reuses_frontmatter_and_tolerates_missing():
    meta = {"description": "网页搜索", "category": "search",
            "trigger_keywords": ["搜索", "web"]}
    r = sl.SkillRecord.from_skill_meta("search_web", meta, usage=3, success=2)
    assert (r.name, r.description, r.category) == ("search_web", "网页搜索", "search")
    assert r.trigger_keywords == ["搜索", "web"]
    # 缺 meta / 缺字段 → 兜底不报错
    bare = sl.SkillRecord.from_skill_meta("x")
    assert bare.description == "" and bare.trigger_keywords == []
    # 逗号字符串也接受
    csv = sl.SkillRecord.from_skill_meta("y", {"trigger_keywords": "a, b ,c"})
    assert csv.trigger_keywords == ["a", "b", "c"]


def test_invalid_counts_rejected():
    import pytest
    with pytest.raises(ValueError):
        rec("a", usage=1, success=2)          # success > usage


# ── ⑧ 建议不自动淘汰 ─────────────────────────────────────────

def test_retire_candidates_are_advice_only():
    p = P(delta_retire=0.3, n_retire=5)
    bad = rec("bad", usage=5, success=0, generation=1, state=S.ACTIVE)
    good = rec("good", usage=5, success=5, state=S.ACTIVE)
    cands = sl.retire_candidates([bad, good], p)
    assert cands == ["bad"]
    assert bad.state is S.ACTIVE                 # 只产建议，状态未动


def test_mutation_candidates_flag_borderline_band():
    p = P(delta_low=0.3, delta_high=0.7, n_warm=3)
    borderline = rec("mid", usage=10, success=5, state=S.ACTIVE)   # f=0.5 ∈ 带内
    star = rec("star", usage=10, success=9, state=S.STABLE)        # f=0.9 带外
    assert sl.mutation_candidates([borderline, star], p) == ["mid"]


# ── ⑨ 确定性 + 可序列化 ──────────────────────────────────────

def test_report_is_deterministic_and_json_serialisable():
    recs = [rec("a", usage=10, success=1, generation=1, state=S.ACTIVE),
            rec("b", usage=10, success=9, state=S.ACTIVE)]
    a = sl.assess(recs, P()).to_dict()
    b = sl.assess(recs, P()).to_dict()
    assert json.dumps(a, sort_keys=True, ensure_ascii=False) == \
           json.dumps(b, sort_keys=True, ensure_ascii=False)
    parsed = json.loads(json.dumps(a))
    assert parsed["counts"]["retired"] == 1
    assert parsed["counts"]["stable"] == 1
    assert set(parsed["counts"]) == {"trial", "active", "stable", "retired"}


def test_multi_round_advance_converges():
    """反复 advance：trial → active → stable 逐轮推进（每轮一步）。"""
    p = P(n_promote=1, delta_stable=0.7, n_stable=5)
    r = rec("a", usage=5, success=4)
    r = sl.advance([r], p)[0]
    assert r.state is S.ACTIVE
    r = sl.advance([r], p)[0]
    assert r.state is S.STABLE
