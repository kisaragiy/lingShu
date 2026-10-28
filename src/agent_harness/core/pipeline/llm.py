"""LLM utilities — shim for backward compatibility with tools/ modules.

The tools were originally written against the ~/.openclaw/workspace/harness/
project structure and import from `pipeline.llm`. This module provides
the same interface using the new agent_harness package.
"""

import json
import os
import sys

import requests as req_lib

from ..config import (
    CLOUD_API_KEY,
    HARNESS_DIR,
    LLAMA_API,
    MODEL_DEEPSEEK,
    MODEL_LLAMA,
    get_llm_config,
)
from ..retry import with_retry

# Re-export for tools/ compatibility
WORKSPACE_DIR = os.path.normpath(os.path.join(str(HARNESS_DIR), "..", ".."))

# ─── Token ledger (circuit-breaker feed) ───
# All LLM call sites bump this on successful responses; the graph layer reads
# deltas between rounds and feeds them into CircuitBreaker.add_tokens().
_LLM_TOKEN_LEDGER: dict[str, int] = {"total": 0}


def ledger_total() -> int:
    """Current cumulative token count across all LLM call sites."""
    return _LLM_TOKEN_LEDGER["total"]


def ledger_bump(tokens: int) -> None:
    """Record tokens consumed by a successful LLM call."""
    if tokens and tokens > 0:
        _LLM_TOKEN_LEDGER["total"] += tokens


# ─── Retried HTTP POST (wire retry into the LLM path) ───
@with_retry(
    max_attempts=3,
    base_delay=1.0,
    max_delay=30.0,
    retryable_exceptions=(req_lib.exceptions.RequestException, OSError, TimeoutError),
)
def _http_post(session, url, payload, headers=None, timeout=120):
    """Single POST with exponential-backoff retry. Raises after attempts exhausted."""
    return session.post(url, json=payload, headers=headers or {}, timeout=timeout)


# ─── Thinking 模式策略（业界做法：不是禁思考，是「按需开 + 给够预算」）───
# HARNESS_LLM_THINKING = off（默认，保持既有行为）| on
#   off → payload 里显式发 thinking:{"type":"disabled"}（关闭思考）
#   on  → 不发该字段，让模型自主思考；并把 max_tokens 抬到至少 4096 给思考留空间
# 无论哪种模式，都会识别「思考把预算吃光」并按预算重试一次（见 _THINKING_MAX_RETRY）。
THINKING_MODE_ENV = "HARNESS_LLM_THINKING"
THINKING_MIN_BUDGET = 4096          # 开思考时 max_tokens 下限（思考 + 答案要一起装下）
THINKING_RETRY_CAP = 8192           # 预算重试的 max_tokens 上限
_REASONING_FIELDS = ("reasoning_content", "reasoning", "thinking")


def _thinking_mode() -> str:
    """当前思考模式：'on' 或 'off'（默认 off，不改变既有部署行为）。"""
    return "on" if os.environ.get(THINKING_MODE_ENV, "").strip().lower() in ("on", "1", "true", "yes") else "off"


def _extract_reasoning(msg: dict) -> str:
    """取思考轨迹。各家字段名不同：DeepSeek/vLLM=reasoning_content、
    ollama 新版 /v1=reasoning、Anthropic 风格=thinking。"""
    for field in _REASONING_FIELDS:
        value = msg.get(field)
        if isinstance(value, str) and value:
            return value
    return ""


