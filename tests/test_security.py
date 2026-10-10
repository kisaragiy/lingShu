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

# ─── 测试数据隔离：auth_db/JWT secret 一律落到临时目录，绝不碰真实 ~/.agent-harness ───
import os as _os
import tempfile as _tempfile
import shutil as _shutil
import atexit as _atexit

_TMP_DATA = _tempfile.mkdtemp(prefix="ling_shu_sec_")
_os.environ["HARNESS_DATA_DIR"] = _TMP_DATA
_atexit.register(_shutil.rmtree, _TMP_DATA, ignore_errors=True)

import pytest

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


# ─── mp-login 命名空间隔离回归（P0：免密铸 admin token） ───
@pytest.fixture(autouse=True)
def _isolated_auth_db():
    """无论 auth_db 模块是否已被其他测试文件以真实路径导入，本文件一律指向临时库。

    auth_db 的 DB_PATH 在 import 时绑定，仅设环境变量不足以保证隔离。
    """
    from agent_harness.core.auth import auth_db as _dbm
    _orig_path = _dbm.DB_PATH
    _dbm.DB_PATH = Path(_TMP_DATA) / "auth.db"
    _dbm.close_all_connections()
    _dbm._DB_INITIALIZED = False
    _dbm._thread_local = None
    yield
    _dbm.close_all_connections()
    _dbm.DB_PATH = _orig_path
    _dbm._DB_INITIALIZED = False
    _dbm._thread_local = None


def test_password_user_cannot_occupy_device_namespace():
    """密码身份禁止占用 mp_ 保留前缀（create_user 单一 choke point）。"""
    from agent_harness.core.auth import auth_db as db
    with pytest.raises(ValueError):
        db.create_user("mp_admin", "AdminPass123", role="admin")


def test_device_user_requires_prefix_and_user_role():
    from agent_harness.core.auth import auth_db as db
    # is_device 必须带前缀
    with pytest.raises(ValueError):
        db.create_user("admin", "AdminPass123", role="admin", is_device=True)
    # 设备账号不允许 admin
    with pytest.raises(ValueError):
        db.create_user("mp_root", "AdminPass123", role="admin", is_device=True)
    dev = db.create_user("mp_dev1", "x" * 32, role="user", is_device=True)
    assert dev["role"] == "user"


def test_mp_login_disabled_by_default_no_endpoint():
    """② 的锁：未设 HARNESS_ENABLE_MP_LOGIN 时 mp-login 不存在（fail-closed）。

    P0（设备身份给 admin 账号铸 token）的**启用态**复核搬到了
    tests/test_mp_login_gate.py —— 那条必须在 import 前把开关设好，用独立子进程跑；
    本文件不设开关，所以这里只能断言"默认关"这一面。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from agent_harness.apps.research import api as api_mod

    r = TestClient(FastAPI()).post("/v1/auth/mp-login", json={"device_id": "admin"})  # 空 app 兜底
    assert r.status_code == 404

    app = FastAPI()
    app.include_router(api_mod.router)
    client = TestClient(app)
    resp = client.post("/v1/auth/mp-login", json={"device_id": "admin"})
    assert resp.status_code == 404, f"默认关时端点应不存在（404），实测 {resp.status_code}"
    assert "token" not in resp.text
    paths = {getattr(rt, "path", "") for rt in api_mod.router.routes}
    assert "/v1/auth/mp-login" not in paths, "默认关时不应注册该路由"
    assert "/v1/auth/mp-login" not in api_mod._AUTH_EXEMPT_V1, (
        "默认关时不得留在豁免名单：豁免=放行，与端点不存在是两件事"
    )
