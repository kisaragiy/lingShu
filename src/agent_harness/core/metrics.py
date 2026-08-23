"""Prometheus 指标采集 — HTTP 请求计数 + Agent 耗时 + LLM Token 消耗。

添加到 FastAPI 中间件自动记录请求指标。
暴露 /metrics 端点供 Prometheus 抓取。
"""
from __future__ import annotations

import time
from typing import Callable

from prometheus_client import Counter, Histogram, Gauge, generate_latest, REGISTRY
from fastapi import Request, Response
from fastapi.routing import APIRoute

# ── 指标定义 ──────────────────────────────────────

HTTP_REQUESTS_TOTAL = Counter(
    "harness_http_requests_total", "Total HTTP requests",
    ["method", "endpoint", "status"],
)

HTTP_REQUEST_DURATION = Histogram(
    "harness_http_request_duration_seconds", "HTTP request duration",
    ["method", "endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

AGENT_CALLS_TOTAL = Counter(
    "harness_agent_calls_total", "Agent call count",
    ["worker_type"],
)

AGENT_CALL_DURATION = Histogram(
    "harness_agent_call_duration_seconds", "Agent call duration",
    ["worker_type"],
    buckets=(1, 5, 10, 30, 60, 120, 300),
)

LLM_CALLS_TOTAL = Counter(
    "harness_llm_calls_total", "LLM API call count",
    ["model", "status"],
)

LLM_TOKENS_TOTAL = Counter(
    "harness_llm_tokens_total", "Total tokens consumed",
    ["model", "type"],  # type: prompt | completion
)

ACTIVE_SESSIONS = Gauge(
    "harness_active_sessions", "Currently active sessions",
)

# ── FastAPI 中间件 ─────────────────────────────────


async def metrics_middleware(request: Request, call_next: Callable) -> Response:
    """自动记录 HTTP 请求指标。"""
    method = request.method
    # 获取路由路径（而非原始 URL，避免高基数）
    route: APIRoute | None = request.scope.get("route")
    path = route.path if route else request.url.path

    start = time.time()
    response: Response = await call_next(request)
    duration = time.time() - start

    HTTP_REQUESTS_TOTAL.labels(method, path, response.status_code).inc()
    HTTP_REQUEST_DURATION.labels(method, path).observe(duration)

    return response


# ── 指标端点 ───────────────────────────────────────


async def metrics_endpoint() -> Response:
    """GET /metrics — Prometheus 抓取端点。"""
    return Response(
        generate_latest(REGISTRY).decode("utf-8"),
        media_type="text/plain; charset=utf-8",
    )


# ── 辅助函数（代码内调用） ──────────────────────


def record_llm_call(model: str, prompt_tokens: int, completion_tokens: int, success: bool = True):
    """记录 LLM API 调用。"""
    status = "success" if success else "error"
    LLM_CALLS_TOTAL.labels(model, status).inc()
    LLM_TOKENS_TOTAL.labels(model, "prompt").inc(prompt_tokens)
    LLM_TOKENS_TOTAL.labels(model, "completion").inc(completion_tokens)


def record_agent_call(worker_type: str, duration_s: float):
    """记录 Agent（Worker）调用。"""
    AGENT_CALLS_TOTAL.labels(worker_type).inc()
    AGENT_CALL_DURATION.labels(worker_type).observe(duration_s)
