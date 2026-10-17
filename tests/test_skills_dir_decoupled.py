"""LWP19 解耦门禁：灵枢技能目录不得指向 Hermes 数据目录。

背景（ADR-001）：`apps/research/api.py` 曾把技能根目录硬编码为
`~/AppData/Local/hermes/skills`，等于把灵枢当 Hermes 的下游。这不只是
路径问题——它决定了「灵枢的技能页展示谁的技能」，是产品边界的载体。

这两条测试守两件事：
1. 代码里不再出现 hermes 目录（回归即红）；
2. `_scan_skills()` 在新目录约定下真的能列出技能（不是只会返回空）。
"""
from __future__ import annotations


def test_skills_dir_is_not_hermes_data_dir() -> None:
    """技能根目录不能落在 Hermes 的 profile 数据目录里。"""
    from agent_harness.apps.research import api

    resolved = str(api._SKILLS_DIR).replace("\\", "/").lower()
    assert "appdata/local/hermes" not in resolved, (
        f"技能目录回到了 Hermes 数据目录：{api._SKILLS_DIR}"
    )
    assert "appdata/local/hermes/skills" not in resolved


def test_scan_skills_lists_enabled_and_disabled(tmp_path, monkeypatch) -> None:
    """`_scan_skills()` 在灵枢自有目录下能列出启用/禁用两类技能。"""
    from agent_harness.apps.research import api

    skills_root = tmp_path / "skills"
    (skills_root / "alpha").mkdir(parents=True)
    (skills_root / "beta").mkdir()
    (skills_root / "_disabled" / "gamma").mkdir(parents=True)
    # 下划线开头的私有目录、点开头的隐藏目录都应被忽略
    (skills_root / "_internal").mkdir()
    (skills_root / ".hidden").mkdir()

    monkeypatch.setattr(api, "_SKILLS_DIR", skills_root)

    skills = api._scan_skills()
    by_name = {s["name"]: s["enabled"] for s in skills}

    assert by_name == {"alpha": True, "beta": True, "gamma": False}


def test_scan_skills_missing_dir_returns_empty(tmp_path, monkeypatch) -> None:
    """目录不存在时返回空列表，不抛异常（首装/裸检出必须能起来）。"""
    from agent_harness.apps.research import api

    monkeypatch.setattr(api, "_SKILLS_DIR", tmp_path / "nope")

    assert api._scan_skills() == []
