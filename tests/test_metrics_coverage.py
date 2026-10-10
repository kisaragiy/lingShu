"""网关层指标覆盖 —— 被中间件短路拒绝的请求（401/429）与未捕获异常（5xx）必须进指标。

背景（2026-09-11 实测）：对真实 :8788 打 41 个请求，其中 8 个 401 在 /metrics 里
**完全缺失**。根因：Starlette 的 `add_middleware` 是 insert(0)，**后注册者在外层**，
而 metrics 中间件注册在认证中间件之前 → 处在内层 → 认证短路返回的响应根本到不了它。

这不是美观问题：401/429 突增是"有人在爆破/被限流"的第一信号，恰恰是安全监控最该
报警的地方。metrics 失明 = 在最该看见的地方看不见。

同理，未捕获异常由最外层的 ServerErrorMiddleware 转成 500，若 metrics 不做异常路径
记录，5xx 也永远是 0。

本文件走**真实管线**（agent_harness.main.app），子进程启动（DISABLE_AUTH 是 import 期
求值），断言中间件栈的真实行为 —— 自建 FastAPI() 测不出中间件相对顺序。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

_SCRIPT = r'''
import json, os, sys
sys.path.insert(0, os.environ["_SRC"])
os.environ.pop("HARNESS_DISABLE_AUTH", None)   # 强制认证开启：401 才有意义

from fastapi.testclient import TestClient
from agent_harness.main import app

# raise_server_exceptions=False：未捕获异常走真实 500 路径而不是抛到测试里
c = TestClient(app, raise_server_exceptions=False)

out = {}
out["unauth_status"] = c.get("/v1/sessions").status_code      # 期望 401（认证短路）
out["nope_status"] = c.get("/v1/definitely-not-here").status_code
out["ok_status"] = c.get("/health").status_code               # 对照组：期望 200

# 注入一个必然抛异常的路由，验证 5xx 路径
@app.get("/_metrics_boom")
async def _metrics_boom():
    raise RuntimeError("boom-for-metrics-test")

out["boom_status"] = c.get("/_metrics_boom").status_code      # 期望 500

out["metrics"] = c.get("/metrics").text
print("__RESULT__" + json.dumps(out))
'''


def _run_pipeline():
    env = dict(os.environ)
    env["_SRC"] = str(SRC)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=180,
    )
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("__RESULT__")), None)
    assert line, f"子进程未产出结果。stdout尾巴:\n{proc.stdout[-2000:]}\nstderr尾巴:\n{proc.stderr[-2000:]}"
    return json.loads(line[len("__RESULT__"):])


def _sample(metrics_text: str, endpoint_fragment: str, status: str) -> float:
    """从 /metrics 文本里取某端点某状态码的计数值（取不到返回 0）。"""
    for ln in metrics_text.splitlines():
        if ln.startswith("harness_http_requests_total{") and f'endpoint="{endpoint_fragment}"' in ln \
                and f'status="{status}"' in ln:
            return float(ln.rsplit(" ", 1)[1])
    return 0.0


def _series_count(metrics_text: str, endpoint_fragment: str) -> int:
    """某 endpoint 标签下的时间序列条数（高基数探测器）。"""
    return sum(
        1 for ln in metrics_text.splitlines()
        if ln.startswith("harness_http_requests_total{") and f'endpoint="{endpoint_fragment}"' in ln
    )


def test_pipeline_reachable():
    """前置：真管线可请求（不然下面的断言没有意义）。"""
    r = _run_pipeline()
    assert r["ok_status"] == 200
    assert r["unauth_status"] == 401, "认证应开启并拒绝匿名 /v1/sessions"


def test_401_from_auth_middleware_is_counted():
    """认证中间件短路返回的 401 必须进指标（监控盲区回归锁）。

    注意落在 `<unmatched>` 而不是 `/v1/sessions`：认证短路发生在 Router 之前，
    `scope["route"]` 从未被写入。这是**有意**的归一——见高基数测试。
    """
    r = _run_pipeline()
    assert r["unauth_status"] == 401
    assert _sample(r["metrics"], "<unmatched>", "401") >= 1, (
        "401 未进指标——metrics 中间件处在认证中间件内侧（注册顺序被改回去了？）"
    )


def test_unmatched_paths_do_not_explode_cardinality():
    """高基数防护：不同未匹配路径必须聚合到同一条时间序列。

    若回退到原始 URL，攻击者用随机路径刷 404 就能让 Prometheus 每个请求建一条
    序列（内存/查询性能杀手）。这里断言两条不同路径只产出 1 条 <unmatched> 序列。
    """
    r = _run_pipeline()
    assert r["nope_status"] in (401, 404)
    # /v1/sessions 与 /v1/definitely-not-here 都短路 → 同一条 <unmatched> 序列
    assert _series_count(r["metrics"], "<unmatched>") == 1, (
        "未匹配路径产生了多条序列——原始 URL 泄漏进指标标签了"
    )
    # 且原始路径不得出现在标签里
    assert 'endpoint="/v1/definitely-not-here"' not in r["metrics"]


def test_unhandled_exception_is_counted_as_500():
    """未捕获异常（500）必须进指标。"""
    r = _run_pipeline()
    assert r["boom_status"] == 500
    assert _sample(r["metrics"], "/_metrics_boom", "500") >= 1, (
        "500 未进指标——metrics 中间件缺少异常路径记录"
    )


def test_success_path_still_counted():
    """对照组：正常 200 照旧被计数（防止把顺序改坏）。"""
    r = _run_pipeline()
    assert r["ok_status"] == 200
    assert _sample(r["metrics"], "/health", "200") >= 1
