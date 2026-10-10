"""种子 ⑯：demo 能力表（按后果语义）+ 市场安装的 slug 参数注入。

背景（@bughunter/@architect 2026-09-10）：
  - demo（DISABLE_AUTH=1）的访客身份是 `role="admin"`（main.py:115）→ **角色闸在 demo 下等于没闸**，
    所以 `admin/create-user`（公网可建持久管理员）、`tools/{name}/toggle`（全局禁用工具 = 持久 DoS）、
    `skills/*`（写宿主技能库）此前对任何访客开放。
  - `POST /v1/skills/marketplace/install` 的 `slug` 未校验就进 `subprocess.run([...])`：
    list 形式挡住 shell 注入，**挡不住 flag 注入**（`--target=/x`）。
判据：demo 下这些能力必须 403；**非 demo 不受影响**；slug 非法必须 400；
并配正向控制（demo 的 chat/报告读仍可用），防"把 demo 一刀切成砖"式假修复。
"""
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import agent_harness.apps.research.api as research_api


@pytest.fixture
def demo(monkeypatch):
    """把 DISABLE_AUTH 打开（函数内 `from ...config import DISABLE_AUTH` 是调用时读取）。"""
    from agent_harness.core import config as _config
    monkeypatch.setattr(_config, "DISABLE_AUTH", True, raising=False)


@pytest.fixture
def prod(monkeypatch):
    from agent_harness.core import config as _config
    monkeypatch.setattr(_config, "DISABLE_AUTH", False, raising=False)


def _client(role: str = "admin") -> TestClient:
    """demo 的访客被注入成 role=admin，这里如实复刻（这才是最坏的输入）。"""
    app = FastAPI()

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        request.state.user = {"id": "guest", "username": "访客", "role": role}
        return await call_next(request)

    app.include_router(research_api.router)
    return TestClient(app)


DEMO_DENIED = [
    ("POST", "/v1/admin/users", {"username": "attacker", "password": "123456", "role": "admin"}),
    ("POST", "/v1/tools/agent_browser/toggle", None),
    ("POST", "/v1/skills/anything/toggle", None),
    ("POST", "/v1/skills/marketplace/install", {"slug": "ok-skill"}),
    ("POST", "/v1/skills/generate", {"description": "随便生成一个技能"}),
    ("POST", "/v1/setup/config", {"llm": {"api_url": "https://evil.example/v1"}}),
    ("POST", "/v1/setup/auto-configure", None),
]


def test_demo_guest_admin_cannot_do_destructive_things(demo):
    c = _client("admin")
    bad = []
    for method, path, body in DEMO_DENIED:
        r = c.request(method, path, json=body) if body is not None else c.request(method, path)
        if r.status_code != 403:
            bad.append((method, path, r.status_code, r.text[:80]))
    assert not bad, (
        "demo 访客（身份被注入成 admin）拿到了破坏性能力：\n"
        + "\n".join(f"  {m} {p} -> {s} {t}" for m, p, s, t in bad)
        + "\n这些后果：建持久管理员 / 全局关工具（持久 DoS）/ 写宿主技能库 / 改服务器配置（RCE 链）"
    )


def test_demo_does_not_break_normal_use(demo):
    """正向控制：demo 仍要能用（防"全 403/全 404"式假修复）。"""
    assert _client("admin").get("/health").status_code == 200
    assert _client("admin").get("/v1/skills").status_code == 200


def test_non_demo_has_no_capability_denial(prod):
    """非 demo 模式不得被能力表误伤（该走的角色闸走角色闸）。"""
    r = _client("admin").post("/v1/skills/marketplace/install", json={"slug": "ok-skill"})
    assert r.status_code != 403, f"非 demo 被能力表误拒：{r.status_code} {r.text[:120]}"


# ─── #9：demo 下的"一键导出"= 转储所有人的数据 ───
# 三个导出端点的 owner 过滤是 `None if is_admin`，而 demo 访客即 admin →
# 任何访客可导出所有访客的会话/报告。自托管必须保留（那里 admin 是真管理员）。
EXPORT_ENDPOINTS = ["/v1/export/sessions", "/v1/export/reports", "/v1/export/backup"]


def test_demo_cannot_export_everyones_data(demo):
    bad = []
    for path in EXPORT_ENDPOINTS:
        r = _client("admin").get(path)
        if r.status_code != 403:
            bad.append((path, r.status_code, len(r.content)))
    assert not bad, (
        "demo 访客导出了全部数据（跨访客转储）：\n"
        + "\n".join(f"  {p} -> {s}，{n} 字节" for p, s, n in bad)
    )


def test_self_hosted_export_still_works(prod):
    """正向控制：自托管（非 demo）的一键导出不得被误砍——这是用户要的便利。"""
    bad = [p for p in EXPORT_ENDPOINTS if _client("admin").get(p).status_code == 403]
    assert not bad, f"自托管的导出被能力表误砍：{bad}"


def test_demo_export_can_be_explicitly_reenabled(demo, monkeypatch):
    """放开路径要存在且可用（一个 env），否则"限制"会变成没人能改的死结。"""
    monkeypatch.setenv("HARNESS_DEMO_ALLOW_EXPORT_ALL_DATA", "1")
    r = _client("admin").get("/v1/export/sessions")
    assert r.status_code != 403, f"显式放开后仍被拒：{r.status_code}"


BAD_SLUGS = ["-x", "--target=/etc", "a b", "../../etc/passwd", "a/../b", "x" * 100, "坏技能", "a;rm -rf /"]


def test_marketplace_slug_rejects_flag_injection_and_traversal(prod):
    """slug 必须过白名单：`-` 开头（flag 注入）、空白、路径分隔、超长、非 ASCII 全拒。"""
    c = _client("admin")
    bad = []
    for slug in BAD_SLUGS:
        r = c.post("/v1/skills/marketplace/install", json={"slug": slug})
        if r.status_code != 400:
            bad.append((slug, r.status_code, r.text[:80]))
    assert not bad, "非法 slug 未被拒（list 形式挡不住 flag 注入）：\n" + "\n".join(
        f"  {s!r} -> {st} {t}" for s, st, t in bad
    )


def test_marketplace_slug_accepts_legit_shape(prod):
    """正向控制：合法 slug 不得被校验误杀（它可能因 SkillHub 未安装而 400，但不能是 slug 错）。"""
    r = _client("admin").post("/v1/skills/marketplace/install", json={"slug": "my-skill_v2"})
    assert "slug 非法" not in r.text, f"合法 slug 被误杀：{r.status_code} {r.text[:120]}"
