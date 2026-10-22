"""SEC-4：JWT 撤销语义一致性 —— access token 登出后必须立刻失效。

修复前：refresh 流程查 sessions 表（撤销有效），access token 的 middleware 分支
只验签不查表（撤销无效）→ 登出后泄露的 access token 仍可用满 8h。
"""
import pytest
from fastapi.testclient import TestClient

import agent_harness.core.auth.auth_db as auth_db
import agent_harness.core.config as config
import agent_harness.main as main_mod

PASSWORD = "Passw0rd!test"
USERNAME = "t_admin_revoke"


@pytest.fixture
def isolated_auth(monkeypatch, tmp_path):
    """把 auth.db 重定向到 tmp，并强制打开认证（关闭 demo 模式）。"""
    monkeypatch.setattr(auth_db, "DB_PATH", tmp_path / "auth.db")
    monkeypatch.setattr(auth_db, "HARNESS_DIR", tmp_path)
    monkeypatch.setattr(auth_db, "_DB_INITIALIZED", False, raising=False)
    monkeypatch.setattr(auth_db, "_thread_local", None, raising=False)
    monkeypatch.setattr(config, "DISABLE_AUTH", False)
    yield
    auth_db.close_all_connections()


def _login(client) -> str:
    r = client.post("/v1/auth/setup-admin", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200, r.text
    r = client.post("/v1/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_access_token_invalid_after_logout(isolated_auth):
    client = TestClient(main_mod.app)
    token = _login(client)
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/v1/auth/me", headers=headers).status_code == 200

    assert client.post("/v1/auth/logout", headers=headers).status_code == 200

    r = client.get("/v1/auth/me", headers=headers)
    assert r.status_code == 401, f"登出后 access token 仍可用: {r.status_code} {r.text}"


def test_forged_token_still_rejected(isolated_auth):
    """负向控制：乱造 token 必须 401（防止修复把闸门改成"永远放行"）。"""
    client = TestClient(main_mod.app)
    _login(client)
    r = client.get("/v1/auth/me", headers={"Authorization": "Bearer not.a.real.token"})
    assert r.status_code == 401
