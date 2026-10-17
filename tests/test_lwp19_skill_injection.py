"""LWP19 注入门禁：supervisor 的 system prompt 真的带上了技能，且带的是对的技能。

三件事必须同时成立，缺一条这套接线就等于没通电：
1. 有 skill 时 prompt 里出现它（否则加载器白装）；
2. ``retired`` 的 skill **不出现**（生命周期闸门真的在链路上，不是摆设）；
3. 加载器炸了也不能把 supervisor 拖下水（返回空串 + stderr 提示）。

第 3 条是最容易被忽略的：技能注入是**增强**，不是主链路的依赖。
"""
from __future__ import annotations

import pytest


def _write_skill(root, name: str, meta: str) -> None:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        f"---\n{meta}\n---\n\n正文（正文不进 prompt，只进渲染摘要）。\n",
        encoding="utf-8",
    )


@pytest.fixture()
def skills_root(tmp_path, monkeypatch):
    """把 SKILLS_DIR 指到临时目录再调 _render_skills_hint。"""
    from agent_harness.core import config

    root = tmp_path / "skills"
    root.mkdir()
    monkeypatch.setattr(config, "SKILLS_DIR", root)
    return root


def test_no_skills_yields_empty_hint(skills_root) -> None:
    from agent_harness.core.agents.supervisor import _render_skills_hint

    assert _render_skills_hint() == ""


def test_loadable_skill_reaches_prompt(skills_root) -> None:
    from agent_harness.core.agents.supervisor import _render_skills_hint

    _write_skill(
        skills_root,
        "pdf-extract",
        "name: pdf-extract\ndescription: 从 PDF 抽取结构化表格\ntrigger_keywords: pdf, 表格",
    )

    hint = _render_skills_hint()

    assert "pdf-extract" in hint
    assert "从 PDF 抽取结构化表格" in hint
    assert "pdf" in hint and "表格" in hint
    # 只渲染摘要，不把正文塞进 prompt
    assert "正文（正文不进 prompt" not in hint


def test_retired_skill_is_not_injected(skills_root) -> None:
    """生命周期状态在真实链路上生效：retired 不能进 prompt。"""
    from agent_harness.core.agents.supervisor import _render_skills_hint

    _write_skill(
        skills_root,
        "live-one",
        "name: live-one\ndescription: 还在役的技能\ntrigger_keywords: 现行",
    )
    _write_skill(
        skills_root,
        "dead-one",
        "name: dead-one\ndescription: 已退役的技能\ntrigger_keywords: 退役\nstate: retired",
    )

    hint = _render_skills_hint()

    assert "live-one" in hint
    assert "dead-one" not in hint
    assert "已退役的技能" not in hint


def test_loader_failure_degrades_to_empty(skills_root, monkeypatch, capsys) -> None:
    """加载器抛异常 → 空串 + stderr 说明，绝不向上抛。"""
    from agent_harness.core.skills import loader
    from agent_harness.core.agents.supervisor import _render_skills_hint

    def _boom(*_args, **_kwargs):
        raise RuntimeError("模拟加载器损坏")

    monkeypatch.setattr(loader, "scan", _boom)

    assert _render_skills_hint() == ""
    assert "技能注入跳过" in capsys.readouterr().err


def test_token_budget_is_respected(skills_root) -> None:
    """预算足够小的时候不能把所有技能都塞进去（预算真的在起作用）。"""
    from agent_harness.core.agents.supervisor import _render_skills_hint

    for index in range(6):
        _write_skill(
            skills_root,
            f"skill-{index}",
            f"name: skill-{index}\ndescription: {'很长的描述' * 40}\ntrigger_keywords: k{index}",
        )

    hint_tight = _render_skills_hint(token_budget=10)
    hint_wide = _render_skills_hint(token_budget=100_000)

    assert len(hint_tight) < len(hint_wide)
    assert hint_wide.count("- skill-") == 6
