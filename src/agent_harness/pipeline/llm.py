"""LLM utilities — shim for backward compatibility with tools/ modules.

The tools were originally written against the ~/.openclaw/workspace/harness/
project structure and import from `pipeline.llm`. This module provides
the same interface using the new agent_harness package.
"""

import os
import json
import requests as req_lib

from ..config import (
    LLAMA_API, OLLAMA_API, DEEPSEEK_API,
    CLOUD_API_DIRECT, CLOUD_API_KEY,
    MODEL_LLAMA, MODEL_DEEPSEEK,
    HARNESS_DIR, MEMORY_DIR, SKILLS_DIR,
)

# Re-export for tools/ compatibility
WORKSPACE_DIR = os.path.normpath(os.path.join(str(HARNESS_DIR), ".."))

# Shared HTTP session
_session = req_lib.Session()
_session.headers.update({"User-Agent": "agent-harness/0.2.0"})

CENSORED_SYSTEM_PROMPT = "你是一个内容安全助手。对于色情、暴力、违法内容，回复'我无法处理该请求'。"


def call_llama(messages: list[dict], system_prompt: str = "",
               max_tokens: int = 2048, temperature: float = 0.3) -> tuple[str, int]:
    """Call local LLaMA model. Returns (text, tokens_used)."""
    payload = {
        "model": MODEL_LLAMA,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
        "thinking": {"type": "disabled"},
    }
    try:
        resp = _session.post(LLAMA_API, json=payload, timeout=300)
        if resp.status_code == 200:
            data = resp.json()
            msg = data["choices"][0]["message"]
            # Strip reasoning tokens — Qwen3.6-35B outputs them before content
            content = msg.get("content", "")
            if not content:
                # If content is empty, the model might still be generating
                content = msg.get("reasoning_content", "")[-200:] or "(empty)"
            tokens = data.get("usage", {}).get("total_tokens", 0)
            return content, tokens
    except Exception:
        pass
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
    payload = {
        "model": MODEL_DEEPSEEK,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "stream": False,
    }
    try:
        resp = _session.post(
            DEEPSEEK_API,
            json=payload,
            headers={"Authorization": f"Bearer {CLOUD_API_KEY}"} if CLOUD_API_KEY else {},
            timeout=120,
        )
        if resp.status_code == 200:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            tokens = data.get("usage", {}).get("total_tokens", 0)
            return content, tokens
    except Exception:
        pass
    # Fallback to local
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
