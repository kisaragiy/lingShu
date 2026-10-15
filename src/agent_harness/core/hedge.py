"""provider 对冲 / 降级（P2-4 ②）—— 主 provider 挂了自动 failover，过 P95 就发第二副本。

痛点
----
单 provider 一抖动 / 降级 / 欠费，整条 LLM 步骤就挂；P99 又常被一次长 prompt 拖爆。
「重试」（``core.retry``）只覆盖**失败后**重试，覆盖不了**慢**（超时前它一直等），也覆盖不了
**换 provider**。

落点（小而真、可离线确定性测）
------------------------------
* :func:`failover`：按序试多个 provider，主挂（抛异常 / 返回空）→ 顺次切下一个。
* :func:`should_hedge`：纯决策 —— 观测 P95 已过（``elapsed >= p95 * factor``）→ 该对冲。
* :func:`hedged_call`：真实对冲执行 —— 先发 primary；**预算内**完成直接用；**超预算**则再发
  一个 **secondary 副本**，取先成功者；primary **抛异常**则直接 failover 到 secondary。

执行走可注入的 ``Executor``（``submit(fn) -> Future``）：默认 :class:`_ThreadExecutor`
（真线程池），测试注入 :class:`FakeExecutor`（预置 ``concurrent.futures.Future``）→ 全程
确定性、不触网、不 sleep 竞速。
"""
from __future__ import annotations

import concurrent.futures as cf
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

ProviderFn = Callable[[], Any]


# ── 结果 ──────────────────────────────────────────────────────

@dataclass
class ProviderResult:
    """一次（可能带对冲/降级的）provider 调用的结果。"""
    ok: bool
    value: Any = None
    provider: str | None = None
    error: str | None = None
    hedged: bool = False
    attempts: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "provider": self.provider, "error": self.error,
            "hedged": self.hedged, "attempts": self.attempts, "errors": list(self.errors),
        }


# ── failover（顺序降级）───────────────────────────────────────

def failover(
    providers: list[tuple[str, ProviderFn]],
    *,
    is_ok: Callable[[Any], bool] | None = None,
    on_all_failed: Callable[[], Any] | None = None,
) -> ProviderResult:
    """按序 failover：主挂（抛异常或 ``is_ok`` 为假）→ 试下一个。

    ``is_ok`` 默认 ``lambda v: v is not None``。全失败 → 调 ``on_all_failed()`` 兜底
    （也没有则返回 ``ok=False``）。
    """
    is_ok = is_ok or (lambda v: v is not None)
    errors: list[str] = []
    attempts = 0

    for name, fn in providers:
        attempts += 1
        try:
            value = fn()
        except Exception as e:                      # provider 异常 = 失败，切下一个
            errors.append(f"{name}: {type(e).__name__}: {e}")
            continue
        if is_ok(value):
            return ProviderResult(ok=True, value=value, provider=name,
                                  attempts=attempts, errors=errors)
        errors.append(f"{name}: unsuitable result")

    value = on_all_failed() if on_all_failed else None
    return ProviderResult(ok=value is not None, value=value, provider=None,
                          error="all_providers_failed", attempts=attempts,
                          errors=errors)


# ── 对冲决策（纯函数）─────────────────────────────────────────

def should_hedge(p95_s: float, elapsed_s: float, *, factor: float = 1.0) -> bool:
    """观测 P95 已过 → 该发对冲副本。``elapsed_s >= p95_s * factor``。"""
    return elapsed_s >= p95_s * factor


# ── Executor 抽象 ─────────────────────────────────────────────

class _ThreadExecutor:
    """默认真线程池（2 worker：primary + 至多 1 个对冲副本）。"""

    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=2)

    def submit(self, fn: ProviderFn) -> cf.Future:
        return self._pool.submit(fn)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False)


class FakeExecutor:
    """确定性测试替身：``submit`` 依次弹出**预置**的 future（不真跑 fn）。"""

    def __init__(self, futures: list[cf.Future]) -> None:
        self._futures = list(futures)
        self.submitted: list[ProviderFn] = []

    def submit(self, fn: ProviderFn) -> cf.Future:
        self.submitted.append(fn)
        if not self._futures:
            f: cf.Future = cf.Future()
            f.set_exception(RuntimeError("FakeExecutor: no future queued"))
            return f
        return self._futures.pop(0)

    def shutdown(self) -> None:  # 与 _ThreadExecutor 同形
        pass


def _completed(value: Any) -> cf.Future:
    f: cf.Future = cf.Future()
    f.set_result(value)
    return f


def _errored(exc: Exception) -> cf.Future:
    f: cf.Future = cf.Future()
    f.set_exception(exc)
    return f


# ── 对冲执行 ──────────────────────────────────────────────────

def hedged_call(
    primary: ProviderFn,
    secondary: ProviderFn,
    *,
    p95_s: float,
    factor: float = 1.0,
    executor: Any = None,
    primary_name: str = "primary",
    secondary_name: str = "hedge",
) -> ProviderResult:
    """带对冲 + failover 的 provider 调用。

    逻辑：
      1. 发 primary；在 ``p95_s * factor`` 预算内完成 → 直接用（**不发**第二副本，省一次调用）；
      2. 超预算（``TimeoutError``）→ 发 secondary 副本，取**先成功**者（``hedged=True``）；
      3. primary **抛异常** → 直接 failover 给 secondary（``hedged=False``）；
      4. 全失败 → ``ok=False``，``errors`` 留痕。
    """
    own = executor is None
    ex = executor if executor is not None else _ThreadExecutor()
    budget = max(0.0, p95_s * factor)
    try:
        f_primary = ex.submit(primary)
        try:
            value = f_primary.result(timeout=budget)
            return ProviderResult(ok=True, value=value, provider=primary_name, attempts=1)
        except cf.TimeoutError:
            # 超 P95 → 对冲：再发一个副本，取先成功者
            f_secondary = ex.submit(secondary)
            done, _ = cf.wait([f_primary, f_secondary],
                              return_when=cf.FIRST_COMPLETED, timeout=budget)
            # 优先主 provider（同名同请求，谁先成就用谁）
            for fut, name in ((f_primary, primary_name), (f_secondary, secondary_name)):
                if fut in done:
                    try:
                        return ProviderResult(ok=True, value=fut.result(),
                                              provider=name, hedged=True, attempts=2)
                    except Exception:
                        continue
            # 无「已完成且成功」者 → 再各等一次预算，任取成功者
            errors: list[str] = []
            for fut, name in ((f_secondary, secondary_name), (f_primary, primary_name)):
                try:
                    return ProviderResult(ok=True, value=fut.result(timeout=budget),
                                          provider=name, hedged=True, attempts=2)
                except Exception as e:
                    errors.append(f"{name}: {type(e).__name__}: {e}")
            return ProviderResult(ok=False, provider=None, error="hedge_exhausted",
                                  hedged=True, attempts=2, errors=errors)
        except Exception as e:
            # primary 异常 → failover 到 secondary
            errors = [f"{primary_name}: {type(e).__name__}: {e}"]
            try:
                value = ex.submit(secondary).result(timeout=budget)
                return ProviderResult(ok=True, value=value, provider=secondary_name,
                                      attempts=2, errors=errors)
            except Exception as e2:
                errors.append(f"{secondary_name}: {type(e2).__name__}: {e2}")
                return ProviderResult(ok=False, provider=None, error="failover_exhausted",
                                      attempts=2, errors=errors)
    finally:
        if own:
            ex.shutdown()
