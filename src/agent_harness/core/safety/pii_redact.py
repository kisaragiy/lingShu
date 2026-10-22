"""PII / 密钥脱敏（SEC-3 输出层 + SEC-19 审计落库共用同一个函数）。

设计要点
--------
* **单独成函数**，不写在 `audit()` 里 —— 可被直接单测，也可被报告输出层复用；
* 两层规则：
  1. **键名规则**：JSON 里 `api_key` / `token` / `secret` / `password` / `authorization` /
     `cookie` … 的值整体替换为 `***`（键名保留，排障时还看得见「这里有敏感字段」）；
     查询串形式 `?api_key=...` 同样处理；
  2. **值形态规则**：`sk-*`、`Bearer <token>`、长 hex、长 base64、手机号/身份证/邮箱/
     银行卡号 —— 就算键名没被识别（塞进 prompt、URL 签名、自由文本），也按形态兜住。
* 开关：`HARNESS_PII_REDACT=0` 关总闸（SEC-3 模块层）；SEC-19 的 audit 落库另有
  `HARNESS_AUDIT_REDACT=0`，两层独立、不合并。
* **尽力而为**：正则覆盖常见形态，不承诺 100%（分段拼接、二次编码的密钥可能漏）。
"""
from __future__ import annotations

import os
import re

__all__ = ["redact_pii", "SECRET_MASK", "PII_MASK"]

SECRET_MASK = "***"
PII_MASK = "[已脱敏]"

# ── 1. 敏感键名（大小写与常见变体） ─────────────────────────────────────────
_SECRET_NAME = (
    r"api[_-]?key|apikey|x-api-key|access[_-]?key|secret|client[_-]?secret|"
    r"token|access[_-]?token|refresh[_-]?token|id[_-]?token|"
    r"password|passwd|pwd|authorization|cookie|set-cookie"
)

# JSON: "api_key": "值"（值里允许转义，整体吞掉）
_SECRET_JSON = re.compile(
    r'("(?:%s)"\s*:\s*)"(?:\\.|[^"\\])*"' % _SECRET_NAME, re.IGNORECASE
)
# 查询串 / k=v 形式：api_key=sk-xxx&page=2（只认 '='，避免和 JSON 的 ':' 打架）
_SECRET_KV = re.compile(
    r"\b(%s)=([^\s&,;\"'}\)]+)" % _SECRET_NAME, re.IGNORECASE
)

# ── 2. 值形态 ───────────────────────────────────────────────────────────────
_VALUE_RULES: list[tuple[re.Pattern[str], object]] = [
    # Bearer <token>
    (re.compile(r"\b(Bearer\s+)([A-Za-z0-9\-._~+/]{10,}=*)", re.IGNORECASE), "bearer"),
    # sk- 前缀的模型 key
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "prefix"),
    # 长 hex（sha256/md5/签名）
    (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "prefix"),
    # 长 base64 / urlsafe token（≥40 字符，含 / + = - _）
    (re.compile(r"\b[A-Za-z0-9_\-+/]{40,}={0,2}\b"), "prefix"),
]

# ── 3. 个人可识别信息 ───────────────────────────────────────────────────────
_PII_RULES: list[re.Pattern[str]] = [
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),            # 手机号
    re.compile(r"(?<![\dXx])\d{17}[\dXx](?![\dXx])"),    # 身份证
    re.compile(r"(?<![\d.])\d{16,19}(?![\d.])"),         # 银行卡（避开小数）
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),             # 邮箱
]


def _mask_value(value: str) -> str:
    """保留前 4 位（够定位"是不是同一把 key"），其余抹掉。"""
    return f"{value[:4]}{SECRET_MASK}" if len(value) > 8 else SECRET_MASK


def _sub_bearer(match: re.Match[str]) -> str:
    return f"{match.group(1)}{_mask_value(match.group(2))}"


def _sub_prefix(match: re.Match[str]) -> str:
    return _mask_value(match.group(0))


def redact_pii(text: str) -> str:
    """对一段文本（通常是 json.dumps 后的 params/result）做密钥 + PII 脱敏。"""
    if not text:
        return text
    if os.environ.get("HARNESS_PII_REDACT", "1") == "0":
        return text

    # 1) 键名命中 → 值整体替换（键名保留，便于排障）
    text = _SECRET_JSON.sub(lambda m: f'{m.group(1)}"{SECRET_MASK}"', text)
    text = _SECRET_KV.sub(lambda m: f"{m.group(1)}={SECRET_MASK}", text)

    # 2) 值形态
    for pattern, kind in _VALUE_RULES:
        text = pattern.sub(_sub_bearer if kind == "bearer" else _sub_prefix, text)

    # 3) PII
    for pattern in _PII_RULES:
        text = pattern.sub(PII_MASK, text)
    return text
