"""LWP19 —— 灵枢自有 skill 加载器（``core/skills/loader.py``）的单测。

覆盖：frontmatter 两种触发词形态（逗号串 / YAML 列表）、生命周期闸门（retired 不加载，
两条路径：直接给 records + 走 tmp_path 真实 SKILL.md）、fitness 排序、token 预算裁剪、
以及「扫盘 → 选择 → 渲染」的真实端到端（不 mock 任何一环）。
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from agent_harness.core.skill_lifecycle import SkillRecord, SkillState
from agent_harness.core.skills import (
    estimate_tokens,
    parse_frontmatter,
    render_skills_prompt,
    scan,
    select_prompt_skills,
)


def _write_skill(root: Path, name: str, frontmatter: str, body: str = "# body\n") -> Path:
    """在 ``root/name/SKILL.md`` 写一个 skill 目录，返回该目录。"""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        "---\n" + textwrap.dedent(frontmatter).strip("\n") + "\n---\n" + body,
        encoding="utf-8",
    )
    return d


# ═══════════════════════ 一、frontmatter 解析 ═══════════════════════


def test_parse_frontmatter_comma_string_keywords():
    """trigger_keywords 写成逗号串 → SkillRecord 拆成列表。"""
    meta = parse_frontmatter(
        "---\n"
        'description: "生成技术插图"\n'
        "trigger_keywords: 生图, 分镜 ,插画\n"
        "---\n# 正文\n"
    )
    assert meta is not None
    rec = SkillRecord.from_skill_meta("illustrator", meta)
    assert rec.description == "生成技术插图"
    assert rec.trigger_keywords == ["生图", "分镜", "插画"]


def test_parse_frontmatter_yaml_block_list_keywords(tmp_path: Path):
    """trigger_keywords 写成 YAML 块列表 → 同样得到列表。"""
    root = tmp_path / "skills"
    _write_skill(
        root,
        "storyboard",
        """
        description: 写分镜
        trigger_keywords:
          - 分镜
          - 运镜
        """,
    )
    records = scan([root])
    assert [r.name for r in records] == ["storyboard"]
    assert records[0].trigger_keywords == ["分镜", "运镜"]


def test_parse_frontmatter_inline_list_and_colon_in_value():
    """行内列表可用；description 里含冒号不会被切坏（只在第一个冒号处切）。"""
    meta = parse_frontmatter(
        "---\n"
        "description: AIGC 管线: SDXL/Flux 双引擎\n"
        "trigger_keywords: [生图, flux]\n"
        "category: mlops\n"
        "---\n"
    )
    assert meta == {
        "description": "AIGC 管线: SDXL/Flux 双引擎",
        "trigger_keywords": ["生图", "flux"],
        "category": "mlops",
    }


def test_parse_frontmatter_ignores_nested_mapping():
    """真实 SKILL.md 形态：metadata 下嵌套 tags 不得被误当成 trigger_keywords。"""
    meta = parse_frontmatter(
        "---\n"
        "name: hermes-agent\n"
        "description: Configure or extend Hermes\n"
        "metadata:\n"
        "  hermes:\n"
        "    tags: [hermes, setup]\n"
        "---\n"
    )
    assert meta is not None
    assert meta["description"] == "Configure or extend Hermes"
    assert "trigger_keywords" not in meta


@pytest.mark.parametrize("text", ["# 没有 frontmatter\n", "---\ndescription: x\n", ""])
def test_parse_frontmatter_absent_or_unclosed_returns_none(text: str):
    assert parse_frontmatter(text) is None


# ═══════════════════════ 二、scan ═══════════════════════


def test_scan_missing_root_returns_empty():
    """不存在的目录 → 空列表，不抛异常。"""
    assert scan([Path("C:/definitely/not/here/lwp19")]) == []


def test_scan_skips_broken_and_underscore_dirs_sorted_by_name(tmp_path: Path):
    """坏 SKILL.md / 无 SKILL.md / `_disabled/` 都跳过；结果按 name 排序。"""
    root = tmp_path / "skills"
    _write_skill(root, "zeta", "description: z")
    _write_skill(root, "alpha", "description: a")
    (root / "no-skill-md").mkdir()                          # 无 SKILL.md
    (root / "broken").mkdir()
    (root / "broken" / "SKILL.md").write_text("no frontmatter here\n", encoding="utf-8")
    (root / "_disabled" / "dead").mkdir(parents=True)
    (root / "_disabled" / "dead" / "SKILL.md").write_text(
        "---\ndescription: dead\n---\n", encoding="utf-8"
    )
    assert [r.name for r in scan([root])] == ["alpha", "zeta"]


# ═══════════════════════ 三、生命周期闸门：retired 不加载 ═══════════════════════


def _rec(name: str, *, state: SkillState = SkillState.ACTIVE, usage: int = 4,
         success: int = 3, desc: str = "") -> SkillRecord:
    return SkillRecord(
        name=name, usage=usage, success=success, state=state,
        description=desc or f"{name} 的描述", trigger_keywords=[name],
    )


def test_select_excludes_retired_records():
    """直接传 records：retired 的那条不得出现在选择结果里。"""
    records = [
        _rec("keep_a"),
        _rec("dead", state=SkillState.RETIRED),
        _rec("keep_b"),
    ]
    picked = [r.name for r in select_prompt_skills(records)]
    assert "dead" not in picked
    assert set(picked) == {"keep_a", "keep_b"}


def test_scan_reads_retired_state_from_frontmatter(tmp_path: Path):
    """frontmatter 里 ``state: retired`` → 该条被选择器过滤掉。"""
    root = tmp_path / "skills"
    _write_skill(root, "alive", "description: 活着\nstate: active")
    _write_skill(root, "dead", "description: 已退役\nstate: retired")
    records = scan([root])
    assert {r.name: r.state for r in records}["dead"] is SkillState.RETIRED
    picked = [r.name for r in select_prompt_skills(records)]
    assert picked == ["alive"]


# ═══════════════════════ 四、fitness 排序 + 预算裁剪 ═══════════════════════


def test_selection_sorted_by_fitness_desc():
    """fitness 高的在前：success/usage 高的优先，即便名字字典序在后。"""
    records = [
        _rec("aaa_low", usage=10, success=2),    # f=0.2
        _rec("zzz_high", usage=10, success=9),   # f=0.9
        _rec("mmm_mid", usage=10, success=5),    # f=0.5
    ]
    assert [r.name for r in select_prompt_skills(records)] == [
        "zzz_high", "mmm_mid", "aaa_low"
    ]


def test_selection_tie_breaks_on_name_for_determinism():
    """fitness 相同时按 name 升序（同输入同输出）。"""
    records = [_rec("b", usage=10, success=5), _rec("a", usage=10, success=5)]
    assert [r.name for r in select_prompt_skills(records)] == ["a", "b"]


def test_selection_truncates_on_small_budget():
    """预算很小 → 只返回前几条（按 fitness 前缀截断，且至少保一条）。"""
    records = [_rec(f"s{i}", usage=10, success=10 - i, desc="x" * 200) for i in range(5)]
    full = select_prompt_skills(records, token_budget=100_000)
    assert len(full) == 5

    per_item = estimate_tokens(records[0])
    trimmed = select_prompt_skills(records, token_budget=per_item * 2 + 1)
    assert 1 <= len(trimmed) < len(full)
    assert [r.name for r in trimmed] == ["s0", "s1"]       # 仍是全局最优前缀


def test_selection_budget_zero_returns_empty():
    assert select_prompt_skills([_rec("a")], token_budget=0) == []


# ═══════════════════════ 五、渲染 ═══════════════════════


def test_render_empty_returns_blank():
    assert render_skills_prompt([]) == ""


def test_render_includes_name_description_keywords():
    text = render_skills_prompt([_rec("storyboard", desc="写分镜")])
    assert "storyboard" in text
    assert "写分镜" in text
    assert "分镜" in text


# ═══════════════════════ 六、端到端（真实扫盘，不 mock） ═══════════════════════


def test_end_to_end_scan_select_render(tmp_path: Path):
    """tmp_path 造 3 个 skill（含 1 个 retired）→ 扫盘 → 选择 → 渲染 prompt 片段。"""
    root_a = tmp_path / "skills"
    root_b = tmp_path / "vendor_skills"

    _write_skill(
        root_a, "lora-training",
        """
        description: LoRA 训练全流程：标图/参数/Block Weights
        trigger_keywords:
          - LoRA
          - 训练
        state: active
        """,
    )
    _write_skill(
        root_a, "retired-skill",
        """
        description: 这条应该永远不出现在 prompt 里
        trigger_keywords: [退役]
        state: retired
        """,
    )
    _write_skill(
        root_b, "resume-builder",
        "description: 生成求职简历 PDF\ntrigger_keywords: 简历, PDF",
    )

    records = scan([root_a, root_b, tmp_path / "missing"])
    assert [r.name for r in records] == ["lora-training", "resume-builder", "retired-skill"]

    picked = select_prompt_skills(records, token_budget=2000)
    prompt = render_skills_prompt(picked)

    assert "lora-training" in prompt
    assert "resume-builder" in prompt
    assert "retired-skill" not in prompt
    assert "退役" not in prompt                        # 触发词也没漏出来
    assert prompt.count("\n") == 1                     # 两条可加载 skill → 两行
