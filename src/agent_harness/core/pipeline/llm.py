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


# Shared HTTP session
_session = req_lib.Session()
_session.headers.update({"User-Agent": "agent-harness/0.2.0"})

# LLM response cache — in-memory, TTL-based
_LLM_CACHE: dict[str, tuple[float, str, int]] = {}  # key → (timestamp, content, tokens)
_LLM_CACHE_TTL = 60  # seconds

def _llm_cache_key(model: str, messages: list[dict], system_prompt: str, temperature: float) -> str:
    """Generate cache key from deterministic parameters."""
    import hashlib
    raw = json.dumps([model, messages, system_prompt, round(temperature, 2)], sort_keys=True, ensure_ascii=False)
    return hashlib.md5(raw.encode()).hexdigest()

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
    # Check cache
    cache_key = _llm_cache_key(MODEL_LLAMA, [{"role": "system", "content": system_prompt}] + messages, system_prompt, temperature) if temperature <= 0.5 else None
    if cache_key:
        cached = _llm_cache_get(cache_key)
        if cached:
            return cached
    # 运行时读取：用户在设置页填的 api_url/model 立即生效，不再依赖 import 快照
    _cfg = get_llm_config()
    payload = {
        "model": _cfg.get("model") or MODEL_LLAMA,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
        "thinking": {"type": "disabled"},
    }
    try:
        resp = _http_post(_session, _cfg.get("api_url") or LLAMA_API, payload, timeout=300)
        if resp.status_code == 200:
            data = resp.json()
            msg = data["choices"][0]["message"]
            # Strip reasoning tokens — Qwen3.6-35B outputs them before content
            content = msg.get("content", "")
            if not content:
                # 模型把 token 全花在 reasoning 上（thinking 未关 / max_tokens 不足）。
                # 推理尾段是思考过程，不是结论 —— 不能拿它冒充答案（ENG-23）。
                reasoning = msg.get("reasoning_content", "") or ""
                if reasoning:
                    print(
                        f"[LLM] call_llama: content 为空，reasoning 长度={len(reasoning)}"
                        f"（疑似 thinking 未关或 max_tokens={max_tokens} 不足）",
                        file=sys.stderr,
                    )
                    if len(reasoning) > max_tokens * 0.8:
                        # DeepSeek-R1 系：答案确实在 reasoning 里且被截断 → 显式标注，
                        # 让上层知道这不是正式结论（比静默当答案诚实）
                        content = "[推理截断输出] " + reasoning[-200:]
                content_cacheable = content.startswith("[推理截断输出]") is False
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
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "stream": False,
        # 云端 Qwen3 系默认开思考模式 → content 恒空、答案全进 reasoning_content，
        # 与本地 call_llama 对齐显式关闭（ENG-23）
        "thinking": {"type": "disabled"},
    }
    # Check cache for cloud calls
    cache_key = _llm_cache_key(model, payload["messages"], system_prompt, 0.7)
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
