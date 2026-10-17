# 灵枢技能目录

灵枢**自有**的技能库。这里不读、不镜像 Hermes 的任何数据目录——灵枢是独立产品，
Hermes 集成是它的可选下游（见 ADR-001）。

## 结构

```
skills/
├── pdf-extract/
│   └── SKILL.md          # 一个技能 = 一个目录 + 一份 SKILL.md
├── code-review/
│   └── SKILL.md
└── _disabled/            # 停用但保留：不进 prompt，列表里显示为未启用
    └── old-skill/
        └── SKILL.md
```

- 目录名即技能名（`scan()` 用它做确定性排序）。
- `_` 或 `.` 开头的目录被忽略，`_disabled/` 是约定俗成的停用区。
- 缺 `SKILL.md` 的目录跳过，不报错。

## SKILL.md 格式

```markdown
---
name: pdf-extract
description: 从 PDF 抽取结构化表格
trigger_keywords: pdf, 表格, excel
state: active
---

正文内容（只在技能被完整加载时读取，不进 supervisor 的 prompt 摘要）。
```

`frontmatter` 只支持 `key: value`、`[a, b]`、`- item` 列表三种写法，**不引入 PyYAML**。
块标量（`description: |`）不支持。

`trigger_keywords` 决定技能被选中的机会，也用来估算 token 预算——
每个关键词按 2 token 计，描述按字数计。写得越长越容易被预算裁掉。

## 生命周期

`state` 决定技能能不能进 prompt：

| state | 含义 |
|---|---|
| `active` / 缺省 | 在役，可被加载 |
| `trial` | 试用，可被加载 |
| `retired` | **退役，不进 prompt**（磁盘上表达退役，不必额外维护状态表） |
| 非法值 | 按 `trial` 处理，不崩 |

排序按 fitness 降序（沿用 `core/skill_lifecycle` 的四态机与适应度），
fitness 相同按 name 升序 —— 同输入必然同输出。

## 覆盖路径

默认 `HARNESS_DIR.parent.parent / "skills"`（即仓库根 `skills/`）。用环境变量改：

```bash
export HARNESS_SKILLS_DIR=/path/to/my/skills
```

目录不存在时 `scan()` 返回空列表，supervisor 不注入任何技能提示 —— 干净检出必须能起来。
