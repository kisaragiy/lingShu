"""LWP19 — 技能加载器（scan / select_prompt_skills / render_skills_prompt）。

为什么要它
----------
生命周期原语已在 :mod:`agent_harness.core.skill_lifecycle` 齐备（四态机、fitness、
``loadable_skills`` 闸门），但灵枢**没有自己的加载器**：``apps/research/api.py`` 的
``_scan_skills()`` 直接指向 **Hermes 的 skills 目录**（跨产品耦合），而且只列目录名，
从不把 skill 内容送进 prompt。于是「skill 库」在灵枢里只是一张列表，不是能力。

本模块补上缺的那一段，且只补一段：

  1. :func:`scan` —— 从**给定 roots**（调用方指定，不再硬编码别家产品的目录）扫描
     ``roots/<name>/SKILL.md``，解析 frontmatter 为
     :class:`~agent_harness.core.skill_lifecycle.SkillRecord`。
  2. :func:`select_prompt_skills` —— 过生命周期闸门（retired 不加载）→ 按 fitness 降序
     → 按 token 预算裁剪。
  3. :func:`render_skills_prompt` —— 渲染成可直接拼进 system prompt 的文本片段。

设计约束
--------
* **纯标准库**：``pyproject.toml`` 里没有 PyYAML，也不引新依赖 —— frontmatter 用一个
  最小的行式解析器（见 :func:`parse_frontmatter`），只认 ``key: value``、``[a, b]``、
  ``- a`` 列表与 ``|`` 块标量，足够覆盖 SKILL.md 的实际形状。
* **不抛异常**：目录不存在 → 空列表；无 frontmatter / frontmatter 畸形 → 跳过该目录。
  skill 库是旁路资产，一条坏 SKILL.md 不该让整个 agent 起不来（静默失败纪律的镜像面：
  不崩，但也不假装成功 —— 坏文件直接不出现）。
* **确定性**：``scan`` 按 name 排序；``select_prompt_skills`` 同 fitness 按 name 升序。
* **只加不改**：不 import ``apps``、不改 ``skill_lifecycle``、不写盘。

用法::

    from agent_harness.core.skills import scan, select_prompt_skills, render_skills_prompt

    records = scan([Path("skills"), Path("vendor_skills")])
    picked = select_prompt_skills(records, token_budget=800)
    prompt_fragment = render_skills_prompt(picked)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from agent_harness.core.skill_lifecycle import (
    LifecyclePolicy,
    SkillRecord,
    SkillState,
    fitness,
    loadable_skills,
)

SKILL_FILENAME = "SKILL.md"

#: frontmatter 值里会被识别为「块标量」的指示符（``description: |`` 这类写法）。
_BLOCK_SCALAR_MARKS = {"|", "|-", "|+", ">", ">-", ">+"}

#: 允许从 frontmatter 里读出的生命周期状态（缺省 / 非法值 → TRIAL）。
_STATES_BY_VALUE = {s.value: s for s in SkillState}


# ═══════════════════════════════════════════════════════════════════════════
# 一、最小 frontmatter 解析器（零依赖）
# ═══════════════════════════════════════════════════════════════════════════


def _split_frontmatter(text: str) -> str | None:
    """取出 ``---`` 包裹的 frontmatter 正文；没有/未闭合 → ``None``。

    只认**文件开头**（允许前置 BOM 与空行）的第一个 ``---`` 块，闭合标记可以是
    ``---`` 或 YAML 的 ``...``。
    """
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines) or lines[i].strip() != "---":
        return None
    for j in range(i + 1, len(lines)):
        if lines[j].strip() in ("---", "..."):
            return "\n".join(lines[i + 1 : j])
    return None


def _unquote(value: str) -> str:
    """去掉成对的首尾引号（``"..."`` / ``'...'``），不做转义展开。"""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _split_inline_list(value: str) -> list[str]:
    """``[a, b, "c"]`` → ``["a", "b", "c"]``（不处理嵌套/逗号转义，SKILL.md 不需要）。"""
    body = value[1:-1].strip()
    if not body:
        return []
    return [_unquote(item.strip()) for item in body.split(",") if item.strip()]


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def parse_frontmatter(text: str) -> dict[str, Any] | None:
    """解析 SKILL.md 的 frontmatter，返回扁平 dict；无 frontmatter → ``None``。

    支持的最小 YAML 子集（够覆盖 SKILL.md，且**抗冒号**）：

    * ``key: value`` —— 只在**第一个** ``: `` 处切分，值里的冒号原样保留
      （``description: AIGC 管线: SDXL/Flux`` 不会把 key 拆坏）。
    * 行内列表 ``key: [a, b]``、逗号串 ``key: a, b``（由 ``SkillRecord`` 负责按逗号拆）。
    * 块列表::

          trigger_keywords:
            - 生图
            - 分镜

    * 块标量 ``key: |`` / ``key: >`` —— 收集后续更深缩进的行，按空格拼接（折叠）。
    * **缩进的嵌套映射一律忽略**（真实 SKILL.md 里的 ``metadata:\\n  hermes:\\n    tags: ...``
      会走到这里），只保留顶层键；这样不会把 ``tags`` 误当成 ``trigger_keywords``。
    """
    block = _split_frontmatter(text)
    if block is None:
        return None

    meta: dict[str, Any] = {}
    lines = block.splitlines()
    i = 0
    last_key: str | None = None
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        i += 1
        if not stripped or stripped.startswith("#"):
            continue
        indent = _indent_of(raw)

        if indent > 0:
            # 缩进内容只可能属于上一个顶层键：`- item` → 列表项；其余（嵌套映射）忽略。
            if last_key is not None and (stripped.startswith("- ") or stripped == "-"):
                item = stripped[1:].strip()
                if item:
                    cur = meta.get(last_key)
                    if isinstance(cur, list):
                        cur.append(_unquote(item))
                    else:
                        meta[last_key] = [_unquote(item)]
            continue

        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        last_key = key

        if value in _BLOCK_SCALAR_MARKS:
            folded: list[str] = []
            while i < len(lines) and (not lines[i].strip() or _indent_of(lines[i]) > 0):
                folded.append(lines[i].strip())
                i += 1
            meta[key] = " ".join(part for part in folded if part)
        elif value.startswith("[") and value.endswith("]"):
            meta[key] = _split_inline_list(value)
        elif value == "":
            meta[key] = []  # 可能是块列表的头，也可能只是个空值
        else:
            meta[key] = _unquote(value)
    return meta


def _state_from_meta(meta: dict[str, Any]) -> SkillState:
    """frontmatter 里的 ``state:`` 决定初始生命周期状态（缺省/非法 → TRIAL）。

    让「退役的 skill」能在磁盘上被表达（``state: retired``），而不必额外维护一份状态表 ——
    与 ``_disabled/`` 目录约定同义。
    """
    raw = str(meta.get("state", "") or "").strip().lower()
    return _STATES_BY_VALUE.get(raw, SkillState.TRIAL)


# ═══════════════════════════════════════════════════════════════════════════
# 二、扫描
# ═══════════════════════════════════════════════════════════════════════════


def _read_skill(directory: Path) -> SkillRecord | None:
    """读一个 skill 目录 → SkillRecord；无 SKILL.md / 解析失败 → ``None``。"""
    skill_file = directory / SKILL_FILENAME
    if not skill_file.is_file():
        return None
    try:
        text = skill_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    meta = parse_frontmatter(text)
    if meta is None:
        return None
    try:
        return SkillRecord.from_skill_meta(
            directory.name, meta, state=_state_from_meta(meta)
        )
    except (ValueError, TypeError):
        # frontmatter 形状超出预期（例如 trigger_keywords 是映射）——跳过，不让它拖垮扫描。
        return None


def scan(roots: Iterable[Path | str]) -> list[SkillRecord]:
    """扫描 ``roots/<name>/SKILL.md``，返回按 name 升序的 :class:`SkillRecord` 列表。

    * 只扫**一级**子目录（不递归）—— skill 目录的约定就是 ``<root>/<name>/SKILL.md``。
    * ``root`` 不存在 / 不是目录 → 跳过（不抛异常）；重复 name 保留先出现的那条
      （roots 顺序即优先级），保证「同名 skill 由靠前的 root 覆盖」。
    * 任何单条解析失败都只跳过该条。
    """
    records: list[SkillRecord] = []
    seen: set[str] = set()
    for root in roots:
        base = Path(root)
        if not base.is_dir():
            continue
        try:
            children = sorted(p for p in base.iterdir() if p.is_dir())
        except OSError:
            continue
        for child in children:
            if child.name.startswith((".", "_")):  # `_disabled/`、`.git/` 等约定目录
                continue
            if child.name in seen:
                continue
            record = _read_skill(child)
            if record is None:
                continue
            seen.add(record.name)
            records.append(record)
    return sorted(records, key=lambda r: r.name)


# ═══════════════════════════════════════════════════════════════════════════
# 三、选择（生命周期闸门 + fitness 排序 + token 预算）
# ═══════════════════════════════════════════════════════════════════════════


def estimate_tokens(record: SkillRecord) -> int:
    """粗估一条 skill 进 prompt 要花的 token（不引 tokenizer，保持零依赖/确定性）。

    公式（刻意保守、可解释）：:

        len(name) // 4
      + len(description) // 4          # 英文 ~4 字符/token
      + 2 * len(trigger_keywords)      # 每个触发词按 2 token（中文关键词 ≈ 1 字 1 token + 分隔）

    对中文 description 会低估（中文 ≈ 1 token/字），但预算本身就是软上限；调用方可用
    更小的 ``token_budget`` 换安全边际。
    """
    return (
        len(record.name) // 4
        + len(record.description) // 4
        + 2 * len(record.trigger_keywords)
    )


def select_prompt_skills(
    records: Iterable[SkillRecord],
    policy: LifecyclePolicy | None = None,
    *,
    token_budget: int = 2000,
) -> list[SkillRecord]:
    """挑出可以进 prompt 的 skill 子集（确定性）。

    三步：

    1. :func:`loadable_skills` 闸门 —— ``retired`` 不加载（生命周期语义的唯一判据）。
    2. 按 :func:`fitness` **降序**，fitness 相同按 ``name`` 升序（保证同输入同输出）。
    3. 按 :func:`estimate_tokens` 累加，**前缀截断**到 ``token_budget``。

    预算的边界语义（写清以免被当成 bug）：

    * ``token_budget <= 0`` → 返回 ``[]``（显式要求不注入）。
    * 若**第一条**（fitness 最高的那条）单独就超预算，仍然返回它 —— 否则加载器在最紧的
      预算下永远注入不了任何 skill，这是「假装成功」的另一面。超额只允许出现在这一条上。
    * 之后的每一条只要超预算就停止（不再尝试更靠后的条目：追加低 fitness 的 skill 换掉
      高 fitness 的位置没有意义）。
    """
    if token_budget <= 0:
        return []
    candidates = sorted(
        loadable_skills(records), key=lambda r: (-fitness(r, policy), r.name)
    )
    selected: list[SkillRecord] = []
    used = 0
    for record in candidates:
        cost = estimate_tokens(record)
        if selected and used + cost > token_budget:
            break
        selected.append(record)
        used += cost
    return selected


# ═══════════════════════════════════════════════════════════════════════════
# 四、渲染
# ═══════════════════════════════════════════════════════════════════════════


def render_skills_prompt(records: Iterable[SkillRecord]) -> str:
    """把 skill 列表渲染成 system prompt 片段（一行一条）。

    格式::

        - name: 描述（触发: 关键词, 关键词）

    空列表 → ``""``（**不返回标题**：调用方自行决定是否要这段，空标题只会污染 prompt）。
    描述为空 → 省略描述；无触发词 → 省略括号段。
    """
    lines: list[str] = []
    for record in records:
        line = f"- {record.name}"
        if record.description:
            line += f": {record.description}"
        if record.trigger_keywords:
            line += f"（触发: {', '.join(record.trigger_keywords)}）"
        lines.append(line)
    return "\n".join(lines)
