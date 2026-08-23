"""Reasoning enhancement tools — self-consistency, reflection, verification.

I call these when I need better reasoning quality:
- reason_ensemble: when uncertain, sample multiple chains → vote
- reflect_and_learn: when corrected, record root cause → prevent repeat
"""
import json
import os
import sys
import random

HARNESS_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, HARNESS_DIR)

from ..pipeline.llm import call_llama

# ═══════════════════════════════════════════
# Self-Consistency: sample N reasoning chains → vote
# ═══════════════════════════════════════════

def _tool_reason_ensemble(question: str, num_samples: int = 3) -> str:
    """Self-consistency reasoning — sample multiple chains, return the most consistent answer.

    I call this when the question has ambiguity and I want to reduce guess error.
    
    Args:
        question: the ambiguous question or decision
        num_samples: how many reasoning chains to sample (3-5)
    
    Returns:
        JSON with: sampled_chains, consensus_answer, confidence_level
    """
    if not question or not question.strip():
        return json.dumps({"ok": False, "error": "question is empty"})

    chains = []
    temps = [0.3, 0.5, 0.7]  # different temperatures for diversity
    seeds = [random.randint(1, 9999) for _ in range(num_samples)]

    for i in range(num_samples):
        try:
            prompt = f"""请逐步推理以下问题，输出你的思考过程，然后给出最终答案。

问题: {question}

格式:
推理过程:
1. ...
2. ...
最终答案: ..."""
            raw, _ = call_llama(
                [{"role": "user", "content": prompt}],
                system_prompt="你是严谨的推理者。先逐步推理，再给出答案。",
                temperature=temps[i % len(temps)],
                seed=seeds[i],
            )
            chains.append({"sample": i, "temperature": temps[i % len(temps)], "raw": raw.strip()[:500]})
        except Exception as e:
            chains.append({"sample": i, "error": str(e)[:100]})

    # Extract final answers from chains and find consensus
    import re
    final_answers = []
    for c in chains:
        if "raw" not in c:
            continue
        m = re.search(r"最终答案[：:]\s*(.+?)(?:\n|$)", c["raw"])
        if m:
            final_answers.append(m.group(1).strip()[:100])
    
    # Count frequency
    from collections import Counter
    answer_counts = Counter(final_answers) if final_answers else Counter()
    
    result = {
        "ok": True,
        "question": question[:100],
        "num_samples": num_samples,
        "chains": chains,
        "final_answers": final_answers,
        "consensus": answer_counts.most_common(1)[0][0] if answer_counts else None,
        "confidence": round(answer_counts.most_common(1)[0][1] / num_samples, 2) if answer_counts else 0.0,
    }
    
    return json.dumps(result, indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════
# Reflection: when corrected → record root cause
# ═══════════════════════════════════════════

def _tool_reflect(what_happened: str, root_cause: str, lesson: str, context: str = "") -> str:
    """Record a mistake/correction and learn from it.

    I call this immediately when I realize I made an error or was corrected.
    It writes to both fact_store (durable) and memory (for future sessions).
    
    Args:
        what_happened: what I did wrong
        root_cause: why it happened
        lesson: what to do differently next time
        context: optional session/task context
    
    Returns:
        confirmation with fact_id if stored
    """
    if not what_happened or not root_cause:
        return json.dumps({"ok": False, "error": "what_happened and root_cause are required"})

    # Build knowledge entry
    entry = f"教训记录: {lesson}\n上下文: {what_happened}\n根因: {root_cause}"
    if context:
        entry += f"\n场景: {context}"

    # Try to write to fact_store
    fact_id = None
    try:
        from agent_harness.core.holographic import fact_store
        result = fact_store(action="add", category="general", content=entry, tags="教训,反思,方法论")
        fact_id = result.get("fact_id")
    except Exception as e:
        pass  # fact_store may not be available

    # Also try to write to memory
    try:
        from hermes_tools import memory
        memory(action="add", content=f"教训 ({lesson[:60]}): {what_happened[:80]} → 根因: {root_cause[:80]}", target="memory")
    except Exception:
        pass  # memory may not be available in current context

    return json.dumps({
        "ok": True,
        "fact_id": fact_id,
        "lesson": lesson[:200],
        "what_happened": what_happened[:200],
        "root_cause": root_cause[:200],
        "message": "✅ 教训已记录，下次不会重复犯同类错",
    }, ensure_ascii=False)


# ═══════════════════════════════════════════
# Verification: check evidence supports a claim
# ═══════════════════════════════════════════

def _tool_verify(claim: str, evidence: list) -> str:
    """Verify a claim against evidence before delivering it.

    I call this after getting search/analysis results, before giving the final answer.
    
    Args:
        claim: the statement I'm about to make
        evidence: list of supporting pieces (search results, data points)
    
    Returns:
        JSON: {verified, confidence, gaps}
    """
    if not claim:
        return json.dumps({"ok": False, "error": "claim is required"})

    try:
        prompt = f"""验证以下声明的准确性，基于提供的证据。

声明: {claim}

证据:
{json.dumps(evidence, ensure_ascii=False, indent=2) if evidence else "无证据"}

请判断:
1. 声明是否被证据充分支持？(yes/no/partial)
2. 置信度 (0-1)
3. 如果未被支持，缺少什么？
4. 修正后的声明（如有必要）

输出 JSON:
{{"supported": "yes|no|partial", "confidence": 0.0-1.0, "gaps": [...], "corrected_claim": "..."}}"""
        
        raw, _ = call_llama(
            [{"role": "user", "content": prompt}],
            system_prompt="你是严谨的事实核查员。只输出 JSON，不加解释。",
        )
        import re as _re
        m = _re.search(r"\{.*\}", raw, _re.DOTALL)
        if m:
            return json.dumps(json.loads(m.group(0)), indent=2, ensure_ascii=False)
    except Exception:
        pass

    return json.dumps({"ok": False, "error": "verification failed"}, ensure_ascii=False)


# ═══════════════════════════════════════════
# Registration
# ═══════════════════════════════════════════

from .registry import register_tool

register_tool("reason_ensemble", _tool_reason_ensemble, {
    "description": "🧠 自一致性推理 — 采样多条推理链投票得到最可靠答案。有歧义或重要决策时调用。参数: question=问题, num_samples=采样数(默认3)",
    "properties": {
        "question": {"type": "string", "description": "需要推理的问题"},
        "num_samples": {"type": "integer", "description": "采样链数量(3-5)"},
    },
}, privilege="read-only")

register_tool("reflect", _tool_reflect, {
    "description": "📝 反思沉淀 — 出错或被纠正时调用，自动记录根因到 fact_store + memory，防止同类错误重复。参数: what_happened=做了什么错, root_cause=根因, lesson=下次怎么做",
    "properties": {
        "what_happened": {"type": "string", "description": "做错了什么"},
        "root_cause": {"type": "string", "description": "根本原因"},
        "lesson": {"type": "string", "description": "下次怎么做"},
        "context": {"type": "string", "description": "场景上下文"},
    },
}, privilege="read-only")

register_tool("verify", _tool_verify, {
    "description": "✅ 事实核查 — 交付结论前用证据验证声明准确性。参数: claim=声明, evidence=证据列表",
    "properties": {
        "claim": {"type": "string", "description": "要验证的声明"},
        "evidence": {"type": "array", "description": "支撑证据列表", "items": {"type": "string"}},
    },
}, privilege="read-only")