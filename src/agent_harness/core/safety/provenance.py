"""
外源内容溯源与不可信标记 — 间接提示注入防护（OWASP ASI06）

痛点：指令与数据在 context 里同为 token。agent 读到的网页 / 工具输出 /
另一个 agent 的响应里的恶意指令，与用户输入无法区分 —— 一旦被当作"事实"
写进持久记忆/状态，就完成了持久化投毒。

对策（本模块只做"标记"这一半，裁决在 admission.py）：
  1. 给流经系统的内容附上**来源 + 信任级**（Provenance）；
  2. 对**外源工具**（web 检索/抓取、RAG 召回等）的产出打 ``untrusted``；
  3. 未标记内容一律 ``unknown``（不默认可信）。

设计原则：**只加不改**。本模块不触碰任何现有调用回路 —— 想要标记一个
工具结果，调用方显式调用 :func:`tag_tool_result`；本模块不在 import 期注册
任何钩子（见 admission.install_memory_admission 的显式启用）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# ─── 信任级 ───
TRUSTED = "trusted"      # 可信：用户直接输入、我方内部生成
UNTRUSTED = "untrusted"  # 不可信：外源内容（网页/检索/外部响应）
UNKNOWN = "unknown"      # 未标记：默认既不可信也不放行（由裁决层保守处理）

_VALID_TRUST = (TRUSTED, UNTRUSTED, UNKNOWN)

# ─── 外源工具（产出默认不可信） ───
# 这些工具的返回来自我方之外（互联网 / 第三方），其文本可能内含指令。
EXTERNAL_SOURCE_TOOLS: frozenset[str] = frozenset({
    "search", "fetch", "web_browse", "web_scrape", "agent_browser",
    "rag_query",          # 召回的是此前导入的外源文本
    "query_decompose",
    "github_issues",      # 第三方仓库内容
    "stock_search",       # 第三方财经文本
})


@dataclass(frozen=True)
class Provenance:
    """一条内容的来源与信任级。"""

    trust: str = UNKNOWN
    origin: str = ""          # 例：user / tool:search / agent:worker
    tool: str = ""            # 产出该内容的工具名（如来自工具）
    detail: str = ""          # 自由说明
    marked_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def is_untrusted(self) -> bool:
        return self.trust == UNTRUSTED

    @property
    def is_trusted(self) -> bool:
        return self.trust == TRUSTED


@dataclass(frozen=True)
class TaggedContent:
    """被标记的内容信封。

    ``content`` 保持原样，不被改写；信任信息随行。字符串化只回内容本身，
    避免信任元数据悄悄混进下游 prompt。
    """

    content: Any
    provenance: Provenance

    def __str__(self) -> str:  # noqa: D105
        return str(self.content)

    def __repr__(self) -> str:  # noqa: D105
        return f"TaggedContent(trust={self.provenance.trust!r}, origin={self.provenance.origin!r})"


# ─── 标记入口 ───

def mark_untrusted(content: Any, *, origin: str = "external", tool: str = "",
                   detail: str = "") -> TaggedContent:
    """把一段内容标为**不可信**（外源）。"""
    return TaggedContent(
        content=content,
        provenance=Provenance(trust=UNTRUSTED, origin=origin, tool=tool, detail=detail),
    )


def mark_trusted(content: Any, *, origin: str = "user", tool: str = "",
                 detail: str = "") -> TaggedContent:
    """把一段内容标为**可信**（用户直接输入 / 我方内部生成）。"""
    return TaggedContent(
        content=content,
        provenance=Provenance(trust=TRUSTED, origin=origin, tool=tool, detail=detail),
    )


# ─── 读取 / 拆封 ───

def is_tagged(obj: Any) -> bool:
    """是否已被标记。"""
    return isinstance(obj, TaggedContent)


def trust_of(obj: Any) -> str:
    """取出信任级。未标记内容返回 :data:`UNKNOWN`（不默认可信）。"""
    if isinstance(obj, TaggedContent):
        return obj.provenance.trust
    return UNKNOWN


def provenance_of(obj: Any) -> Provenance | None:
    """取出溯源信息；未标记返回 ``None``。"""
    if isinstance(obj, TaggedContent):
        return obj.provenance
    return None


def unwrap(obj: Any) -> Any:
    """拆封取出原始内容；未标记则原样返回。"""
    if isinstance(obj, TaggedContent):
        return obj.content
    return obj


# ─── 工具结果分类 ───

def is_external_source(tool_name: str) -> bool:
    """该工具是否属于外源（其产出默认不可信）。"""
    return tool_name in EXTERNAL_SOURCE_TOOLS


def classify_tool_result(tool_name: str, content: Any) -> TaggedContent:
    """按工具来源给结果定信任级。

    外源工具 → ``untrusted``；其余（我方内部工具）→ ``trusted``。
    """
    if is_external_source(tool_name):
        return mark_untrusted(content, origin=f"tool:{tool_name}", tool=tool_name)
    return mark_trusted(content, origin=f"tool:{tool_name}", tool=tool_name)


# 兼容别名（"tag" = classify）
tag_tool_result = classify_tool_result
