"""judge 冻结（P2-3）—— 版本化判分类 + 注册/冻结 + 多 judge 共识 + 变更重测，全离线可测。

判据（量尺三问）：
  ① 判据/模型**一变戳就变**（reuse versioning 稳定戳，跨进程可复现）；
  ② 冻结后可**检测漂移**，并点出需要重测的 judge；
  ③ 多 judge 离散过大 → **不共识**（拒绝单 judge 偏置）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core import judge_registry as jr  # noqa: E402


# ── ① JudgeSpec 戳 ───────────────────────────────────────────

def test_stamp_stable_and_sensitive():
    a = jr.build_spec("rubric", "v1", model="deepseek-flash", criteria={"kw": 40})
    a2 = jr.build_spec("rubric", "v1", model="deepseek-flash", criteria={"kw": 40})
    b = jr.build_spec("rubric", "v1", model="deepseek-flash", criteria={"kw": 50})
    m = jr.build_spec("rubric", "v1", model="gpt-x", criteria={"kw": 40})
    v = jr.build_spec("rubric", "v2", model="deepseek-flash", criteria={"kw": 40})
    assert a.stamp == a2.stamp
    assert a.stamp != b.stamp          # 判据变
    assert a.stamp != m.stamp          # 模型变
    assert a.stamp != v.stamp          # 版本变
    assert len(a.stamp) == 12


def test_spec_to_dict():
    d = jr.build_spec("j", "v1", criteria={"x": 1}).to_dict()
    assert d["name"] == "j" and d["stamp"] and d["criteria"] == {"x": 1}


# ── ② 注册表 + 冻结 + 漂移 ───────────────────────────────────

def test_registry_resolve_latest_and_version():
    reg = jr.JudgeRegistry()
    v1 = reg.register(jr.build_spec("rubric", "v1"))
    v2 = reg.register(jr.build_spec("rubric", "v2"))
    assert reg.resolve("rubric").version == "v2"        # 默认最新
    assert reg.resolve("rubric", "v1") is v1
    assert reg.resolve("nope") is None
    assert reg.names() == ["rubric"]


def test_register_same_version_overwrites():
    reg = jr.JudgeRegistry()
    reg.register(jr.build_spec("rubric", "v1", criteria={"a": 1}))
    reg.register(jr.build_spec("rubric", "v1", criteria={"a": 2}))
    assert len(reg._by_name["rubric"]) == 1
    assert reg.resolve("rubric").criteria == {"a": 2}


def test_freeze_and_detect_drift():
    reg = jr.JudgeRegistry()
    reg.register(jr.build_spec("rubric", "v1", criteria={"kw": 40}))
    reg.register(jr.build_spec("safety", "v1"))
    snap = reg.freeze()
    assert set(snap) == {"rubric", "safety"}
    assert reg.detect_drift()["changed"] == []

    # 改 rubric 判据 → changed + needs_recheck
    reg.register(jr.build_spec("rubric", "v2", criteria={"kw": 50}))
    d = reg.detect_drift()
    assert d["changed"] == ["rubric"]
    assert d["needs_recheck"] == ["rubric"]
    assert d["added"] == [] and d["removed"] == []


def test_detect_drift_added_and_removed():
    reg = jr.JudgeRegistry()
    reg.register(jr.build_spec("a", "v1"))
    reg.freeze()
    prev = {"a": reg.resolve("a").stamp, "old": "deadbeef"}
    reg.register(jr.build_spec("b", "v1"))
    d = reg.detect_drift(prev)
    assert d["added"] == ["b"]
    assert d["removed"] == ["old"]
    assert set(d["needs_recheck"]) == {"b"}


# ── ③ 多 judge 共识 ──────────────────────────────────────────

def test_consensus_median_and_agree():
    c = jr.consensus([0.80, 0.82, 0.79])
    assert c["score"] == 0.80 and c["n"] == 3
    assert c["agree"] is True


def test_consensus_mean_method():
    c = jr.consensus([0.80, 0.90], method="mean")
    assert c["score"] == 0.85


def test_consensus_disagreement_flags_bias():
    c = jr.consensus([0.20, 0.90], max_spread=0.15)
    assert c["agree"] is False and c["spread"] == 0.70


def test_consensus_empty_is_not_agreement():
    c = jr.consensus([])
    assert c["score"] is None and c["agree"] is False and c["n"] == 0


# ── ④ 变更 → 重测历史错点样本 ────────────────────────────────

def test_recheck_samples_reports_transitions():
    samples = [
        {"id": "s1", "judge_passed": False},
        {"id": "s2", "judge_passed": False},
        {"id": "s3", "judge_passed": True},
    ]
    # 新 judge 把 s1 翻成对、把 s3 翻成错
    rc = jr.recheck_samples(samples, lambda s: s["id"] == "s1", loosening_rate=0.5)
    assert rc["n"] == 3
    assert rc["loosened"] == 1      # s1: 原错→新对
    assert rc["tightened"] == 1     # s3: 原对→新错
    assert rc["fix_rate"] == 0.5    # 1/2 错点被翻面，未超阈值
    assert rc["loosening_suspected"] is False


def test_recheck_samples_loosening_threshold():
    # 2 个错点全被翻成对 → fix_rate 1.0 → 疑似放水
    samples = [{"id": "s1", "judge_passed": False}, {"id": "s2", "judge_passed": False}]
    rc = jr.recheck_samples(samples, lambda s: True, loosening_rate=0.5)
    assert rc["loosened"] == 2 and rc["loosening_suspected"] is True


def test_recheck_samples_judge_exception_is_safe():
    samples = [{"id": "s1", "judge_passed": True}]
    rc = jr.recheck_samples(samples, lambda s: (_ for _ in ()).throw(RuntimeError("boom")))
    assert rc["n"] == 1 and rc["tightened"] == 1  # 异常 → 视为不通过


def test_recheck_no_wrong_samples():
    rc = jr.recheck_samples([{"id": "s1", "judge_passed": True}], lambda s: True)
    assert rc["fix_rate"] == 0.0 and rc["loosening_suspected"] is False


def test_module_self_test_passes():
    assert jr.self_test() == 0
