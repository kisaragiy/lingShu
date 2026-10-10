"""Prometheus 指标采集 — HTTP 请求计数 + Agent 耗时 + LLM Token 消耗。

添加到 FastAPI 中间件自动记录请求指标。
暴露 /metrics 端点供 Prometheus 抓取。
"""
from __future__ import annotations

import time
from typing import Callable

from prometheus_client import Counter, Histogram, Gauge, generate_latest, REGISTRY
from fastapi import Request, Response

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

TOOL_CALLS_TOTAL = Counter(
    "harness_tool_calls_total", "Tool invocation count by tool and outcome",
    ["tool", "status"],  # status: success | not_found | disabled | denied | needs_confirm | guard_unavailable | timeout | error
)

TOOL_CALL_DURATION = Histogram(
    "harness_tool_call_duration_seconds", "Tool invocation duration",
    ["tool"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)

TOOL_RESULTS_TOTAL = Counter(
    "harness_tool_results_total", "Tool result validation verdict",
    ["tool", "verdict"],  # verdict: ok | error | warning
)

# ── FastAPI 中间件 ─────────────────────────────────


def _route_label(request: Request) -> str:
    """取路由**模板**（如 `/v1/reports/{report_id}`）作为指标标签。

    必须在 `call_next` 之后读取 —— Router 匹配完成才写入 `scope["route"]`。
    未匹配（404/短路）时归一为 `<unmatched>`，**绝不回退到原始 URL**：
    原始 URL 里的 ID 会让每个唯一路径产生一条独立时间序列（高基数，Prometheus
    内存与查询性能的经典杀手）。
    """
    route = request.scope.get("route")
    return getattr(route, "path", None) or "<unmatched>"


async def metrics_middleware(request: Request, call_next: Callable) -> Response:
    """自动记录 HTTP 请求指标（含失败路径：短路 4xx 与未捕获异常 5xx）。"""
    method = request.method

    start = time.time()
    try:
        response: Response = await call_next(request)
    except Exception:
        # 未捕获异常：最外层 ServerErrorMiddleware 会把它转成 500，
        # 这里是唯一能记下它的地方。不记录 = 5xx 永远为 0（监控在最该报警处失明）。
        elapsed = time.time() - start
        label = _route_label(request)
        HTTP_REQUESTS_TOTAL.labels(method, label, "500").inc()
        HTTP_REQUEST_DURATION.labels(method, label).observe(elapsed)
        raise

    duration = time.time() - start
    label = _route_label(request)

    HTTP_REQUESTS_TOTAL.labels(method, label, response.status_code).inc()
    HTTP_REQUEST_DURATION.labels(method, label).observe(duration)

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


# ── 工具调用观测 ───────────────────────────────────

# 失败原因归类规则表：(error 文本特征, 归类标签)，按序匹配，首个命中者生效。
# 与 registry.call_tool 各失败分支的 error 前缀一一对应——改前缀必须同步改这里。
_FAILURE_RULES = (
    ("工具不存在", "not_found"),
    ("[开关拒绝]", "disabled"),
    ("[权限拒绝]", "denied"),
    ("[需要确认]", "needs_confirm"),
    ("[安全护栏不可用]", "guard_unavailable"),
)


def classify_tool_failure(error: str | None) -> str:
    """把工具失败错误归类为可聚合的 reason 标签。

    失败不是一坨：not_found（模型选错工具）/ denied（权限）/ disabled（配置）
    / timeout / error（工具自身 bug）在运维上是完全不同的动作，必须分开统计。
    未知原因统一归 "error"，本函数不抛异常。
    """
    text = error or ""
    for marker, label in _FAILURE_RULES:
        if marker in text:
            return label
    if "超时" in text or "timeout" in text.lower():
        return "timeout"
    return "error"


def record_tool_call(tool: str, status: str, duration_s: float = 0.0) -> None:
    """记录一次工具调用结果。status 取 classify_tool_failure 的返回值或 "success"。"""
    TOOL_CALLS_TOTAL.labels(tool, status).inc()
    if duration_s > 0:
        TOOL_CALL_DURATION.labels(tool).observe(duration_s)


def record_tool_result(tool: str, verdict: str) -> None:
    """记录工具**结果有效性**判定（与 `TOOL_CALLS_TOTAL` 互补）。

    `TOOL_CALLS_TOTAL` 只看「工具函数有没有抛异常 / 有没有被护栏拦」——但搜索后端
    不可用时的降级返回、空列表、内容里写着"搜索失败"都仍算 success。`verdict`
    （来自 `validate_result`）补上「结果到底有没有用」这一层。两者合起来才是完整的
    工具失败视图：**调用失败** vs **调用成功但结果无效**，运维动作完全不同。
    """
    TOOL_RESULTS_TOTAL.labels(tool, verdict or "unknown").inc()
