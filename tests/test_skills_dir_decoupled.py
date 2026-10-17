"""LWP19 解耦门禁：灵枢技能目录必须在仓库内，不得指向用户级 agent 数据目录。

背景（ADR-001）：`apps/research/api.py` 曾把技能根目录硬编码为某个用户级
agent 的数据目录（`~/AppData/Local/<agent>/skills`），等于把灵枢当作外部产品
的下游。这不只是路径问题——它决定了「灵枢的技能页展示谁的技能」，是产品边界的载体。

这两条测试守两件事：
1. 技能目录必须来自 `core.config`（单一事实源），不得在 API 层自行拼接（回归即红）；
2. `_scan_skills()` 在新目录约定下真的能列出技能（不是只会返回空）。
"""
from __future__ import annotations


def test_skills_dir_follows_core_config() -> None:
    """技能根目录必须来自 core.config（单一事实源），不得在 API 层自行拼接外部路径。"""
    from agent_harness.apps.research import api
    from agent_harness.core import config

    assert api._SKILLS_DIR == config.SKILLS_DIR, (
        f"技能目录偏离 core.config：{api._SKILLS_DIR} != {config.SKILLS_DIR}"
    )


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
