"""SEC-15：会话写路径的 owner 校验（DELETE / POST-meta）。

读路径（messages / export / logs）此前已调 `_session_access_denied`，
写路径漏了 —— 任意登录用户可删除/篡改他人会话。本文件锁定写路径的矩阵：
{owner, non-owner, admin, anonymous} × {删, 改 meta}，双向断言（该放的必须放）。
"""
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import agent_harness.apps.research.api as research_api
from agent_harness.core.pipeline import session_store as ss

OWNER_ID = "u_owner"
SECRET = "他人私有研究内容"

IDENTITIES = {
    "owner": ("u_owner", "user", True),
    "non_owner": ("u_me", "user", False),
    "admin": ("u_admin", "admin", True),
    "anonymous": (None, None, False),
}


def _client(user_id, role) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        if user_id is not None:
            request.state.user = {"id": user_id, "username": user_id, "role": role}
        return await call_next(request)

    app.include_router(research_api.router)
    return TestClient(app)


@pytest.fixture
def owned_session(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "SESSION_DIR", tmp_path, raising=False)
    monkeypatch.setattr(ss, "DEFAULT_DIR", str(tmp_path), raising=False)
    sid = "cs_write_authz"
    ss.save_session(sid, [{"role": "user", "content": SECRET}], owner_id=OWNER_ID)
    return sid


@pytest.mark.parametrize("ident", list(IDENTITIES), ids=list(IDENTITIES))
def test_delete_session_owner_scoped(ident, owned_session):
    user_id, role, allow = IDENTITIES[ident]
    r = _client(user_id, role).delete(f"/v1/sessions/{owned_session}")
    if allow:
        assert r.status_code == 200, r.text
        assert ss.load_session(owned_session) is None      # 真删了
    else:
        assert r.status_code in (401, 404), r.text
        assert ss.load_session(owned_session) is not None  # 别人删不掉


@pytest.mark.parametrize("ident", list(IDENTITIES), ids=list(IDENTITIES))
def test_update_meta_owner_scoped(ident, owned_session):
    user_id, role, allow = IDENTITIES[ident]
    r = _client(user_id, role).post(
        f"/v1/sessions/{owned_session}/meta", json={"title": "被篡改的标题"}
    )
    if allow:
        assert r.status_code == 200, r.text
        assert ss.get_session_summary(owned_session)["title"] == "被篡改的标题"
    else:
        assert r.status_code in (401, 404), r.text
        title = (ss.get_session_summary(owned_session) or {}).get("title", "")
        assert title != "被篡改的标题"


def test_non_owner_sees_same_404_as_missing(owned_session):
    """非 owner 与不存在的会话必须返回同样的状态码 + 同样的错误体（不做存在性 oracle）。"""
    client = _client("u_me", "user")
    r_other = client.delete(f"/v1/sessions/{owned_session}")
    r_missing = client.delete("/v1/sessions/cs_does_not_exist")
    assert r_other.status_code == r_missing.status_code == 404
    assert r_other.json() == r_missing.json()
