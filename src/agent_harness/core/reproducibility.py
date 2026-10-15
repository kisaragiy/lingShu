"""可复现信封（Reproducibility Envelope）— 让每一次 LLM 调用可被复现/重放。

痛点（P1-1）：同一输入两次调用拿到不同输出，线上事故无法复现。三类根因：

1. **采样非确定性** —— temperature / top_p / seed 未落档，无法重放。
2. **batch 数值差异** —— 同一 prompt 在不同 batch / 不同后端数值不一致，
   需要 `system_fingerprint`（后端指纹）+ `model_version`（权重版本）来区分。
3. **外部状态** —— 不同工具集（tools）或不同系统提示会产生不同行为，
   需要 `prompt_hash` / `tools_hash` 固定"到底喂了什么"。

本模块只做**纯函数**：从调用参数算出信封并做稳定哈希。它不发起任何网络调用、
不读写全局可变状态、缺字段一律用 `None` 兜底（向后兼容，绝不抛异常）。
哈希一律用 `hashlib.sha256` + **稳定序列化**（`sort_keys` + 固定分隔符），
**绝不使用内置 `hash()`**（受 `PYTHONHASHSEED` 影响，跨进程/跨机器不稳定）。

字段名同时向 OpenTelemetry GenAI 语义约定对齐（P1-4），见 `OTEL_GEN_AI_MAP`
与 `envelope_record()` —— 过渡期**新旧两种键一并输出**，不破坏现有读者。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

# ── 信封字段（顺序固定：便于人工比对与测试断言）─────────────
ENVELOPE_FIELDS: tuple[str, ...] = (
    "model",
    "model_version",
    "provider",
    "seed",
    "temperature",
    "system_fingerprint",
    "prompt_hash",
    "tools_hash",
)

# ── 旧键 → OTel GenAI 语义约定键（P1-4）───────────────────
# 前 5 条为 OTel 稳定约定；response.* / request.seed / *_hash 为 harness 扩展
# （OTel 尚无对应字段，沿用 gen_ai 命名空间以示归属）。
OTEL_GEN_AI_MAP: dict[str, str] = {
    "provider": "gen_ai.system",
    "model": "gen_ai.request.model",
    "model_version": "gen_ai.response.model",
    "temperature": "gen_ai.request.temperature",
    "seed": "gen_ai.request.seed",
    "system_fingerprint": "gen_ai.response.system_fingerprint",
    "prompt_hash": "gen_ai.request.prompt_hash",
    "tools_hash": "gen_ai.request.tools_hash",
}

# usage（token）旧键 → OTel 键
OTEL_USAGE_MAP: dict[str, str] = {
    "prompt_tokens": "gen_ai.usage.input_tokens",
    "completion_tokens": "gen_ai.usage.output_tokens",
}


# ── 稳定哈希 ──────────────────────────────────────────────

def stable_hash(obj: Any) -> str:
    """对任意可 JSON 化对象做**稳定** sha256（跨进程 / 跨机器一致）。

    - `sort_keys=True` 消除 dict 键序差异；
    - 固定 `separators` 消除空白差异；
    - `default=str` 兜底不可序列化对象，**不抛异常**。

    绝不使用内置 `hash()`：它受 `PYTHONHASHSEED` 随机化影响，同一对象在不同
    进程会得到不同值——用它做"可复现"的键是自欺。
    """
    try:
        raw = json.dumps(
            obj, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), default=str,
        )
    except (TypeError, ValueError):
        raw = str(obj)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hash_prompt(messages: list[dict] | None, system_prompt: str | None = None) -> str:
    """prompt 指纹：system 提示 + 消息序列。空输入也有确定值（不抛异常）。"""
    return stable_hash({"system": system_prompt or "", "messages": messages or []})


def hash_tools(tools: list | None) -> str:
    """tools（工具 schema）指纹。`None` 与 `[]` 都视为"无工具"，得到同一哈希。"""
    return stable_hash(tools or [])


# ── 信封构造 ──────────────────────────────────────────────

def reproducibility_envelope(
    *,
    model: str | None = None,
    model_version: str | None = None,
    provider: str | None = None,
    seed: int | None = None,
    temperature: float | None = None,
    system_fingerprint: str | None = None,
    messages: list[dict] | None = None,
    system_prompt: str | None = None,
    tools: list | None = None,
    prompt_hash: str | None = None,
    tools_hash: str | None = None,
) -> dict:
    """构造一次 LLM 调用的**可复现信封**。

    字段齐全、缺一律 `None`（向后兼容：老调用方只传 model 也不会报错）。
    若未显式给出 `prompt_hash` / `tools_hash`，则从 `messages` / `tools` 现算。
    """
    return {
        "model": model,
        "model_version": model_version,
        "provider": provider,
        "seed": seed,
        "temperature": temperature,
        "system_fingerprint": system_fingerprint,
        "prompt_hash": prompt_hash if prompt_hash is not None
        else (hash_prompt(messages, system_prompt) if (messages or system_prompt) else None),
        "tools_hash": tools_hash if tools_hash is not None
        else (hash_tools(tools) if tools else None),
    }


# ── OTel 对齐输出 ─────────────────────────────────────────

def otel_attributes(envelope: dict | None) -> dict:
    """把信封翻译成 OTel GenAI 键（`gen_ai.*`）。空值（None）跳过。"""
    out: dict[str, Any] = {}
    for old, new in OTEL_GEN_AI_MAP.items():
        val = (envelope or {}).get(old)
        if val is not None:
            out[new] = val
    return out


def otel_usage_attributes(usage: dict | None) -> dict:
    """把 usage（token 数）翻译成 OTel 键。容忍 None 与缺失字段。"""
    out: dict[str, Any] = {}
    u = usage or {}
    for old, new in OTEL_USAGE_MAP.items():
        val = u.get(old)
        if val is not None:
            out[new] = val
    return out


def envelope_record(envelope: dict | None, usage: dict | None = None) -> dict:
    """过渡期统一记录体：**同时含旧键与 gen_ai.* 新键**。

    现有读者按旧键（`model`、`prompt_tokens`…）解析的方法不变；新读者可直接按
    OTel 约定取 `gen_ai.*`。两者并存是刻意的：一次性改名会打断所有现有消费者。
    """
    record: dict[str, Any] = {}
    env = envelope or {}
    for k in ENVELOPE_FIELDS:
        record[k] = env.get(k)
    record.update(otel_attributes(env))

    u = usage or {}
    prompt_t = u.get("prompt_tokens")
    completion_t = u.get("completion_tokens")
    record["prompt_tokens"] = prompt_t
    record["completion_tokens"] = completion_t
    record.update(otel_usage_attributes(u))
    return record
