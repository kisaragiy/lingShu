"""LWP18 — core 模块评测（角色专属接口 + 行为评测 + 可替换实现）。

对标 **MESH-Harness**（arXiv:2610.05300, *Self-Improving Agent Harnesses via
Bandit-Guided Compositional Evolution*）。该论文的核心结构主张：

    "organizes each harness into functional modules with **explicit role-specific
     interfaces**, allowing alternative implementations of each module to be
     **substituted and recombined**."

即 —— 把 harness 拆成若干功能模块，**每个模块有显式的「角色专属接口」**，于是
「同一角色」的不同实现可以被**替换 / 重组合**。论文用 full-covariance LinUCB +
mixed-start coordinate ascent 在这个可替换空间里做 bandit 组合搜索（PLAN LWP18
明确 **不做**：个人项目负收益）。

本模块只取那**最小、可离线、确定性**的一半，把「接口可替换」落成可机械验证的形状：

  1. **角色专属接口**（:class:`RetryModule` / :class:`DegradationModule` /
     :class:`BudgetModule` / :class:`CircuitBreakerModule`）—— 每个是一个
     :class:`typing.Protocol`，只声明「这个角色该提供什么」，不绑定实现。
  2. **行为探针**（behavioral probe）—— 每个接口配一组「**触发一次、断言行为**」的
     探针（PLAN LWP18「每模块定义接口 + 行为评测（触发一次，断言行为）」）。
  3. **适配器**（adapter）—— 把灵枢 ``core/`` 里**真实存在**的实现接到接口上
     （:class:`LingShuModules`）。这是「替换实现」的物理落点：换适配器 = 换实现。
  4. **差分判据**（:func:`distinguishes`）—— 同一套评测喂「好实现」与「坏实现」，
     评测必须**能区分二者**（PLAN LWP18 验收：「换一个模块实现，评测能区分好坏」）。

用法::

    from agent_harness.core.module_eval import evaluate_all, LingShuModules
    report = evaluate_all(LingShuModules())
    report.passed      # 灵枢四模块是否全部通过全部探针
    report.score       # 通过率 [0,1]

设计约束
--------
* **纯标准库**、无网络、无 LLM、确定性（同输入 → 同报告，``to_dict`` 可 diff）。
* **只加不改**：不触碰任何现有模块的 API；真实实现走**延迟 import**，本模块自身
  导入零重依赖。
* 探针**只触发一次**并断言可观测行为；不测实现细节、不度量时延（避免 flaky）。

不做
----
* 不上 bandit 组合搜索（MESH-Harness 的 LinUCB / coordinate ascent）——PLAN LWP18 不做。
* 不做自动「择优替换」；本模块只**评测并区分**，决策权在人。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable


# ═══════════════════════════════════════════════════════════════════════════
# 一、角色专属接口（role-specific interfaces）
#     只声明角色契约，不绑定任何实现 —— 这是「实现可替换」的抽象边界。
# ═══════════════════════════════════════════════════════════════════════════


@runtime_checkable
class RetryModule(Protocol):
    """重试角色：给定一个可调用对象，返回「带重试语义」的包装。"""

    def wrap(self, fn: Callable, *, max_attempts: int, base_delay: float = 0.0) -> Callable:
        ...


@runtime_checkable
class DegradationModule(Protocol):
    """降级角色：按 preferred → fallback → on_failure 依次尝试，返回首个成功结果。"""

    def run(
        self,
        capability: str,
        *,
        preferred_fn: Callable | None = None,
        fallback_fns: list[Callable] | None = None,
        on_failure: Callable | None = None,
        **kwargs: Any,
    ) -> Any:
        ...


@runtime_checkable
class BudgetModule(Protocol):
    """预算角色：按 token 消耗给出预算级别（ok/warn/ask/stop）。"""

    def new_budget(self, soft_limit: int) -> Any:
        ...

    def add(self, budget: Any, tokens: int) -> str:
        ...

    def can_continue(self, budget: Any) -> bool:
        ...


@runtime_checkable
class CircuitBreakerModule(Protocol):
    """熔断角色：追踪 token / 无进展，判断是否需终止。"""

    def new_breaker(self, max_tokens: int = 100_000, max_no_progress: int = 5) -> Any:
        ...

    def add_tokens(self, breaker: Any, n: int) -> None:
        ...

    def record_output(self, breaker: Any, output: str) -> None:
        ...

    def tripped(self, breaker: Any) -> bool:
        ...


# ═══════════════════════════════════════════════════════════════════════════
# 二、灵枢真实实现适配器（把现有 core/ 模块接到上面的接口）
#     延迟 import：本模块自身导入零重依赖；真实模块只在被调用时加载。
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class LingShuRetry:
    """适配 ``agent_harness.core.retry.with_retry``。

    ``base_delay`` 默认 0 → ``max_delay`` 也传 0，使退避为 0（行为不变，只是不睡）；
    重试**语义**（尝试次数 / 可重试异常过滤）完全由真实实现决定。
    """

    def wrap(self, fn: Callable, *, max_attempts: int, base_delay: float = 0.0) -> Callable:
        from agent_harness.core.retry import with_retry

        return with_retry(
            max_attempts=max_attempts,
            base_delay=base_delay,
            max_delay=base_delay,
        )(fn)


@dataclass
class LingShuDegradation:
    """适配 ``agent_harness.core.degradation.call_with_degradation``。"""

    def run(
        self,
        capability: str,
        *,
        preferred_fn: Callable | None = None,
        fallback_fns: list[Callable] | None = None,
        on_failure: Callable | None = None,
        **kwargs: Any,
    ) -> Any:
        from agent_harness.core.degradation import call_with_degradation

        return call_with_degradation(
            capability,
            preferred_fn=preferred_fn,
            fallback_fns=fallback_fns,
            on_failure=on_failure,
            **kwargs,
        )


@dataclass
class LingShuBudget:
    """适配 ``agent_harness.core.budget.TokenBudget``（枚举 → 字符串级别）。"""

    def new_budget(self, soft_limit: int) -> Any:
        from agent_harness.core.budget import TokenBudget

        return TokenBudget(soft_limit=soft_limit)

    def add(self, budget: Any, tokens: int) -> str:
        return budget.add(tokens).value

    def can_continue(self, budget: Any) -> bool:
        return bool(budget.can_continue(budget.check()))


@dataclass
class LingShuCircuitBreaker:
    """适配 ``agent_harness.core.pipeline.circuit_breaker.CircuitBreaker``。"""

    def new_breaker(self, max_tokens: int = 100_000, max_no_progress: int = 5) -> Any:
        from agent_harness.core.pipeline.circuit_breaker import CircuitBreaker

        return CircuitBreaker(max_tokens=max_tokens, max_no_progress=max_no_progress)

    def add_tokens(self, breaker: Any, n: int) -> None:
        breaker.add_tokens(n)

    def record_output(self, breaker: Any, output: str) -> None:
        breaker.record_output("probe", output)

    def tripped(self, breaker: Any) -> bool:
        return bool(breaker.check().get("tripped"))


@dataclass
class LingShuModules:
    """灵枢四模块的**默认（真实）实现**集合 —— 一个可替换的 bundle。

    把其中任一字段换成别的实现，即「换一个模块实现」；评测应能区分好坏。
    """

    retry: RetryModule = field(default_factory=LingShuRetry)
    degradation: DegradationModule = field(default_factory=LingShuDegradation)
    budget: BudgetModule = field(default_factory=LingShuBudget)
    circuit_breaker: CircuitBreakerModule = field(default_factory=LingShuCircuitBreaker)


# ═══════════════════════════════════════════════════════════════════════════
# 三、评测数据模型
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class ProbeResult:
    """单条行为探针的结果。"""

    name: str
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "passed": bool(self.passed), "detail": self.detail}


@dataclass
class ModuleReport:
    """单模块评测报告。"""

    module: str
    probes: list[ProbeResult]

    @property
    def score(self) -> float:
        """通过率 [0,1]；无探针记 0.0（避免 0/0）。"""
        if not self.probes:
            return 0.0
        return sum(1 for p in self.probes if p.passed) / len(self.probes)

    @property
    def passed(self) -> bool:
        return bool(self.probes) and all(p.passed for p in self.probes)

    @property
    def failed_probes(self) -> list[str]:
        return [p.name for p in self.probes if not p.passed]

    def to_dict(self) -> dict:
        return {
            "module": self.module,
            "passed": self.passed,
            "score": round(self.score, 4),
            "failed_probes": self.failed_probes,
            "probes": [p.to_dict() for p in self.probes],
        }


@dataclass
class EvalReport:
    """全模块评测报告。"""

    modules: list[ModuleReport]

    @property
    def score(self) -> float:
        if not self.modules:
            return 0.0
        return sum(m.score for m in self.modules) / len(self.modules)

    @property
    def passed(self) -> bool:
        return bool(self.modules) and all(m.passed for m in self.modules)

    def module(self, name: str) -> ModuleReport:
        for m in self.modules:
            if m.module == name:
                return m
        raise KeyError(name)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "score": round(self.score, 4),
            "modules": [m.to_dict() for m in self.modules],
        }


# ═══════════════════════════════════════════════════════════════════════════
# 四、行为探针（触发一次、断言行为）
# ═══════════════════════════════════════════════════════════════════════════


def _ok(name: str, cond: bool, detail: str = "") -> ProbeResult:
    return ProbeResult(name, bool(cond), detail)


def probe_retry(adapter: RetryModule) -> list[ProbeResult]:
    """重试角色：瞬态恢复 / 耗尽重抛 / 不可重试直通。"""
    results: list[ProbeResult] = []

    # R1 瞬态恢复：前两次失败、第三次成功 → 返回成功值，且恰好调用 max_attempts 次。
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("transient")
        return "recovered"

    try:
        val = adapter.wrap(flaky, max_attempts=3)()
        results.append(_ok("retry.transient_recovery",
                           val == "recovered" and calls["n"] == 3,
                           f"val={val!r} calls={calls['n']}"))
    except Exception as e:  # noqa: BLE001
        results.append(_ok("retry.transient_recovery", False,
                           f"raised {type(e).__name__}: {e}"))

    # R2 耗尽：始终失败（可重试异常）→ 抛原异常，且调用 max_attempts 次（不是 1 次、不是无限）。
    count = {"n": 0}

    def always_fail():
        count["n"] += 1
        raise ConnectionError("down")

    raised = None
    try:
        adapter.wrap(always_fail, max_attempts=3)()
    except Exception as e:  # noqa: BLE001
        raised = e
    results.append(_ok("retry.exhaustion_reraises",
                       isinstance(raised, ConnectionError) and count["n"] == 3,
                       f"raised={type(raised).__name__ if raised else None} calls={count['n']}"))

    # R3 不可重试直通：非可重试异常只调用一次，不重试。
    nc = {"n": 0}

    def bad_arg():
        nc["n"] += 1
        raise ValueError("not retryable")

    raised2 = None
    try:
        adapter.wrap(bad_arg, max_attempts=3)()
    except Exception as e:  # noqa: BLE001
        raised2 = e
    results.append(_ok("retry.non_retryable_passthrough",
                       isinstance(raised2, ValueError) and nc["n"] == 1,
                       f"raised={type(raised2).__name__ if raised2 else None} calls={nc['n']}"))

    return results


def probe_degradation(adapter: DegradationModule) -> list[ProbeResult]:
    """降级角色：首选成功 / 回退成功 / 全失败降级 / on_failure 兜底。"""
    results: list[ProbeResult] = []

    # D1 首选成功：返回首选结果，且回退**不被调用**。
    fb_called = {"n": 0}

    def pref_ok(**kw):
        return "P"

    def fb_should_not_run(**kw):
        fb_called["n"] += 1
        return "F"

    out = adapter.run("cap1", preferred_fn=pref_ok, fallback_fns=[fb_should_not_run])
    results.append(_ok("degradation.preferred_wins",
                       out == "P" and fb_called["n"] == 0,
                       f"out={out!r} fallback_calls={fb_called['n']}"))

    # D2 回退成功：首选抛错 → 返回第一个回退结果。
    def pref_down(**kw):
        raise RuntimeError("preferred down")

    def fb1(**kw):
        return "F1"

    out = adapter.run("cap2", preferred_fn=pref_down, fallback_fns=[fb1])
    results.append(_ok("degradation.fallback_recovers", out == "F1", f"out={out!r}"))

    # D3 全失败：返回「降级结果」——falsy 且带 capability（不是抛异常、不是 None）。
    def all_down(**kw):
        raise RuntimeError("all down")

    out = adapter.run("cap3", preferred_fn=all_down, fallback_fns=[all_down])
    # 注意：detail 里**不嵌**对象 repr —— 默认 repr 含内存地址，会破坏报告确定性。
    results.append(_ok("degradation.exhausted_is_degraded_result",
                       (not out) and getattr(out, "capability", None) == "cap3",
                       f"falsy={not out} capability={getattr(out, 'capability', None)!r}"))

    # D4 on_failure 兜底：全失败但有 on_failure → 返回其值。
    def on_fail(**kw):
        return "OF"

    out = adapter.run("cap4", preferred_fn=all_down, on_failure=on_fail)
    results.append(_ok("degradation.on_failure_used", out == "OF", f"out={out!r}"))

    return results


def probe_budget(adapter: BudgetModule) -> list[ProbeResult]:
    """预算角色：ok / warn / ask / stop 四档 + 越过 stop 时不可继续。"""
    results: list[ProbeResult] = []

    b = adapter.new_budget(1000)
    lvl = adapter.add(b, 100)
    results.append(_ok("budget.ok_below_warn",
                       lvl == "ok" and adapter.can_continue(b) is True,
                       f"level={lvl} can_continue={adapter.can_continue(b)}"))

    b = adapter.new_budget(1000)
    lvl = adapter.add(b, 700)  # == warn_at(0.7) * soft
    results.append(_ok("budget.warn_on_threshold", lvl == "warn", f"level={lvl}"))

    b = adapter.new_budget(1000)
    lvl = adapter.add(b, 1000)  # >= soft_limit → ASK
    results.append(_ok("budget.ask_at_soft_limit", lvl == "ask", f"level={lvl}"))

    b = adapter.new_budget(1000)
    lvl = adapter.add(b, 2000)  # >= stop_at(2.0) * soft → STOP
    results.append(_ok("budget.stop_is_terminal",
                       lvl == "stop" and adapter.can_continue(b) is False,
                       f"level={lvl} can_continue={adapter.can_continue(b)}"))

    return results


def probe_circuit_breaker(adapter: CircuitBreakerModule) -> list[ProbeResult]:
    """熔断角色：健康不触发 / 无进展触发 / token 超限触发。"""
    results: list[ProbeResult] = []

    cb = adapter.new_breaker(max_tokens=1000, max_no_progress=3)
    results.append(_ok("circuit_breaker.healthy_no_trip",
                       adapter.tripped(cb) is False, "fresh breaker"))

    cb = adapter.new_breaker(max_tokens=1000, max_no_progress=3)
    for _ in range(3):
        adapter.record_output(cb, "same")
    results.append(_ok("circuit_breaker.no_progress_trips",
                       adapter.tripped(cb) is True, "3 identical outputs"))

    cb = adapter.new_breaker(max_tokens=1000, max_no_progress=5)
    adapter.add_tokens(cb, 1500)
    results.append(_ok("circuit_breaker.token_overrun_trips",
                       adapter.tripped(cb) is True, "tokens 1500/1000"))

    return results


# 角色名 → 探针函数。新增角色只需在此登记（接口 + 探针成套出现）。
PROBE_REGISTRY: dict[str, Callable[[Any], list[ProbeResult]]] = {
    "retry": probe_retry,
    "degradation": probe_degradation,
    "budget": probe_budget,
    "circuit_breaker": probe_circuit_breaker,
}


# ═══════════════════════════════════════════════════════════════════════════
# 五、评测入口
# ═══════════════════════════════════════════════════════════════════════════


def evaluate_module(module: str, adapter: Any) -> ModuleReport:
    """对单个角色的一个实现跑该角色的全部行为探针。"""
    if module not in PROBE_REGISTRY:
        raise KeyError(f"未登记的角色: {module!r}（可用: {sorted(PROBE_REGISTRY)}）")
    return ModuleReport(module, PROBE_REGISTRY[module](adapter))


def evaluate_all(bundle: Any) -> EvalReport:
    """对一个 ``bundle``（按角色名取属性的对象）跑全部角色的探针。

    典型用法：``evaluate_all(LingShuModules())`` —— 评测灵枢真实四模块。
    """
    modules: list[ModuleReport] = []
    for name in PROBE_REGISTRY:
        adapter = getattr(bundle, name)
        modules.append(evaluate_module(name, adapter))
    return EvalReport(modules)


def distinguishes(good: EvalReport, bad: EvalReport) -> bool:
    """LWP18 验收集：好实现全过、坏实现**未全过** → 评测能区分好坏。

    只要求「坏实现被评出至少一处失败」，不要求坏实现恰好失败在哪条 ——
    探针集合本身保证是**行为**判据而非身份判据。
    """
    return bool(good.passed and not bad.passed)
