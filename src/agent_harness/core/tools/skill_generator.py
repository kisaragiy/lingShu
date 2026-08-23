"""对话式 Skill 生成器 — NL → SKILL.md → 安装

P2 功能：用户用自然语言描述一个 skill 的功能，
系统自动生成符合规范的 SKILL.md 文件并注册。

流程:
  1. LLM 从 NL 描述提取: name, description, trigger_keywords, steps, params, notes
  2. 生成 SKILL.md（YAML frontmatter + markdown body）
  3. 写入 skills/<name>/SKILL.md
  4. 返回安装路径和摘要
"""
import json, os, re
from pathlib import Path
from ..pipeline.llm import call_llama

SKILLS_DIR = Path(os.environ.get("HARNESS_SKILLS_DIR",
    Path.home() / ".agent-harness" / "skills"))

_GENERATE_PROMPT = """You are a Skill designer. Based on the user's natural language description, generate a complete AI Agent Skill file.

Skill specification:
- YAML frontmatter (surrounded by ---): name, description, trigger_keywords (list), category
- Markdown body: ## sections for Steps, Parameters, Notes, Examples
- Steps must be numbered, clear, actionable
- Parameters: table with name, type, default, description

User description:
---
{description}
---

Output ONLY the SKILL.md content, format:
---
name: <skill name>
description: <one-line description>
trigger_keywords: [keyword1, keyword2]
category: <category>
---

## Steps

1. ...
2. ..."""


def generate_skill(description: str, name: str = None) -> dict:
    """Generate a SKILL.md from NL description and install it."""
    if not description or not description.strip():
        return {"ok": False, "error": "描述为空"}

    # Step 1: LLM generates SKILL.md
    try:
        prompt = _GENERATE_PROMPT.format(description=description[:3000])
        raw, _ = call_llama(
            [{"role": "user", "content": prompt}],
            system_prompt="You are a Skill designer. Output ONLY the complete SKILL.md content, no extra text.",
        )
    except Exception as e:
        return {"ok": False, "error": f"LLM 生成失败: {e}"}

    if not raw or not raw.strip():
        return {"ok": False, "error": "LLM 返回为空"}

    # Step 2: Extract name from YAML frontmatter
    m_name = re.search(r"^name:\s*(.+)$", raw, re.MULTILINE)
    skill_name = name or (m_name.group(1).strip() if m_name else "custom_skill")

    # Step 3: Write SKILL.md
    skill_dir = SKILLS_DIR / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(raw.strip(), encoding="utf-8")

    # Step 4: Validate format
    has_yaml = raw.startswith("---")
    preview = raw[:300] + ("..." if len(raw) > 300 else "")

    return {
        "ok": True,
        "skill_name": skill_name,
        "path": str(skill_path),
        "has_valid_frontmatter": has_yaml,
        "preview": preview,
        "hint": "安装完成后请在技能市场中启用此技能",
    }


# Auto-register when imported by tools __init__
from .registry import register_tool

register_tool("skill_generate", generate_skill, {
    "description": "对话式 Skill 生成器 — 用自然语言描述 Skill 功能，自动生成 SKILL.md 并安装。参数: description=功能描述, name=指定名称",
    "properties": {"description": {"type": "string"}, "name": {"type": "string"}},
}, privilege="reversible")
