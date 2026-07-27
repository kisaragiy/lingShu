"""
降级矩阵 — 定义每个功能的降级链。

用法:
    from agent_harness.core.degradation import call_with_degradation
    result = call_with_degradation("image_analysis", image="ref.png")
"""
from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Callable

logger = logging.getLogger(__name__)


class DegradedResult:
    """降级结果 —— 功能不可用时返回，不是抛异常。"""

    def __init__(self, capability: str, reason: str, data: Any = None):
        self.capability = capability
        self.reason = reason
        self.data = data
        self.degraded = True

    def __bool__(self) -> bool:
        return False


# ── 降级矩阵 ──────────────────────────────────────
# 每个功能定义降级链：preferred → fallback_N → on_failure

DEGRADATION_MATRIX: dict[str, dict[str, Any]] = {
    "image_analysis": {
        "description": "参考图角色特征/画风分析",
        "qwen3-vl:8b": "vlm_analyze_character",
        "fallback_1": "ollama_text_only",
        "fallback_2": "use_user_description",
        "on_failure": "return_empty_with_warning",
    },
    "aesthetic_scoring": {
        "description": "VLM 审美评分",
        "qwen3-vl:8b": "aesthetic_scorer.score",
        "fallback_1": "skip_scoring",
        "on_failure": "pass_through",
    },
    "comfyui_generation": {
        "description": "ComfyUI 图片生成",
        "local_8188": "comfyui_api.generate",
        "fallback_1": "queue_for_retry",
        "on_failure": "report_unavailable",
    },
    "search": {
        "description": "网络搜索",
        "searxng": "search_chain.search",
        "fallback_1": "duckduckgo_direct",
        "fallback_2": "use_agent_knowledge",
        "on_failure": "return_empty",
    },
    "llm_inference": {
        "description": "LLM 推理",
        "deepseek_flash": "model_proxy.chat",
        "fallback_1": "ollama_qwen3_14b",
        "fallback_2": "ollama_qwen3_1_7b",
        "on_failure": "report_unavailable",
    },
}


def get_degradation_chain(capability: str) -> list[str]:
    """返回降级链（有序列表）。"""
    matrix = DEGRADATION_MATRIX.get(capability)
    if not matrix:
        return []
    chain = []
    for key, val in matrix.items():
        if key in ("description",) or key.startswith("_"):
            continue
        chain.append(f"{key}:{val}")
    return chain


def call_with_degradation(
    capability: str,
    preferred_fn: Callable | None = None,
    fallback_fns: list[Callable] | None = None,
    on_failure: Callable | None = None,
    **kwargs: Any,
) -> Any:
    """按降级链依次尝试，全失败则返回 DegradedResult。

    Args:
        capability: 功能名，用于日志
        preferred_fn: 首选函数
        fallback_fns: 降级函数列表（按优先级）
        on_failure: 全失败时调用的函数
        **kwargs: 传给所有函数的参数
    """
    fns = []
    if preferred_fn:
        fns.append(("preferred", preferred_fn))
    if fallback_fns:
        for i, fn in enumerate(fallback_fns):
            fns.append((f"fallback_{i + 1}", fn))

    for label, fn in fns:
        try:
            result = fn(**kwargs)
            logger.info(
                "degradation_ok",
                extra={"capability": capability, "level": label},
            )
            return result
        except Exception as e:
            logger.warning(
                "degradation_skip",
                extra={"capability": capability, "level": label, "error": str(e)},
            )
            continue

    # 全失败
    if on_failure:
        try:
            return on_failure(**kwargs)
        except Exception as e:
            logger.error(
                "degradation_all_failed",
                extra={"capability": capability, "error": str(e)},
            )

    logger.error("degradation_exhausted", extra={"capability": capability})
    return DegradedResult(capability, "all_fallbacks_exhausted")
