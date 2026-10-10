"""种子 ⑬：`/v1/audit/logs` 必须是 admin-only（跨用户信息泄漏）。

审计行含每次工具调用的实参与输出（用户 prompt、执行的代码、文件路径…），
而表结构里没有 owner 列 → 无法按用户过滤，多用户部署下 user 读得到所有人的明细。
"""
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import agent_harness.apps.research.api as research_api


def _client_as(role: str) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = {"id": f"u_{role}", "username": role, "role": role}
        return await call_next(request)

    app.include_router(research_api.router)
    return TestClient(app)


def test_audit_logs_rejects_non_admin():
    """普通用户不得读审计明细（它含所有人的工具实参/输出）。"""
    r = _client_as("user").get("/v1/audit/logs")
    assert r.status_code == 403, f"普通用户拿到了审计明细：{r.status_code} {r.text[:120]}"


def test_audit_logs_allows_admin():
    """正向控制：admin 仍可读（否则修法就成了把端点砍掉）。"""
    r = _client_as("admin").get("/v1/audit/logs")
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    assert "logs" in body and "count" in body


def test_audit_logs_unauthenticated_is_denied():
    """未认证（无 user 上下文）同样拒绝——不能因为"没注入身份"就默认放行。"""
    app = FastAPI()
    app.include_router(research_api.router)
    r = TestClient(app).get("/v1/audit/logs")
    assert r.status_code == 403, r.status_code
