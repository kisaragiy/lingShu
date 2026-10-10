"""种子 ⑮：**真实管线**（`agent_harness.main.app`）的网关层测试 —— 本仓此前为零。

结构性盲区（实测）：`grep -rn "from agent_harness.main" tests/` **零命中**。所有 HTTP 用例
都是自建 `FastAPI()` + 中间件注入身份，从不经过 `main.py`：
  - `_api_auth_middleware` 的 JWT / API-key 解析
  - 限流 `_check_rate_limit`（**demo 分支在它之前 return → 结构绕过**）
  - `_AUTH_EXEMPT_PREFIXES/EXACT/V1` 的实际消费（豁免清单漂移）
  - `DISABLE_AUTH` 的身份注入（demo 访客 = `role="admin"`）
这三项能长期存活（公网 demo 访客即管理员、限流被绕过、豁免漂移），根因之一就是
**网关层无任何用例**。本文件用子进程启动真实 app（`DISABLE_AUTH` 是 import 期求值），
断言走真中间件的结果。

注意：不做 lifespan 启动（不用 `with TestClient(...)`），避免触发启动自检。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

_SCRIPT = r'''
import json, os, sys
sys.path.insert(0, os.environ["_SRC"])
from fastapi.testclient import TestClient
from agent_harness.main import app          # 真实管线
c = TestClient(app)                         # 不进 lifespan（不触发启动自检）
out = {}
out["health"] = c.get("/health").status_code
out["setup_config_write"] = c.post("/v1/setup/config", json={"llm": {"api_url": "https://evil.example/v1"}}).status_code
out["setup_config_read"] = c.get("/v1/setup/config").status_code
out["sessions_list"] = c.get("/v1/sessions").status_code
out["demo_auth"] = os.environ.get("HARNESS_DISABLE_AUTH", "")
print("__RESULT__" + json.dumps(out))
'''


def _run(disable_auth: str) -> dict:
    tmp = tempfile.mkdtemp(prefix="lingshu_pipeline_")
    env = dict(os.environ)
    env.update({
        "_SRC": str(SRC),
        "HARNESS_DISABLE_AUTH": disable_auth,
        "HARNESS_DATA_DIR": tmp,
        "HARNESS_CONFIG_DIR": tmp,
        "HARNESS_REPORTS_DIR": str(Path(tmp) / "reports"),
        "HARNESS_SESSION_DIR": str(Path(tmp) / "sessions"),
        "HARNESS_SKILLS_DIR": str(Path(tmp) / "skills"),
        "PYTHONPATH": str(SRC),
    })
    p = subprocess.run([sys.executable, "-c", _SCRIPT], env=env,
                       capture_output=True, text=True, timeout=180, cwd=str(SRC.parent))
    for line in (p.stdout or "").splitlines():
        if line.startswith("__RESULT__"):
            return json.loads(line[len("__RESULT__"):])
    raise AssertionError(
        "真实管线启动失败（stdlib 之外还缺依赖时也会到这里）:\n"
        f"stdout={p.stdout[-800:]}\nstderr={p.stderr[-800:]}"
    )


def test_real_pipeline_demo_mode_blocks_config_write():
    """demo（DISABLE_AUTH=1）下：真实中间件注入访客身份后，写服务器配置必须被拒。

    这条正是公网 demo 的 RCE 链第一环（访客可控 llm.api_url → 服务器按访客端点发 LLM 请求
    → agent 工具在部署主机上执行）。此前该断言只在自建 App 上测过，**从未经过真中间件**。
    """
    r = _run("1")
    assert r["demo_auth"] == "1"
    assert r["health"] == 200, f"豁免端点 /health 应 200，实测 {r['health']}"
    assert r["setup_config_write"] == 403, (
        f"demo 下写配置未被拒（实测 {r['setup_config_write']}）——RCE 链第一环在真实管线上是开的"
    )


def test_real_pipeline_without_auth_env_denies_protected_endpoints():
    """关掉 demo：未认证访问受保护端点必须被真中间件拦下（不是靠 handler 自觉）。"""
    r = _run("0")
    assert r["sessions_list"] in (401, 403), (
        f"未认证访问 /v1/sessions 应被拒，实测 {r['sessions_list']}（中间件可能没生效）"
    )
    assert r["setup_config_write"] in (401, 403), r["setup_config_write"]
