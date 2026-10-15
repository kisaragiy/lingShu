"""Load testing for LingShu Agent API.

用法（两步，缺一不可）：

    # 1) 放宽限流起服务 —— 默认 100 req/min/IP 会让 50 并发的压测立刻 429，
    #    测出来的是限流器而不是服务性能。
    HARNESS_RATE_LIMIT=100000 ./.venv/Scripts/python scripts/run_server_8788.py

    # 2) 压测
    ./.venv/Scripts/python -m locust -f locustfile.py --host http://127.0.0.1:8788 \
        --headless -u 50 -r 5 --run-time 60s --csv=loadtest

认证：`/v1/*` 需要 `X-API-Key`。压测端与 API 同机，直接读服务端 token 文件
（值不打印、不写盘、不进仓库）。读不到时退化为只压公开端点。
"""
import os
import sys
from pathlib import Path

from locust import HttpUser, task, between

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _api_key() -> str:
    """取本地 API token 供压测使用；不打印、不写盘。"""
    try:
        from agent_harness.core.auth.api_security import load_or_generate_token
        return load_or_generate_token()
    except Exception:
        return os.environ.get("HARNESS_BENCH_API_KEY", "")


_API_KEY = _api_key()
_AUTH: dict[str, str] = {"X-API-Key": _API_KEY} if _API_KEY else {}


class LingShuUser(HttpUser):
    wait_time = between(1, 3)

    # ── 公开端点（无需认证）──

    @task(3)
    def health_check(self):
        self.client.get("/health")

    @task(2)
    def cs_demo_page(self):
        self.client.get("/cs-demo")

    @task(1)
    def main_page(self):
        self.client.get("/")

    @task(1)
    def knowledge_qa(self):
        self.client.get("/knowledge-qa")

    # ── 认证端点（/v1/*）──

    @task(5)
    def cs_chat(self):
        self.client.post(
            "/v1/cs/chat",
            json={"message": "查一下我的订单"},
            headers={"Content-Type": "application/json", **_AUTH},
        )

    @task(2)
    def cs_chat_stream(self):
        self.client.post(
            "/v1/cs/chat/stream",
            json={"message": "推荐一款耳机"},
            headers={"Content-Type": "application/json", **_AUTH},
        )

    @task(1)
    def metrics_scrape(self):
        """Prometheus 拉取端点也在负载里 —— 它自身不能被压测时拖垮。"""
        self.client.get("/metrics")


# ═══════════════════════════════════════════════════════════════════════════
# SLO 定义 + 检查（P2-4 ①）—— 压测不只是报数字，还要对「多少算好」下判据。
# ═══════════════════════════════════════════════════════════════════════════
# 目标来自 `agent_harness.core.slo`（纯标准库、可单测；见 tests/test_slo.py）。
# 运行结束时按端点把 locust 的分位喂给 SLO 判定；越线打印告警，默认**不**改变退出码
# （避免误卡 CI）—— 需要门禁时设 `HARNESS_SLO_ENFORCE=1`，越线即让 locust 退出码非零。

from locust import events  # noqa: E402

# locust stat 名（请求路径）→ SLO 目标键 的映射（子串匹配，宽松点以免版本差异）
_SLO_ENDPOINT_MAP = {
    "/health": "health",
    "/metrics": "metrics",
    "/v1/cs/chat/stream": "cs_stream",
    "/v1/cs/chat": "cs_chat",
}


def _slo_target_for(stat_name: str):
    from agent_harness.core import slo as _slo
    for needle, key in _SLO_ENDPOINT_MAP.items():
        if needle in stat_name:
            return _slo.DEFAULT_SLO_TARGETS.get(key)
    return None


@events.quitting.add_listener
def _check_slo_on_quit(environment, **_kw):
    """压测结束时对每个有 SLO 的端点判分位；越线打印，必要时设非零退出码。"""
    try:
        from agent_harness.core import slo as _slo
    except Exception:  # slo 不可用时不拖垮压测
        return
    breaches = []
    for (name, _method), stat in environment.stats.entries.items():
        target = _slo_target_for(name)
        if target is None or stat.num_requests == 0:
            continue
        err_rate = (stat.num_failures / stat.num_requests) if stat.num_requests else None
        res = _slo.check_percentiles(
            target,
            p50=stat.get_response_time_percentile(0.50),
            p95=stat.get_response_time_percentile(0.95),
            p99=stat.get_response_time_percentile(0.99),
            error_rate=err_rate,
        )
        mark = "✅" if res["passed"] else "❌"
        print(f"[SLO] {mark} {name}  {res['observed']}")
        breaches.extend({"endpoint": name, **b} for b in res["breaches"])

    if breaches:
        print("[SLO] ⚠️  越线:")
        for b in breaches:
            print(f"       {b['endpoint']} {b['metric']} {b['observed']} > {b['limit']}")
        if os.environ.get("HARNESS_SLO_ENFORCE") == "1":
            environment.process_exit_code = 1
    else:
        print("[SLO] ✅ 所有已定义端点达标")
