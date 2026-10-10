"""mp-login 启用态契约：开关打开时，设备身份也不得给既有密码账号铸 token。

为什么单独一个文件、且用子进程跑：
`_MP_LOGIN_ENABLED` 在 `import agent_harness.apps.research.api` 时求值（关时不注册
路由），同一进程内无法为单个用例切换。若在测试进程里先 import 再改环境变量，结果是
"看起来测了启用态、其实测的是关闭态"的假绿灯；而 importlib.reload 会把模块级路由
复制进同一个 router，污染其他契约用例。所以这里用子进程 + 独立临时数据目录，
真正在"开关已开"的进程里执行一次 HTTP 请求，结果以 JSON 回传断言。

隔离：子进程的 HARNESS_DATA_DIR 指向临时目录，auth_db 只会写临时库，
不碰真实 ~/.agent-harness。

运行：.venv/Scripts/python.exe -m pytest tests/test_mp_login_gate.py -q
"""
import json
import os
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"

_CHILD = r'''
import json, os, sys, tempfile
tmp = tempfile.mkdtemp(prefix="lingshu_mp_gate_")
os.environ["HARNESS_DATA_DIR"] = tmp
sys.path.insert(0, os.environ["LINGSHU_SRC"])

from fastapi import FastAPI
from fastapi.testclient import TestClient

import agent_harness.apps.research.api as api

out = {"flag": os.environ.get("HARNESS_ENABLE_MP_LOGIN")}
out["route_registered"] = any(
    getattr(r, "path", "") == "/v1/auth/mp-login" for r in api.router.routes
)
out["exempt"] = "/v1/auth/mp-login" in api._AUTH_EXEMPT_V1

from agent_harness.core.auth import auth_db as db
from agent_harness.core.auth import auth_jwt as jwt

admin = db.create_user("admin", "AdminPass123", role="admin", display_name="admin")
app = FastAPI()
app.include_router(api.router)
r = TestClient(app).post("/v1/auth/mp-login", json={"device_id": "admin"})
out["status"] = r.status_code
out["body_head"] = r.text[:200]
if r.status_code == 200:
    payload = jwt.verify_token(r.json()["token"]) or {}
    out["role"] = payload.get("role")
    out["sub"] = payload.get("sub")
    out["admin_id"] = admin["id"]
    out["is_new"] = r.json().get("is_new")
    dev = db.get_user_by_username("mp_admin")
    out["device_row"] = bool(dev) and dev.get("role") == "user"
    out["device_id_matches"] = bool(dev) and payload.get("sub") == dev["id"]

tc = TestClient(app)
out["bad_slash"] = tc.post("/v1/auth/mp-login", json={"device_id": "a/b"}).status_code
out["bad_empty"] = tc.post("/v1/auth/mp-login", json={"device_id": ""}).status_code
print("CHILD_RESULT " + json.dumps(out))
'''


@pytest.fixture(scope="module")
def child_result():
    tmp = tempfile.mkdtemp(prefix="lingshu_mp_gate_parent_")
    env = dict(os.environ)
    env["HARNESS_ENABLE_MP_LOGIN"] = "1"
    env["LINGSHU_SRC"] = str(SRC)
    env["HARNESS_DATA_DIR"] = tmp
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _CHILD],
            capture_output=True, text=True, env=env, cwd=str(REPO), timeout=180,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    assert proc.returncode == 0, f"子进程失败：{proc.stderr[-2000:]}"
    line = [l for l in proc.stdout.splitlines() if l.startswith("CHILD_RESULT ")]
    assert line, f"子进程无结果输出：{proc.stdout[-2000:]}"
    return json.loads(line[-1][len("CHILD_RESULT "):])


def test_enabled_flag_registers_endpoint_and_exemption(child_result):
    assert child_result["flag"] == "1"
    assert child_result["route_registered"] is True, "开关打开时端点必须注册"
    assert child_result["exempt"] is True, "开关打开时端点必须在豁免名单（否则登录端点自己要先登录）"


def test_mp_login_never_mints_token_for_existing_password_account(child_result):
    """P0 回归锁：device_id="admin" 命中既有 admin 账号时，铸出的必须是设备侧
    普通用户 token（role=user、sub≠admin 的 user id），绝不能是 admin 身份。"""
    assert child_result["status"] == 200, f"启用态应 200，实测 {child_result}"
    assert child_result["role"] == "user", (
        f"设备登录铸出了 {child_result['role']} token（P0 回归）"
    )
    assert child_result["sub"] != child_result["admin_id"], (
        "设备登录落到了既有 admin 账号的身份上（P0 回归）"
    )
    # ① 的命名空间隔离：只碰 mp_<device_id> 设备账号，不动 admin 行
    assert child_result["device_row"] is True, "未建/未复用 role=user 的 mp_admin 设备账号"
    assert child_result["device_id_matches"] is True, "token 的 sub 不是设备账号 id"
    assert child_result["is_new"] is True, "设备账号应为新建，而不是命中密码账号"


def test_mp_login_rejects_malformed_device_id(child_result):
    assert child_result["bad_slash"] == 400, 'device_id="a/b" 应 400'
    assert child_result["bad_empty"] == 400, 'device_id="" 应 400'
