"""P0 门禁：/v1/setup/config 的认证与脱敏。

背景（2026-09-10 评审发现，secguard 问题 5）：
- `/v1/setup/config` 曾在 `_AUTH_EXEMPT_V1` 认证豁免名单里；
- GET 直接 `return load_config()`，原样返回 `llm.api_key`。

组合后果：本机任意进程（或另一个 Windows 账户）一条
`curl http://127.0.0.1:8788/v1/setup/config` 就能读到云 API key；
配合 `main.py` 的 `?api_token=` query 旁路，还可被恶意网页 drive-by 改写
api_url，把用户后续每条 prompt 发往攻击者服务器。
"""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def test_setup_config_not_auth_exempt():
    """配置读写端点必须要求认证。"""
    from agent_harness.apps.research.api import _AUTH_EXEMPT_V1
    assert "/v1/setup/config" not in _AUTH_EXEMPT_V1, (
        "/v1/setup/config 不得出现在认证豁免名单里"
    )


def test_redact_config_hides_api_key():
    """脱敏后不得出现任何密钥片段，只暴露 has_api_key。"""
    from agent_harness.core.pipeline.config_manager import redact_config

    out = redact_config({"llm": {"api_key": "sk-abcdef1234567890wxyz", "model": "m"},
                         "paths": {"llama_cpp": ""}})
    assert out["llm"]["api_key"] == ""
    assert out["llm"]["has_api_key"] is True
    blob = str(out)
    assert "sk-" not in blob and "abcdef1234" not in blob, "脱敏后仍残留密钥片段"


def test_redact_config_keeps_other_fields():
    """脱敏不能破坏其余配置字段。"""
    from agent_harness.core.pipeline.config_manager import redact_config

    out = redact_config({"llm": {"api_key": "", "model": "deepseek-v4"},
                         "ui": {"theme": "dark"}})
    assert out["llm"]["model"] == "deepseek-v4"
    assert out["llm"]["has_api_key"] is False
    assert out["ui"]["theme"] == "dark"


def test_no_query_token_bypass_in_main():
    """API token 不得通过 URL query 传递（会进日志/历史/Referer）。"""
    txt = (SRC / "agent_harness" / "main.py").read_text(encoding="utf-8")
    assert 'query_params.get("api_token"' not in txt, (
        "main.py 里仍有 ?api_token= 旁路，密钥会泄漏到访问日志"
    )
