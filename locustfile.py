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
