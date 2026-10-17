"""灵枢自有技能层（LWP19）。

本包把「skill 库」从产品侧（外部 agent 的 skills 目录）收回到 harness 内核：

* :mod:`.loader` —— 扫描 ``roots/*/SKILL.md`` → :class:`~agent_harness.core.skill_lifecycle.SkillRecord`
  列表；按 fitness + token 预算挑选可注入 prompt 的子集；渲染成 system prompt 片段。
* :mod:`agent_harness.core.skill_lifecycle`（已存在，本包只读不改）—— 四态生命周期与 fitness。

设计约束：纯标准库（frontmatter 自带最小解析器，**不引 PyYAML**）、无网络、无 LLM、
确定性（同输入 → 同输出），便于离线单测。
"""
from __future__ import annotations

from .loader import (
    estimate_tokens,
    parse_frontmatter,
    render_skills_prompt,
    scan,
    select_prompt_skills,
)

__all__ = [
    "estimate_tokens",
    "parse_frontmatter",
    "render_skills_prompt",
    "scan",
    "select_prompt_skills",
]
