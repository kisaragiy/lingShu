"""SEC-19 回归门禁：审计日志敏感实参脱敏。

背景（2026-10-22 问题清单 SEC-19）：
`core/audit.py::audit()` 曾把 params/result **原文** `json.dumps` 落 sqlite ——
用户 prompt 里的手机号、工具实参里的 api_key/token/Bearer、URL 里的签名，
全部明文躺在 `audit.db` 里；admin-only 只是 API 面闸，库文件被读即全量泄露。

定稿方向：脱敏逻辑**单独成函数**（`core/safety/pii_redact.py::redact_pii`）+ audit() 写入前调用，
保留 `HARNESS_AUDIT_REDACT=0` 本地调试开关。本文件直接单测该函数 + 端到端测 audit()。
"""
from __future__ import annotations

import json

import pytest


# ═══════════════════════════════════════════════════════════════════════
# 一、redact_pii 纯函数（可直接单测，不依赖 DB）
# ═══════════════════════════════════════════════════════════════════════
@pytest.fixture
def redact(monkeypatch):
    monkeypatch.delenv("HARNESS_PII_REDACT", raising=False)
    from agent_harness.core.safety.pii_redact import redact_pii
    return redact_pii


@pytest.mark.parametrize("key_name", [
    "api_key", "apikey", "apiKey", "API_KEY", "access_key", "token",
    "access_token", "secret", "client_secret", "password", "passwd",
    "authorization", "cookie", "x-api-key",
])
def test_secret_key_names_are_masked(redact, key_name):
    text = json.dumps({key_name: "super-secret-value-42"}, ensure_ascii=False)
    out = redact(text)
    assert "super-secret-value-42" not in out, f"{key_name} 的值没被脱敏"
    assert "***" in out
    assert json.loads(out) == {key_name: "***"}, f"{key_name} 脱敏后 JSON 结构被破坏: {out}"


def test_sk_prefixed_value_is_masked(redact):
    out = redact("用这个 key 调接口：sk-live-abcdef1234567890 别外传")
    assert "sk-live-abcdef1234567890" not in out
    assert "***" in out


def test_bearer_token_is_masked(redact):
    out = redact('headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig"}')
    assert "eyJhbGciOiJIUzI1NiJ9.payload.sig" not in out
    assert "***" in out


def test_long_hex_is_masked(redact):
    hex_secret = "a3f9c1d2e4b5a6978877665544332211ffeeddccbbaa99887766554433221100"
    out = redact(f"签名 sig={hex_secret}&ts=1")
    assert hex_secret not in out
    assert "***" in out


def test_query_string_secret_is_masked(redact):
    out = redact("https://x.example/cb?api_key=sk-abc123456&token=tok_9f8e7d6c&page=2")
    assert "sk-abc123456" not in out and "tok_9f8e7d6c" not in out
    assert "page=2" in out, "非敏感 query 参数不得被误伤"


def test_phone_and_email_are_masked(redact):
    out = redact(json.dumps({"prompt": "我的手机号13812345678，邮箱 admin@example.com"},
                            ensure_ascii=False))
    assert "13812345678" not in out
    assert "admin@example.com" not in out


# ─── 不误伤：普通数字 / 普通单词 / 正常文本 ───
@pytest.mark.parametrize("text", [
    "模型 deepseek-v4 返回 42 条，耗时 1.5 秒",
    '{"limit": 50, "page": 2, "score": 0.87}',
    "今天日期 2026-10-22，批次号 20261022",
    "hello world, this is a normal sentence with punctuation.",
    "路径 C:/Users/zwq/agent-harness/src 与 tokenizer 都是非敏感内容",
    '{"action": "generate", "duration_ms": 1234, "model": "sdxl"}',
])
def test_normal_content_is_not_mangled(redact, text):
    assert redact(text) == text, f"正常内容被误伤: {text} → {redact(text)}"


def test_empty_and_none_text(redact):
    assert redact("") == ""


def test_module_owner_switch_off_disables_redaction(monkeypatch):
    """SEC-3 模块层总闸：HARNESS_PII_REDACT=0 → 原样返回。"""
    monkeypatch.setenv("HARNESS_PII_REDACT", "0")
    from agent_harness.core.safety.pii_redact import redact_pii

    raw = json.dumps({"api_key": "sk-abc"})
    assert redact_pii(raw) == raw


# ═══════════════════════════════════════════════════════════════════════
# 二、audit() 端到端（落库前脱敏）
# ═══════════════════════════════════════════════════════════════════════
@pytest.fixture
def audit_mod(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_AUDIT_REDACT", raising=False)
    monkeypatch.delenv("HARNESS_PII_REDACT", raising=False)
    from agent_harness.core import audit as audit_mod

    monkeypatch.setattr(audit_mod, "DB_PATH", tmp_path / "audit.db", raising=False)
    return audit_mod


def test_audit_redacts_params_on_write(audit_mod):
    audit_mod.audit("sec19_test", prompt="我的手机号13812345678",
                    api_key="sk-abc123xyz", model="sdxl")

    row = audit_mod.query_audit("sec19_test")[0]
    params = json.loads(row["params"])
    assert "sk-abc123xyz" not in row["params"]
    assert "13812345678" not in row["params"]
    assert params["api_key"] == "***"
    assert params["model"] == "sdxl", "非敏感实参必须保留（可排障）"
    # 元字段不受影响
    assert row["action"] == "sec19_test"
    assert row["error"] in ("", None)


def test_audit_redacts_result_on_write(audit_mod):
    audit_mod.audit("sec19_result", result={"output": "联系 admin@example.com",
                                            "token": "tok_live_1234567890"})
    row = audit_mod.query_audit("sec19_result")[0]
    assert "admin@example.com" not in row["result"]
    assert "tok_live_1234567890" not in row["result"]


def test_audit_duration_and_error_fields_untouched(audit_mod):
    audit_mod.audit("sec19_meta", duration_ms=12.5, error="timeout", query="x")
    row = audit_mod.query_audit("sec19_meta")[0]
    assert row["duration_ms"] == 12.5
    assert row["error"] == "timeout"
    assert json.loads(row["params"]) == {"query": "x"}


def test_audit_redact_switch_off_for_local_debug(audit_mod, monkeypatch):
    """HARNESS_AUDIT_REDACT=0 → 本地调试可落原文。"""
    monkeypatch.setenv("HARNESS_AUDIT_REDACT", "0")
    audit_mod.audit("sec19_off", api_key="sk-abc123xyz")

    row = audit_mod.query_audit("sec19_off")[0]
    assert "sk-abc123xyz" in row["params"]
