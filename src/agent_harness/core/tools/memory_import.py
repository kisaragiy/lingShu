"""
记忆跨产品导入器 — 从其他 AI 产品的对话/输出中提取结构化知识

支持来源: ChatGPT, DeepSeek, Claude, Kimi, Gemini, 通用文本

流程:
  1. 接收原始文本 (从其他 AI 复制粘贴)
  2. 本地 LLM (call_llama) 提取结构化知识点 {topic, content, tags[]}
  3. 调用 rag_index 存入 RAG 知识库
  4. 返回摘要 (提取数/存入数/话题列表)
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..pipeline.llm import call_llama


# ─── 导入提示词 ───

_IMPORT_PROMPT = """你是一个知识提取器。用户提供了来自 AI 对话/输出的文本，请从中提取有价值的、可复用的知识点。

提取规则:
1. 每条知识点 = {topic, content, tags} 格式
   - topic: 简短主题（10字以内）
   - content: 知识点正文（30-100字，保留关键细节、数据、步骤、结论）
   - tags: 标签数组（3-5个，用中文，如 ["AI工程", "架构", "数据"]）
2. 只提取有实际信息量的点（事实、步骤、配置、参数、结论）
   - 过滤掉寒暄、客套、空泛鼓励
   - 过滤掉明显不完整/无意义的片段
3. 每条独立，不合并，不重复
4. 如果输入完全无价值内容，返回 {"points": []}
5. 输出严格 JSON 格式: {"source": "推断的来源", "summary": "一句话摘要", "points": [...]}

用户的文本:
---
{text}
---

请直接输出 JSON，不加任何其他文字。"""


def import_text(text: str, source: str = "", collection: str = "memory") -> dict:
    """导入一段来自其他 AI 产品的文本到知识库。

    Args:
        text: 原始文本（从其他 AI 复制）
        source: 来源标识 (ChatGPT, DeepSeek, Claude, 通用 等)
        collection: 目标知识库集合名（默认 "memory"）

    Returns:
        {"ok": True, "points_count": N, "inserted": N,
         "summary": "...", "topics": [...]}
        或 {"ok": False, "error": "..."}
    """
    if not text or not text.strip():
        return {"ok": False, "error": "文本为空"}

    # 1. LLM 提取结构化知识点
    try:
        prompt = _IMPORT_PROMPT.format(text=text[:8000])
        raw, _ = call_llama(
            [{"role": "user", "content": prompt}],
            system_prompt="你是知识提取器，只输出 JSON，不加解释。",
        )
    except Exception as e:
        return {"ok": False, "error": f"LLM 提取失败: {e}"}

    # 2. 解析 JSON
    parsed = _parse_json_output(raw)
    if not parsed or "points" not in parsed:
        return {"ok": False, "error": "LLM 返回格式异常，未能提取知识点"}

    points = parsed.get("points", [])
    if not points:
        return {"ok": True, "points_count": 0, "inserted": 0,
                "summary": parsed.get("summary", "未提取到知识点"), "topics": []}

    # 3. 存入 RAG 知识库
    from .registry import call_tool
    inserted = 0
    errors = []
    for pt in points:
        topic = (pt.get("topic") or "").strip()
        content = (pt.get("content") or "").strip()
        tags = pt.get("tags", [])
        if not topic or not content:
            continue
        chunk_text = f"## {topic}\n\n{content}\n\n来源: {source}\n标签: {', '.join(tags[:5])}"
        try:
            r = call_tool("rag_index",
                          text=chunk_text,
                          source=f"memory-import/{source}" if source else "memory-import",
                          collection=collection,
                          _source="harness")
            if r.get("success"):
                inserted += 1
            else:
                errors.append(f"{topic}: {r.get('error', '索引失败')}")
        except Exception as e:
            errors.append(f"{topic}: {e}")

    topics = [p.get("topic", "") for p in points if p.get("topic")]

    result = {
        "ok": True,
        "points_count": len(points),
        "inserted": inserted,
        "summary": parsed.get("summary", f"提取了 {len(points)} 条知识点"),
        "topics": topics,
        "source": source or parsed.get("source", "未知"),
        "collection": collection,
    }
    if errors:
        result["errors"] = errors
    return result


def _parse_json_output(raw: str) -> dict | None:
    """从 LLM 输出中提取 JSON（可能包含多余文本）。"""
    # 尝试直接解析
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 尝试提取 ```json ... ``` 块
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试提取第一个 { ... } 块
    m = re.search(r"(\{.*\})", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    return None