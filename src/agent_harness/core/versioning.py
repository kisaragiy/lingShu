"""配置 / Prompt 版本戳（P1-7 · L 侧）—— 给 prompt 模板、system prompt 与关键 config
算**内容哈希版本**，让"到底喂了什么 / 跑在什么配置上"可追踪、可 diff。

复用而非重造：哈希一律走 ``reproducibility.stable_hash``（sha256 + 稳定序列化，
跨进程/跨机器一致，**绝不使用内置 hash()**）。本模块只做纯函数 —— 不触网、不写全局态。

被追踪的目标分两类：
  1. **Prompt 源**（模块内大写常量字符串）：内容哈希 → sha256 前 12 位即"版本戳"。
  2. **关键 Config**：``Settings`` 各字段默认值逐字段哈希（变更可定位到具体字段）。

**变更门禁用法（一句话）**：把本模块产出的 ``manifest`` 与金标漂移哨兵
（``scripts/eval_drift_sentinel.py``）配对 —— 每次改动先 ``scripts/prompt_version.py stamp``
存基线、CI 上 ``scripts/prompt_version.py check`` 比对；prompt/config 一变版本戳就变，
提示你"这次改动动了行为输入，请连同金标集一起重跑确认没漂"。版本戳变了 + 金标成功率掉了
= 高置信度的行为回归。
"""
from __future__ import annotations

from typing import Any

from agent_harness.core.reproducibility import stable_hash

# ── 被追踪的 prompt 源（模块路径, 属性名）────────────────────────
# 保持声明式：新增 prompt 常量时只加一行，无需改逻辑。
PROMPT_SOURCES: list[tuple[str, str]] = [
    ("agent_harness.core.pipeline.llm", "CENSORED_SYSTEM_PROMPT"),
    ("agent_harness.core.tools.memory_import", "_IMPORT_PROMPT"),
    ("agent_harness.core.tools.misc", "_MEMORY_IMPORT_PROMPT"),
    ("agent_harness.core.tools.skill_generator", "_GENERATE_PROMPT"),
    ("agent_harness.core.tools.web", "_QUERY_DECOMPOSE_PROMPT"),
    ("agent_harness.apps.research.agents.comic_agent", "_SCRIPT_SYSTEM_PROMPT"),
]

# ── 被追踪的关键 config 字段（预算/编排/多智能体 —— 影响行为的那些）──
CONFIG_FIELDS: list[str] = [
    "max_retries",
    "max_iterations",
    "max_tokens",
    "max_time",
    "max_no_progress",
    "max_workers",
    "supervisor_rounds",
    "model_llama",
    "model_deepseek",
]

STAMP_LEN = 12  # 版本戳展示长度（sha256 前缀）


def version_stamp(text: Any) -> str:
    """任一可 JSON 化对象 → 短版本戳（sha256 前 12 位）。"""
    return stable_hash(text)[:STAMP_LEN]


def _collect_prompts() -> dict[str, dict]:
    import importlib

    out: dict[str, dict] = {}
    for module, attr in PROMPT_SOURCES:
        try:
            mod = importlib.import_module(module)
            text = getattr(mod, attr)
        except (ImportError, AttributeError) as exc:  # 容忍缺失：不因一个漏项全挂
            out[f"prompt:{attr}"] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        out[f"prompt:{attr}"] = {
            "sha256": stable_hash(text),
            "stamp": version_stamp(text),
            "chars": len(text),
        }
    return out


def _collect_config() -> dict[str, dict]:
    from agent_harness.core.config import Settings

    out: dict[str, dict] = {}
    fields = Settings.model_fields
    for name in CONFIG_FIELDS:
        if name not in fields:
            out[f"config:{name}"] = {"error": "field not found"}
            continue
        default = fields[name].default
        out[f"config:{name}"] = {
            "sha256": stable_hash(default),
            "stamp": version_stamp(default),
            "value": default,
        }
    return out


def collect_versions() -> dict[str, dict]:
    """扫描当前代码/配置，返回 {name: {sha256, stamp, ...}} 版本清单。"""
    versions: dict[str, dict] = {}
    versions.update(_collect_prompts())
    versions.update(_collect_config())
    return versions


def diff_manifests(prev: dict | None, cur: dict) -> dict[str, list[str]]:
    """对比两份版本清单，返回 {added, removed, changed}（name 列表为 sha256 变化）。

    * added：新增被追踪项；
    * removed：旧清单有、当前没了（源被删/改名）；
    * changed：内容哈希变了 → 该 prompt / config 字段发生实质变更。
    """
    prev = prev or {}
    added = sorted(set(cur) - set(prev))
    removed = sorted(set(prev) - set(cur))
    changed = sorted(
        k for k in set(prev) & set(cur)
        if prev[k].get("sha256") != cur[k].get("sha256")
    )
    return {"added": added, "removed": removed, "changed": changed}