def _extract_openai_content(data: dict, max_tokens: int) -> tuple[str, str, str, bool]:
    """从 OpenAI 兼容响应里取 (答案, 思考轨迹, finish_reason, 是否可缓存)。

    思考轨迹与答案走两条通道，**永远不合并**：
      - content 有值 → 正常答案；reasoning 单独交还（可记日志/展示「思考过程」）
      - content 空 + reasoning 已吃满预算（R1 系答案写在推理里）→ 标 [推理截断输出]，不缓存
      - content 空 + finish=length + 思考没吃满 → 预算卡在思考中途，答案还没开始写；
        返回空串，由调用方决定加预算重试（不把思考当答案，也不假装成功）

    分支顺序有讲究：「吃满预算」必须排在「finish=length」**前面**。R1 系模型思考吃光
    预算时 finish 必然是 length，反过来的顺序会让 [推理截断输出] 成为不可达死代码，
    于是这类响应只会永远返回空串（2026-10-23 由 cassette 夹具跑出来）。
    """
    try:
        choice = data["choices"][0]
    except (KeyError, IndexError, TypeError):
        return "", "", "", False
    msg = choice.get("message") or {}
    content = (msg.get("content") or "").strip()
    reasoning = _extract_reasoning(msg)
    finish = choice.get("finish_reason") or ""
    if content:
        return content, reasoning, finish, True
    if reasoning and len(reasoning) > max_tokens * 0.8:
        return "[推理截断输出] " + reasoning[-200:], reasoning, finish, False
    if reasoning and finish == "length":
        return "", reasoning, finish, False
    return "", reasoning, finish, True


# ─── 其它 LLM 调用点唯一允许的入口 ───
# 背景：supervisor / graph / comic_agent / cs_agent 曾各自 import config 模块常量
# （进程启动时的快照）并各自用 `content or reasoning[-500:]` 兜底。实测一次调用串里
# 同时出现 mock / 127.0.0.1:18888 / qwen3.5:9b 三种后端，报告里真实分析与占位内容
# 混在一起 —— 看起来完整、实际不可核对。下面三个函数是这些点的统一入口。

def runtime_endpoint() -> tuple[str, str]:
    """返回当前 (端点, 模型)。每次调用重读配置，不吃 import 快照。"""
    cfg = get_llm_config() or {}
    return str(cfg.get("api_url") or ""), str(cfg.get("model") or "")


def extract_answer(data: dict, max_tokens: int = 2048) -> str:
    """从 OpenAI 兼容响应里取答案。思考轨迹永不冒充答案（答案空就返回空）。"""
    return _extract_openai_content(data, max_tokens)[0]


def runtime_auth_headers(api_url: str) -> dict[str, str]:
    """Use the current key only for its configured endpoint; never expose it in logs."""
    cfg = get_llm_config() or {}
    key = cfg.get("api_key") or ""
    if key and cfg.get("api_url") == api_url:
        return {"Authorization": f"Bearer {key}"}
    return {}


def thinking_payload(config: dict | None = None) -> dict:
    """Translate the thinking switch to the configured provider's request format."""
    if _thinking_mode() == "on":
        return {}
    cfg = config if config is not None else get_llm_config() or {}
    if cfg.get("backend") == "ollama":
        return {"reasoning_effort": "none"}
    return {"thinking": {"type": "disabled"}}


# Shared HTTP session
_session = req_lib.Session()
_session.headers.update({"User-Agent": "agent-harness/0.2.0"})

# LLM response cache — in-memory, TTL-based
_LLM_CACHE: dict[str, tuple[float, str, int]] = {}  # key → (timestamp, content, tokens)
_LLM_CACHE_TTL = 60  # seconds

