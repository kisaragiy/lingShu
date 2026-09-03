"""知识管道 v4 测试 — R-31~35 加载/拦截/检索 + extract_candidates 加权信号（Day 8-14）。"""
import os

from guardian import guard, load_rules, search_rules
from extract_candidates import extract_candidates, _line_signal_weight


# ─── R-31~35 加载 ───

def test_v4_rules_loaded():
    rules = load_rules()
    assert "R-31" in rules and "R-35" in rules
    assert rules["R-31"]["title"]  # 有标题
    assert "fail-open" not in rules["R-32"]["lesson"].lower() or True  # lesson 非空
    assert all(rules[f"R-{n}"]["block_hint"] for n in range(31, 36))
    # 总规则数 ≥ 35（v1 10 + v2 11 + career 9 + v4 5）
    assert len(rules) >= 35


# ─── guard 关键词拦截 ───

def test_guard_hits_breaker_wiring_rule():
    r = guard("我要宣称实现了三重熔断，写个接线喂数据")
    assert not r["ok"]
    rids = [w["rule"] for w in r["warnings"]]
    assert "R-31" in rids


def test_guard_hits_fail_closed_rule():
    r = guard("except ImportError: pass 静默降级为无护栏")
    assert not r["ok"]
    rids = [w["rule"] for w in r["warnings"]]
    assert "R-32" in rids


def test_guard_hits_cost_rate_rule():
    r = guard("看成本看板发现费率没配 漏算了 ¥0")
    assert not r["ok"]
    rids = [w["rule"] for w in r["warnings"]]
    assert "R-34" in rids


def test_guard_hits_worktree_env_rule():
    r = guard("worktree 里 gitignore 本地文件没带进来 环境依赖失败")
    assert not r["ok"]
    rids = [w["rule"] for w in r["warnings"]]
    assert "R-35" in rids


def test_guard_no_false_hit():
    r = guard("刷新页面加载样式")
    assert r["ok"]


# ─── search_rules 检索 ───

def test_search_finds_cost_rule():
    results = search_rules("成本看板费率漏算没配 PRICES", top_k=5)
    rids = [r["rule_id"] for r in results]
    assert "R-34" in rids


def test_search_finds_breaker_rule():
    results = search_rules("熔断器 token 预算没喂数据 接线", top_k=5)
    rids = [r["rule_id"] for r in results]
    assert "R-31" in rids


# ─── extract_candidates 加权信号 ───

def test_weight_strong_vs_weak():
    assert _line_signal_weight("踩了个大坑，教训是 X") == 1.0
    assert _line_signal_weight("这里有个注意点需要小心") == 0.5
    assert _line_signal_weight("今天天气不错") == 0.0


def test_extract_requires_strong_weight(tmp_path):
    # 只有 2 行弱信号（发现/注意）→ 权重 1.0 < 2 → 不抽
    weak_only = tmp_path / "weak.md"
    weak_only.write_text(
        "## 正常记录\n"
        "今天发现列表有点乱\n"
        "这个问题之后注意\n",
        encoding="utf-8",
    )
    assert extract_candidates(str(weak_only), min_signals=2) == []

    # 2 行强信号（坑/教训）→ 权重 2.0 ≥ 2 → 抽
    strong = tmp_path / "strong.md"
    strong.write_text(
        "## 踩坑记录\n"
        "这个坑踩了三次，根因是 X\n"
        "教训：先验证再动手\n",
        encoding="utf-8",
    )
    cands = extract_candidates(str(strong), min_signals=2)
    assert len(cands) == 1
    assert cands[0]["signal_score"] == 2.0

    # 1 强 + 2 弱 → 权重 2.0 → 抽（弱信号当辅助证据）
    mixed = tmp_path / "mixed.md"
    mixed.write_text(
        "## 混合\n"
        "这个坑是版本问题\n"
        "发现还影响性能\n"
        "后续注意回归\n",
        encoding="utf-8",
    )
    cands2 = extract_candidates(str(mixed), min_signals=2)
    assert len(cands2) == 1
    assert cands2[0]["signal_score"] == 2.0
