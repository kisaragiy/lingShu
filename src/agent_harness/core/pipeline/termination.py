"""终止条件（TerminationCondition）—— 把「停不停」从熔断器里解耦出来。

为什么拆（P1-7）：`CircuitBreaker` 里塞了三种判据（token / 墙钟 / 无进展），
但这三者的语义与冷却策略完全不同，而 breaker 又是**每 run 新建**的实例 ——
它保护不了任何下游依赖，本质是「单次任务的终止条件」。业界一手做法（AutoGen
TerminationCondition）把这类判断做成可组合、每次 run 后重置的独立条件。

设计约束（为什么是最小版，而不是重写熔断器）：
- 不动 ``CircuitBreaker`` 内部状态机 —— 100+ 测试锚在它的三态语义上，重写只会
  把「可靠性」换成「回归」。
- 条件只做**判断**，不持全局、不改 state；需要读实例的（breaker / 预算）通过
  注入的探针函数读。这样组合器可以纯逻辑测试。
- ``AnyOf`` 短路求值，**顺序即优先级** —— 与原来的 if-链逐条比对，行为不变。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable


@dataclass(frozen=True)
class StopReason:
    """为什么停。``hard``=True 表示「已经花掉的东西回不来」（token/超时），
    不该给冷却重试的机会；False 表示「可能只是卡住」，可以冷却后探测。"""

    name: str
    message: str
    hard: bool = True

    def __str__(self) -> str:
        return f"[{self.name}] {self.message}"


@runtime_checkable
class TerminationCondition(Protocol):
    """条件协议：给 state，返回停因或 None（None = 未命中，继续）。"""

    name: str

    def __call__(self, state: Any) -> StopReason | None: ...


class TokenLimit:
    """token 上限（取值用 used_fn 注入，便于测）。"""

    def __init__(self, used_fn: Callable[[], int], max_tokens: int,
                 name: str = "token_limit"):
        self.name = name
        self._used_fn = used_fn
        self.max_tokens = int(max_tokens)

    def __call__(self, state: Any = None) -> StopReason | None:
        used = int(self._used_fn())
        if used >= self.max_tokens:
            return StopReason(self.name, f"token 超预算（{used} ≥ {self.max_tokens}）", hard=True)
        return None


class WallTime:
    """墙钟上限（elapsed_fn 返回已用秒数）。"""

    def __init__(self, elapsed_fn: Callable[[], float], max_seconds: float,
                 name: str = "wall_time"):
        self.name = name
        self._elapsed_fn = elapsed_fn
        self.max_seconds = float(max_seconds)

    def __call__(self, state: Any = None) -> StopReason | None:
        elapsed = float(self._elapsed_fn())
        if elapsed >= self.max_seconds:
            return StopReason(self.name, f"超时（{elapsed:.0f}s ≥ {self.max_seconds:g}s）", hard=True)
        return None


class StateFlag:
    """state 上的布尔/枚举标记命中即停（无进展、不可行、预算级别…）。

    ``equals=None`` 时按真值判断；给 ``equals`` 则要求等值。
    """

    def __init__(self, field: str, *, equals: Any = None, message: str = "",
                 name: str | None = None):
        self.name = name or field
        self.field = field
        self.equals = equals
        self.message = message or f"{field} 命中"

    def __call__(self, state: Any) -> StopReason | None:
        if not isinstance(state, dict):
            return None
        value = state.get(self.field)
        hit = (value == self.equals) if self.equals is not None else bool(value)
        return StopReason(self.name, self.message, hard=False) if hit else None


class CallbackCondition:
    """把任意 ``probe(state) -> StopReason | bool`` 包成条件。

    给需要副作用或实例状态的判据用（例：先喂 token 再问熔断器、先结算预算再问
    是否放行）—— 这类「探针」留在调用方，条件本身保持无状态。
    """

    def __init__(self, name: str, probe: Callable[[Any], Any], message: str,
                 *, hard: bool = True):
        self.name = name
        self._probe = probe
        self.message = message
        self.hard = hard

    def __call__(self, state: Any) -> StopReason | None:
        hit = self._probe(state)
        if isinstance(hit, StopReason):
            return hit
        return StopReason(self.name, self.message, hard=self.hard) if hit else None


class AnyOf:
    """短路组合：按顺序求值，第一个命中即返回（顺序 = 优先级）。"""

    def __init__(self, *conditions: TerminationCondition, name: str = "any_of"):
        self.name = name
        self.conditions = tuple(conditions)

    def __call__(self, state: Any) -> StopReason | None:
        for condition in self.conditions:
            reason = condition(state)
            if reason is not None:
                return reason
        return None

    def __repr__(self) -> str:  # 便于报错时看清组合了哪些条件
        return f"AnyOf({', '.join(getattr(c, 'name', '?') for c in self.conditions)})"


class AllOf:
    """全部命中才停（返回最后一个停因，便于看到「压垮骆驼的最后一根」）。

    用于「必须同时满足两条才允许放弃」的保守判据；单独命中只记不停。
    """

    def __init__(self, *conditions: TerminationCondition, name: str = "all_of"):
        self.name = name
        self.conditions = tuple(conditions)

    def __call__(self, state: Any) -> StopReason | None:
        last: StopReason | None = None
        for condition in self.conditions:
            reason = condition(state)
            if reason is None:
                return None
            last = reason
        return last

    def __repr__(self) -> str:
        return f"AllOf({', '.join(getattr(c, 'name', '?') for c in self.conditions)})"