def _llm_cache_key(model: str, messages: list[dict], system_prompt: str, temperature: float,
                   thinking: str | None = None, max_tokens: int | None = None) -> str:
    """Generate cache key from deterministic parameters.

    ENG-24：命中缓存的唯一前提是「请求体完全相同」。thinking 开关会改变请求体
    （off → 发 {"thinking":{"type":"disabled"}}，on → 不发该字段）并连带把
    max_tokens 抬到 THINKING_MIN_BUDGET 下限，两条路径的**响应不同**；
    不把这两维纳入 key，后跑的一侧会命中前一侧的缓存、拿到别的配置的结果。
    thinking 显式传 None 时回退到当前环境（本文件的缓存写入口都显式传参）。
    """
    import hashlib
    mode = _thinking_mode() if thinking is None else thinking
    raw = json.dumps(
        [model, messages, system_prompt, round(temperature, 2), mode, max_tokens],
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()

def _llm_cache_get(key: str) -> tuple[str, int] | None:
    """Get cached result if not expired."""
    import time
    entry = _LLM_CACHE.get(key)
    if entry and time.time() - entry[0] < _LLM_CACHE_TTL:
        return entry[1], entry[2]
    if entry:
        del _LLM_CACHE[key]
    return None

def _llm_cache_set(key: str, content: str, tokens: int):
    """Set cache entry."""
    import time
    if len(_LLM_CACHE) > 500:  # evict oldest if full
        oldest = min(_LLM_CACHE.keys(), key=lambda k: _LLM_CACHE[k][0])
        del _LLM_CACHE[oldest]
    _LLM_CACHE[key] = (time.time(), content, tokens)

CENSORED_SYSTEM_PROMPT = "你是一个内容安全助手。对于色情、暴力、违法内容，回复'我无法处理该请求'。"


def _record_llm_metrics(model: str, usage: dict | None, success: bool, total: int = 0) -> None:
    """把一次**真实** LLM 调用记进指标 —— token 消耗是成本控制的唯一依据。

    只记真实调用：缓存命中不算（否则 calls_total 与 token 数虚高）。
    观测层故障绝不允许影响 LLM 调用本身，故整个函数被 try 吞住。
    """
    try:
        from ..metrics import record_llm_call
        u = usage or {}
        prompt_t = int(u.get("prompt_tokens", 0) or 0)
        completion_t = int(u.get("completion_tokens", 0) or 0)
        if not prompt_t and not completion_t:
            completion_t = int(total or 0)  # 上游只给 total 时归到 completion 侧，不能丢
        record_llm_call(model, prompt_t, completion_t, success)
    except Exception:
        pass


def _emit_llm_envelope(model: str, messages: list[dict] | None = None,
                       system_prompt: str = "", temperature: float | None = None,
                       usage: dict | None = None, seed: int | None = None,
                       data: dict | None = None) -> None:
    """落一次 LLM 调用的可复现信封（P1-1 / P1-4），供日志/trace 排查复现。

    观测层故障绝不允许影响 LLM 调用本身 —— 整函数 try 吞住。
    信封字段可从上游响应（`data`）补 model_version / system_fingerprint。
    """
    try:
        from ..agent_log import log_llm_envelope
        from ..reproducibility import reproducibility_envelope
        resp = data or {}
        env = reproducibility_envelope(
            model=model,
            model_version=resp.get("model") or resp.get("model_version"),
            provider=resp.get("provider"),
            seed=seed,
            temperature=temperature,
            system_fingerprint=resp.get("system_fingerprint"),
            messages=messages,
            system_prompt=system_prompt,
        )
        log_llm_envelope(None, env, usage)
    except Exception:
        pass



def call_llama(messages: list[dict], system_prompt: str = "",
               max_tokens: int = 2048, temperature: float = 0.3) -> tuple[str, int]:
    """Call local LLaMA model. Returns (text, tokens_used)."""
    # 运行时读取：用户在设置页填的 api_url/model 立即生效，不再依赖 import 快照
    _cfg = get_llm_config()
    mode = _thinking_mode()
    # 实际发出的模型名（_cfg 覆盖时常量与 MODEL_LLAMA 不同，key 必须跟请求体一致）
    _model = _cfg.get("model") or MODEL_LLAMA
    # 开思考时把预算抬到下限 —— 思考与答案共用 max_tokens，预算不够时答案一个 token 都轮不到
    budget = max(max_tokens, THINKING_MIN_BUDGET) if mode == "on" else max_tokens
    # Check cache —— key 带上 thinking 与预算：同消息不同配置的结果不可互换（ENG-24）
    cache_key = _llm_cache_key(
        _model, [{"role": "system", "content": system_prompt}] + messages, system_prompt,
        temperature, thinking=mode, max_tokens=budget,
    ) if temperature <= 0.5 else None
    if cache_key:
        cached = _llm_cache_get(cache_key)
        if cached:
            return cached
    payload = {
        "model": _model,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "max_tokens": budget,
        "temperature": temperature,
        "stream": False,
    }
    if mode != "on":
        # 只在「本次不希望思考」时显式关闭；开思考时不发这个字段，让模型自主决定
        payload.update(thinking_payload(_cfg))
    _api_url = _cfg.get("api_url") or LLAMA_API
    try:
        resp = _http_post(_session, _api_url, payload,
                          headers=runtime_auth_headers(_api_url), timeout=300)
        if resp.status_code == 200:
            data = resp.json()
            content, reasoning, finish, content_cacheable = _extract_openai_content(data, budget)
            # 思考把预算吃光（content 空 + finish=length + 有 reasoning）→ 加预算重试一次。
            # 业界做法：thinking 模型的 max_tokens 必须同时装下思考与答案；不重试就只能
            # 拿到半截思考，重试一次比调高全局默认预算更省（多数请求不需要）。
            if not content and finish == "length" and reasoning and budget < THINKING_RETRY_CAP:
                retry_budget = min(budget * 2, THINKING_RETRY_CAP)
                print(
                    f"[LLM] call_llama: content 为空且 finish=length（推理长度={len(reasoning)}），"
                    f"预算 {budget}→{retry_budget} 重试一次",
                    file=sys.stderr,
                )
                payload["max_tokens"] = retry_budget
                resp2 = _http_post(_session, _api_url, payload,
                                   headers=runtime_auth_headers(_api_url), timeout=300)
                if resp2.status_code == 200:
                    data = resp2.json()
                    retry_content, retry_reasoning, finish, content_cacheable = _extract_openai_content(
                        data, retry_budget)
                    content = retry_content
                    reasoning = retry_reasoning or reasoning
                    budget = retry_budget
            if not content and reasoning:
                # 重试后仍无答案 → 不拿思考冒充答案（ENG-23），但要说清是什么问题
                print(
                    f"[LLM] call_llama: 仍无答案（finish={finish}, reasoning={len(reasoning)} tokens）。"
                    f"若为思考模型请设 {THINKING_MODE_ENV}=on，或提高调用方 max_tokens。",
                    file=sys.stderr,
                )
            tokens = data.get("usage", {}).get("total_tokens", 0)
            ledger_bump(tokens)
            _record_llm_metrics(payload["model"], data.get("usage"), True, tokens)
            _emit_llm_envelope(payload["model"], messages, system_prompt, temperature,
                               data.get("usage"), data=data)
            if cache_key and content and content_cacheable:
                _llm_cache_set(cache_key, content, tokens)
            return content, tokens
    except Exception as e:
        print(f"[LLM] call_llama: 请求异常 — {e}", file=sys.stderr)
        pass
    print("[LLM] call_llama: LLM 后端不可达，返回空结果", file=sys.stderr)
    _record_llm_metrics(payload["model"], None, False)
    _emit_llm_envelope(payload["model"], messages, system_prompt, temperature, None)
    return "", 0


def call_llama_text(prompt: str, system_prompt: str = "",
                    max_tokens: int = 2048) -> tuple[str, int]:
    """Call LLM with a single text prompt."""
    return call_llama([{"role": "user", "content": prompt}],
                      system_prompt=system_prompt, max_tokens=max_tokens)


def call_llama_censored(messages: list[dict]) -> tuple[str, int]:
    """Call LLM with content safety check."""
    return call_llama(messages, system_prompt=CENSORED_SYSTEM_PROMPT)


def is_censored_content(text: str) -> bool:
    """Check if content appears to be censored/unsafe."""
    blocked = ["我无法处理该请求", "无法提供", "不能生成", "违反"]
    return any(phrase in text for phrase in blocked)


def _post_cloud(messages: list[dict], system_prompt: str = "",
                max_tokens: int = 4096) -> tuple[str, int]:
    """Call cloud API."""
    from ..config import get_llm_config
    cfg = get_llm_config()
    api_url = cfg["api_url"]
    api_key = cfg["api_key"]
    model = cfg["model"] or MODEL_DEEPSEEK
    cloud_mode = _thinking_mode()
    cloud_budget = max(max_tokens, THINKING_MIN_BUDGET) if cloud_mode == "on" else max_tokens
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "max_tokens": cloud_budget,
        "temperature": 0.7,
        "stream": False,
    }
    if cloud_mode != "on":
        # 云端 Qwen3 系默认开思考模式 → content 恒空、答案全进 reasoning_content。
        # 只有明确「本次不要思考」时才关（ENG-23 / HARNESS_LLM_THINKING）。
        payload.update(thinking_payload(cfg))
    # Check cache for cloud calls —— key 带上 thinking 与预算（ENG-24：不同配置的结果不可互换）
    cache_key = _llm_cache_key(model, payload["messages"], system_prompt, 0.7,
                               thinking=cloud_mode, max_tokens=cloud_budget)
    cached = _llm_cache_get(cache_key)
    if cached:
        return cached
    if not api_url or not api_key:
        print("[LLM] _post_cloud: 未配置云端 api_url/api_key，降级到本地 LLM", file=sys.stderr)
        return call_llama(messages, system_prompt=system_prompt, max_tokens=max_tokens)
    try:
        resp = _http_post(
            _session, api_url, payload,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
            timeout=120,
        )
        if resp.status_code == 200:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            tokens = data.get("usage", {}).get("total_tokens", 0)
            ledger_bump(tokens)
            _record_llm_metrics(model, data.get("usage"), True, tokens)
            _emit_llm_envelope(model, messages, system_prompt, 0.7,
                               data.get("usage"), data=data)
            if content:
                _llm_cache_set(cache_key, content, tokens)
            return content, tokens
    except Exception as e:
        print(f"[LLM] _post_cloud: 云 API 请求异常 — {e}", file=sys.stderr)
        # 云侧失败单独记 error：能看出「云挂了 → 降级到本地」这个模式（随后 call_llama 会再记一次 success）
        _record_llm_metrics(model, None, False)
        _emit_llm_envelope(model, messages, system_prompt, 0.7, None)
        pass
    # Fallback to local
    print("[LLM] _post_cloud: 云 API 不可达，降级到本地 LLM", file=sys.stderr)
    return call_llama(messages, system_prompt=system_prompt, max_tokens=max_tokens)


def call_deepseek(messages: list[dict], system_prompt: str = "",
                  max_tokens: int = 4096) -> tuple[str, int]:
    """Call DeepSeek model."""
    return _post_cloud(messages, system_prompt=system_prompt, max_tokens=max_tokens)


def call_coder(prompt: str, system_prompt: str = "",
               max_tokens: int = 4096) -> tuple[str, int]:
    """Call coder model for code generation."""
    return call_llama([{"role": "user", "content": prompt}],
                      system_prompt=system_prompt or "你是一个编程助手。",
                      max_tokens=max_tokens, temperature=0.1)


def extract_json_array(text: str) -> list[dict]:
    """Extract JSON array from model output."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:]) if len(lines) > 1 else text
        if text.endswith("```"):
            text = text[:-3]
    text = text.strip().strip("`").replace("json\n", "").strip()
    try:
        result = json.loads(text)
        return result if isinstance(result, list) else [result]
    except (json.JSONDecodeError, ValueError):
        return []


def _cloud_headers() -> dict:
    """Cloud API headers."""
    if CLOUD_API_KEY:
        return {"Authorization": f"Bearer {CLOUD_API_KEY}"}
    return {}
