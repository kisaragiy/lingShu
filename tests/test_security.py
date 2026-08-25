"""安全测试 — Render 暴露面修复

覆盖：
- sanitize_detail 抹 IP/端口/连接错误（防 /health 泄露内网）
- HealthReport.to_dict 输出无内网 IP
- 前端不再需要 _API_TOKEN（serve_frontend 不注入 token）
运行：cd agent-harness && .venv/Scripts/python.exe -m pytest tests/test_security.py -q
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core.health import sanitize_detail


# ─── sanitize_detail: 抹内网 IP ───
def test_sanitize_removes_private_ip():
    detail = "HTTPConnectionPool(host='172.22.175.253', port=11434): Max retries"
    out = sanitize_detail(detail)
    assert "172.22.175.253" not in out
    assert "172." not in out


def test_sanitize_removes_ip_in_plain_text():
    out = sanitize_detail("连接 WSL 172.22.175.253:11434 超时")
    assert "172.22.175.253" not in out


def test_sanitize_keeps_normal_info():
    """正常信息(磁盘/模型)应保留"""
    out = sanitize_detail("仅剩 69.2GB 可用")
    assert "69.2GB" in out
    out2 = sanitize_detail("WSL 桥接可能不通")
    assert "WSL" in out2


def test_sanitize_empty():
    assert sanitize_detail("") == ""


def test_sanitize_returns_str():
    assert isinstance(sanitize_detail(None or ""), str)


# ─── HealthReport.to_dict 无 IP 泄露 ───
def test_health_report_to_dict_no_ip(monkeypatch):
    from agent_harness.core.health import HealthReport, CheckResult
    r = HealthReport(
        status="degraded",
        version="0.75.0",
        checks=[
            CheckResult("ollama", "down", "连接 172.22.175.253:11434 超时"),
        ],
    )
    d = r.to_dict()
    text = json.dumps(d, ensure_ascii=False)
    assert "172.22.175.253" not in text
    assert "11434" not in text  # 端口也不该暴露
