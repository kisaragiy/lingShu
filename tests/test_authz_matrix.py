"""ADR#9 追加规则：authz 用例必须**双向** —— {admin, owner, non-owner, anonymous} × {读, 写, 删}。

为什么单独立一个文件（@architect 2026-09-10）：session owner 隔离那次，负向控制是**假绿**——
"非 owner 404"看起来是权限收紧了，但实际是**所有人都 404**（`get_session_summary` 没吐
`owner_id`，`_session_access_denied` 读到空字符串 → 非 admin 全拒），只有正向控制抓到。
"一刀切全拒"是最容易伪装成正确的错误，所以矩阵必须同时断言**该放的必须放**。

矩阵实现（4 端点 × 4 身份 = 16 格，每格都验：状态码 + 不泄漏正文）：
  端点：GET .../messages（读）· GET .../export（读-导出）· GET .../logs（读）· DELETE .../logs（写/删）
  身份：owner(user) 该放 · non-owner(user) 该拒 · admin 该放 · anonymous 该拒
"""
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import agent_harness.apps.research.api as research_api
from agent_harness.core.pipeline import session_store as ss

SECRET = "他人私有研究内容"

OWNER_ID = "u_owner"

# (method, path_template, 语义)
ENDPOINTS = [
    ("GET", "/v1/sessions/{sid}/messages", "读会话消息"),
    ("GET", "/v1/sessions/{sid}/export", "导出会话"),
    ("GET", "/v1/sessions/{sid}/logs", "读会话日志"),
    ("DELETE", "/v1/sessions/{sid}/logs", "清空会话日志"),
]

# 身份 -> (user_id or None, role or None, 期望 allow/deny)
IDENTITIES = {
    "owner": ("u_owner", "user", "allow"),
    "non_owner": ("u_me", "user", "deny"),
    "admin": ("u_admin", "admin", "allow"),
    "anonymous": (None, None, "deny"),
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
def session_of_owner(monkeypatch, tmp_path):
    """把会话存储重定向到 tmp，造一条属于 u_owner 的会话。"""
    monkeypatch.setattr(ss, "SESSION_DIR", tmp_path, raising=False)
    monkeypatch.setattr(ss, "DEFAULT_DIR", str(tmp_path), raising=False)
    sid = "cs_authz_matrix"
    ss.save_session(sid, [{"role": "user", "content": SECRET},
                          {"role": "assistant", "content": "答复"}], owner_id=OWNER_ID)
    return sid


@pytest.mark.parametrize("method,path,label", ENDPOINTS, ids=[e[2] for e in ENDPOINTS])
@pytest.mark.parametrize("ident", list(IDENTITIES), ids=list(IDENTITIES))
def test_authz_matrix(method, path, label, ident, session_of_owner):
    user_id, role, expect = IDENTITIES[ident]
    url = path.format(sid=session_of_owner)
    r = _client(user_id, role).request(method, url)

    if expect == "allow":
        assert r.status_code < 300, (
            f"[{label}] {ident} 应该被放行，实测 {r.status_code} {r.text[:160]} —— "
            "注意：如果这里 404/403，先确认不是「一刀切全拒」型回归"
            "（对所有人都拒，也能让负向格全绿）"
        )
    else:
        assert r.status_code >= 400, (
            f"[{label}] {ident} 应该被拒，实测 {r.status_code} —— 越权可达"
        )
        assert SECRET not in r.text, f"[{label}] {ident} 被拒但正文泄漏了内容（oracle）"


def test_matrix_is_not_all_deny(session_of_owner):
    """独立断言：矩阵里"允许"的格子必须真的存在（防整套矩阵被"全拒"实现骗过）。"""
    allowed = 0
    for method, path, _ in ENDPOINTS:
        r = _client(*IDENTITIES["owner"][:2]).request(method, path.format(sid=session_of_owner))
        allowed += 1 if r.status_code < 300 else 0
    assert allowed == len(ENDPOINTS), (
        f"owner 自己只有 {allowed}/{len(ENDPOINTS)} 个端点可用 —— 隔离实现把 owner 也拒了"
    )
