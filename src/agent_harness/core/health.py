"""
启动自检 — 全链路依赖健康检查。

提供 HealthCheck 核心 + 各依赖的 check 函数。
启动时自动运行，/health 端点轮询最新状态。
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import requests

logger = logging.getLogger(__name__)

# ── 数据类型 ──────────────────────────────────────


@dataclass
class CheckResult:
    """单个检查项的结果"""
    name: str
    status: str  # "up" | "degraded" | "down"
    detail: str = ""
    latency_ms: float = 0.0
    required: bool = False  # True = 必须可用，否则视为 down


import re as _re

_IP_RE = _re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')


def sanitize_detail(detail: str) -> str:
    """脱敏健康检查 detail —— 抹掉内网 IP/端口/主机，防 /health 泄露网络拓扑。"""
    if not detail:
        return detail
    # 先抹掉 IP:PORT 组合(整个 host:port)
    text = _re.sub(r'(?:\d{1,3}\.){3}\d{1,3}:\d+', '[internal-host]', detail)
    # 再抹掉裸 IP 地址(无端口)
    text = _IP_RE.sub("[internal-host]", text)
    # 抹掉裸露的 host=port 形式
    text = _re.sub(r'\(\S*?(?:host|address)\s*=\s*[^)]+\)', '', text)
    return text


@dataclass
class HealthReport:
    """完整的健康状态报告"""
    status: str = "healthy"  # "healthy" | "degraded" | "down"
    checks: list[CheckResult] = field(default_factory=list)
    uptime_s: float = 0.0
    version: str = ""
    started_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "version": self.version,
            "uptime_s": int(time.time() - self.started_at),
            "checks": [
                {
                    "name": c.name,
                    "status": c.status,
                    "detail": sanitize_detail(c.detail),
                    "latency_ms": round(c.latency_ms, 1),
                    "required": c.required,
                }
                for c in self.checks
            ],
        }


# ── 检查函数 ──────────────────────────────────────

_CHECKERS: list[Callable[[], CheckResult]] = []
_started_at = time.time()
_last_report: HealthReport | None = None


def check(name: str = "", required: bool = False):
    """装饰器：注册一个健康检查函数。"""
    def decorator(fn: Callable) -> Callable:
        _CHECKERS.append(fn)
        fn._check_name = name or fn.__name__.replace("check_", "").replace("_", " ")
        fn._check_required = required
        return fn
    return decorator


# ── 内置检查项 ────────────────────────────────────


@check(name="disk space", required=True)
def check_disk() -> CheckResult:
    """磁盘空间检查"""
    try:
        total, used, free = shutil.disk_usage("/")
        free_gb = free / (1024 ** 3)
        if free_gb < 1:
            return CheckResult("disk space", "down", f"仅剩 {free_gb:.1f}GB", required=True)
        if free_gb < 5:
            return CheckResult("disk space", "degraded", f"仅剩 {free_gb:.1f}GB", required=True)
        return CheckResult("disk space", "up", f"{free_gb:.1f}GB 可用", required=True)
    except Exception as e:
        return CheckResult("disk space", "degraded", str(e), required=True)


@check(name="comfyui")
def check_comfyui() -> CheckResult:
    """ComfyUI 服务连通性"""
    url = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")
    t0 = time.time()
    try:
        r = requests.get(f"{url}/system_stats", timeout=3)
        ms = (time.time() - t0) * 1000
        if r.status_code == 200:
            return CheckResult("comfyui", "up", f"连通 ({ms:.0f}ms)", latency_ms=ms)
        return CheckResult("comfyui", "degraded", f"HTTP {r.status_code}")
    except requests.ConnectionError:
        return CheckResult("comfyui", "down", "连接失败")
    except Exception as e:
        return CheckResult("comfyui", "down", str(e))


@check(name="ollama")
def check_ollama() -> CheckResult:
    """Ollama 模型服务"""
    url = os.environ.get("OLLAMA_URL", "http://172.22.175.253:11434")
    t0 = time.time()
    try:
        r = requests.get(f"{url}/api/tags", timeout=3)
        ms = (time.time() - t0) * 1000
        if r.status_code != 200:
            return CheckResult("ollama", "degraded", f"HTTP {r.status_code}")
        models = r.json().get("models", [])
        names = [m["name"] for m in models[:5]]
        return CheckResult("ollama", "up", f"在线 ({len(models)} 模型: {', '.join(names)})", latency_ms=ms)
    except requests.ConnectionError:
        return CheckResult("ollama", "down", "WSL 桥接可能不通")
    except Exception as e:
        return CheckResult("ollama", "down", str(e))


@check(name="vlm")
def check_vlm() -> CheckResult:
    """VLM 视觉模型 (qwen3-vl:8b)"""
    url = os.environ.get("OLLAMA_URL", "http://172.22.175.253:11434")
    t0 = time.time()
    try:
        r = requests.get(f"{url}/api/tags", timeout=3)
        ms = (time.time() - t0) * 1000
        if r.status_code != 200:
            return CheckResult("vlm", "degraded", f"Ollama 不可用，无法检测 VLM", latency_ms=ms)
        models = [m["name"] for m in r.json().get("models", [])]
        vl_models = [m for m in models if "vl" in m.lower()]
        if vl_models:
            return CheckResult("vlm", "up", f"可用: {', '.join(vl_models)}", latency_ms=ms)
        return CheckResult("vlm", "degraded", "未安装 VL 模型")
    except Exception as e:
        return CheckResult("vlm", "down", str(e))


@check(name="searxng")
def check_searxng() -> CheckResult:
    """SearXNG 搜索引擎"""
    url = os.environ.get("SEARXNG_URL", "http://127.0.0.1:4000")
    t0 = time.time()
    try:
        r = requests.get(f"{url}/health", timeout=3)
        ms = (time.time() - t0) * 1000
        if r.status_code == 200:
            return CheckResult("searxng", "up", f"连通 ({ms:.0f}ms)", latency_ms=ms)
        return CheckResult("searxng", "degraded", f"HTTP {r.status_code}")
    except requests.ConnectionError:
        return CheckResult("searxng", "down", "未启动")
    except Exception as e:
        return CheckResult("searxng", "down", str(e))


@check(name="vlm scorer")
def check_vlm_scorer() -> CheckResult:
    """VLM 审美评分 (port 8083)"""
    t0 = time.time()
    try:
        r = requests.get("http://127.0.0.1:8083/health", timeout=2)
        ms = (time.time() - t0) * 1000
        if r.status_code == 200:
            return CheckResult("vlm scorer", "up", f"连通 ({ms:.0f}ms)", latency_ms=ms)
        return CheckResult("vlm scorer", "down", f"HTTP {r.status_code}")
    except (requests.ConnectionError, Exception):
        return CheckResult("vlm scorer", "down", "未启动")


# ── 运行 ──────────────────────────────────────────


def run_health_checks() -> HealthReport:
    """运行所有注册的健康检查。"""
    from agent_harness import __version__

    results: list[CheckResult] = []
    for checker in _CHECKERS:
        try:
            result = checker()
            result.name = getattr(checker, "_check_name", checker.__name__)
            result.required = getattr(checker, "_check_required", False)
            results.append(result)
        except Exception as e:
            results.append(CheckResult(
                name=getattr(checker, "_check_name", checker.__name__),
                status="down",
                detail=str(e),
                required=getattr(checker, "_check_required", False),
            ))

    # 聚合状态
    required_down = any(r.status == "down" and r.required for r in results)
    any_down = any(r.status == "down" for r in results)
    any_degraded = any(r.status == "degraded" for r in results)

    if required_down:
        overall = "down"
    elif any_down or any_degraded:
        overall = "degraded"
    else:
        overall = "healthy"

    report = HealthReport(
        status=overall,
        checks=results,
        uptime_s=time.time() - _started_at,
        version=__version__,
        started_at=_started_at,
    )

    global _last_report
    _last_report = report

    return report


def get_cached_report() -> HealthReport:
    """返回最后一次检查结果，未检查过则立即运行。"""
    if _last_report is None:
        return run_health_checks()
    return _last_report
