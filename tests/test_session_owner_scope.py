"""种子 ⑭：单会话端点的 owner 隔离（IDOR 家族）。

`/v1/sessions/{id}/messages|export|logs`（GET）与 `DELETE .../logs` 此前只接 `session_id`、
不接 `request` → 任何已登录用户可读/导出/清空**他人**的会话与日志。
会话元数据本就带 `owner_id`（`session_store.py:270` 写、`:303` 过滤），所以是漏闸不是缺字段。
判据：非 owner 与"不存在"同回 404（不得成为存在性 oracle）；owner 与 admin 正常。
"""
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import agent_harness.apps.research.api as research_api


def _client(user_id: str, role: str) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = {"id": user_id, "username": user_id, "role": role}
        return await call_next(request)

    app.include_router(research_api.router)
    return TestClient(app)


def _mk_session(tmp_store, owner_id: str) -> str:
    sid = "cs_deadbeef"
    tmp_store.save_session(sid, [{"role": "user", "content": "他人私有研究内容"},
                                 {"role": "assistant", "content": "答复"}], owner_id=owner_id)
    return sid


import pytest  # noqa: E402


@pytest.fixture
def session_owned_by_other(monkeypatch, tmp_path):
    """把会话存储重定向到 tmp，并造一条属于 u_other 的会话。"""
    from agent_harness.core.pipeline import session_store as ss
    monkeypatch.setattr(ss, "SESSION_DIR", tmp_path, raising=False)
    monkeypatch.setattr(ss, "DEFAULT_DIR", str(tmp_path), raising=False)
    sid = _mk_session(ss, "u_other")
    return sid


def test_non_owner_cannot_read_session_messages(session_owned_by_other):
    sid = session_owned_by_other
    r = _client("u_me", "user").get(f"/v1/sessions/{sid}/messages")
    assert r.status_code == 404, f"非 owner 读到了他人会话：{r.status_code} {r.text[:120]}"
    assert "他人私有研究内容" not in r.text


def test_non_owner_cannot_export_or_read_logs_or_clear(session_owned_by_other):
    sid = session_owned_by_other
    c = _client("u_me", "user")
    assert c.get(f"/v1/sessions/{sid}/export").status_code == 404, "非 owner 导出了他人会话"
    assert c.get(f"/v1/sessions/{sid}/logs").status_code == 404, "非 owner 读了他人日志"
    assert c.delete(f"/v1/sessions/{sid}/logs").status_code == 404, "非 owner 清空了他人日志"


def test_owner_and_admin_still_allowed(session_owned_by_other):
    """正向控制：owner 与 admin 都必须仍能用（防"一刀切全 404"式假修复）。"""
    sid = session_owned_by_other
    ok = _client("u_other", "user").get(f"/v1/sessions/{sid}/messages")
    assert ok.status_code == 200, ok.text[:200]
    assert "他人私有研究内容" in ok.text
    assert _client("u_admin", "admin").get(f"/v1/sessions/{sid}/messages").status_code == 200
