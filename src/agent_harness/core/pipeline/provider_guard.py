"""provider 级熔断（**进程内**）—— 保护下游 HTTP 端点，不是任务终止条件。

与 `circuit_breaker.CircuitBreaker` 的区别（名字像，语义完全不同，别混）：
  - `CircuitBreaker`：**任务级进度熔断**。看 worker 输出指纹有没有变、token 烧了多少，
    一次 run 一个实例，tripped 就 finalize 整条任务。它不关心 endpoint 死活。
  - `ProviderGuard`：**下游健康熔断**。按 endpoint host 记失败次数，连续失败就快速
    失败后续调用，冷却后放行一次探测。它不管任务逻辑，只回答"这个 endpoint 现在
    还值得打吗"。

为什么值得存在：本仓 4 个 LLM 调用点的 timeout 是 **300 秒**。一个半死的 endpoint
会让每次调用干等五分钟，而调用点各自重试、互不知情 —— 这就是"技术上没把失败路径
堵死"的典型。熔断后同一个 endpoint 的后续调用毫秒级失败。

**边界（诚实声明，别在面试里吹成分布式熔断）**：
  - 状态在进程内存里，**不跨进程、不跨机器**。多 worker 部署时每个进程各算各的。
  - 进程重启即失效（不持久化）—— 这是有意的：熔断的意义是短时保护，不是长期封禁。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable
from urllib.parse import urlparse


class ProviderUnavailable(RuntimeError):
    """该 endpoint 正处在熔断中：不要发请求，稍后重试。

    ``retry_after`` 是建议的等待秒数；调用方拿它决定退避，或直接降级到备用端点。
    """

    def __init__(self, host: str, retry_after: float) -> None:
        self.host = host
        self.retry_after = max(0.0, float(retry_after))
        super().__init__(
            f"provider {host} 熔断中，{self.retry_after:.0f}s 后放行探测"
        )


def _host_of(url: str) -> str:
    parsed = urlparse(url or "")
    return parsed.netloc or (url or "")[:80]


def is_provider_failure(status_code: int) -> bool:
    """这个 HTTP 状态码是否说明「provider 有问题」。

    5xx / 429 = provider 侧问题（该退避）；
    4xx = 我的请求有问题（参数错、鉴权错），熔断它对谁都没好处，反而会把一个
    健康端点封死 —— 所以**不计入** provider 失败。
    """
    return status_code >= 500 or status_code == 429


class ProviderGuard:
    """按 host 累计失败、冷却后半开探测的进程内熔断器。"""

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown: float = 60.0,
        max_cooldown: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold 必须 >= 1")
        self.failure_threshold = int(failure_threshold)
        self.cooldown = float(cooldown)
        self.max_cooldown = float(max_cooldown)
        self._clock = clock
        self._lock = threading.Lock()
        # host → {"failures", "opened_at", "cooldown", "probing"}
        self._hosts: dict[str, dict[str, Any]] = {}

    # ── 状态查询 ──────────────────────────────────────────────────────────
    def _slot(self, host: str) -> dict[str, Any]:
        slot = self._hosts.get(host)
        if slot is None:
            slot = {"failures": 0, "opened_at": None,
                    "cooldown": self.cooldown, "probing": False}
            self._hosts[host] = slot
        return slot

    def state_of(self, url: str) -> str:
        """closed / open / half_open。"""
        host = _host_of(url)
        with self._lock:
            slot = self._hosts.get(host)
            if not slot or slot["failures"] < self.failure_threshold:
                return "closed"
            if slot["opened_at"] is None:
                return "open"
            if self._clock() - slot["opened_at"] < slot["cooldown"]:
                return "open"
            return "half_open"

    # ── 调用前：熔断中直接拒绝 ────────────────────────────────────────────
    def before(self, url: str) -> None:
        """熔断中抛 :class:`ProviderUnavailable`；半开时**只放行一个**探测。"""
        host = _host_of(url)
        with self._lock:
            slot = self._hosts.get(host)
            if not slot or slot["failures"] < self.failure_threshold:
                return
            if slot["opened_at"] is None:
                slot["opened_at"] = self._clock()
            elapsed = self._clock() - slot["opened_at"]
            if elapsed < slot["cooldown"]:
                raise ProviderUnavailable(host, slot["cooldown"] - elapsed)
            # 冷却结束：半开。并发时只放一个探测，其余继续快速失败。
            if slot["probing"]:
                raise ProviderUnavailable(host, 1.0)
            slot["probing"] = True

    # ── 调用后：记账 ──────────────────────────────────────────────────────
    def record(self, url: str, ok: bool) -> None:
        host = _host_of(url)
        with self._lock:
            slot = self._slot(host)
            if slot["probing"]:
                # 半开探测的结论：成功就彻底关闭，失败就重新打开并翻倍冷却。
                slot["probing"] = False
                if ok:
                    slot["failures"] = 0
                    slot["opened_at"] = None
                    slot["cooldown"] = self.cooldown
                else:
                    slot["failures"] = self.failure_threshold
                    slot["opened_at"] = self._clock()
                    slot["cooldown"] = min(slot["cooldown"] * 2, self.max_cooldown)
                return
            if ok:
                slot["failures"] = 0
                slot["opened_at"] = None
                return
            slot["failures"] += 1
            if slot["failures"] >= self.failure_threshold:
                slot["opened_at"] = self._clock()
                slot["cooldown"] = self.cooldown

    # ── 观测 / 测试用 ────────────────────────────────────────────────────
    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {h: dict(s) for h, s in self._hosts.items()}

    def reset(self) -> None:
        with self._lock:
            self._hosts.clear()


_default_guard = ProviderGuard()


def default_guard() -> ProviderGuard:
    """进程级默认 guard —— 所有走 `guarded_post` 的调用点共享它。"""
    return _default_guard


def guarded_post(url: str, *, guard: ProviderGuard | None = None, **kwargs: Any) -> Any:
    """`requests.post` 的熔断包装。

    熔断中直接抛 :class:`ProviderUnavailable`（**不发请求**）；网络异常 / 5xx / 429
    记一次失败并把异常原样抛给调用方（吞异常是调用方的选择，不是这里的选择）。
    """
    guard = guard or _default_guard
    guard.before(url)
    try:
        import requests

        resp = requests.post(url, **kwargs)
    except Exception:
        guard.record(url, ok=False)          # 连不上 / 超时 → provider 侧故障
        raise
    guard.record(url, ok=not is_provider_failure(getattr(resp, "status_code", 0)))
    return resp
