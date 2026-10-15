"""工具返回值**语义校验**（anomaly gate）—— P0-5。

痛点（ToolMaze 实证）：上游工具 **HTTP 200 但语义已坏**（负库存、陈旧缓存、
单位错乱）→ 下游把 `{success: True}` 当"可信"全盘接收 → 雪崩。
`validate_result` 原有的判据只覆盖「HTTP 码 / 非空 / 降级关键词」这一层，
管不了"这个值在业务/物理上可能吗"。

本模块在该层之上加一个**可注册、可缺省**的语义校验层：

    register_semantic_validator("inventory", non_negative())
    register_semantic_validator("prices", numeric_range(lo=0, hi=1e7))
    register_semantic_validator("timeseries", monotonic("nondecreasing"))

校验器签名为 ``fn(result: dict) -> None | str | dict``：

* ``None``      —— 通过（含"不适用"，见下）；
* ``str``       —— 异常，原因文本（severity 取 ``error``）；
* ``dict``      —— 异常，``{"reason": ..., "severity": "error"|"warning"}``。

**保守原则**：默认**零规则**——不注册任何校验器时，``run_semantic_validators``
恒返回 ``None``，`validate_result` 行为与从前逐字节一致。内置工厂在数据
"不适用"（类型对不上、空值、非纯数值序列）时一律返回 ``None`` 跳过，
不制造误杀。

校验器自身抛异常 → **fail-closed**：判为异常（error）而非静默放过
（与仓库"护栏失效不可静默降级"一致）。
"""
from __future__ import annotations

from typing import Any, Callable

#: 语义异常原因前缀——下游日志/指标可据此与其他校验失败区分。
ANOMALY_PREFIX = "[语义异常]"

#: tool_name -> [validator, ...]；特殊键 ``"*"`` = 对所有工具生效。
_VALIDATORS: dict[str, list[Callable[[dict], Any]]] = {}


# ─── 注册 / 查询 ───

def register_semantic_validator(
    tool_name: str,
    fn: Callable[[dict], Any],
    *,
    replace: bool = False,
) -> None:
    """为一个工具（或 ``"*"`` 全局）挂一条语义校验器。

    Args:
        tool_name: 工具名；``"*"`` 表示对所有工具生效。
        fn: ``fn(result) -> None | str | dict``（见模块 docstring）。
        replace: True 时替换该工具的既有规则集（默认追加）。
    """
    if not callable(fn):
        raise TypeError("语义校验器必须可调用")
    if replace:
        _VALIDATORS[tool_name] = [fn]
    else:
        _VALIDATORS.setdefault(tool_name, []).append(fn)


def unregister_semantic_validator(
    tool_name: str,
    fn: Callable[[dict], Any] | None = None,
) -> None:
    """移除某工具的校验器；``fn=None`` 时清空该工具的全部规则。"""
    if fn is None:
        _VALIDATORS.pop(tool_name, None)
        return
    bucket = _VALIDATORS.get(tool_name)
    if bucket and fn in bucket:
        bucket.remove(fn)
        if not bucket:
            _VALIDATORS.pop(tool_name, None)


def clear_semantic_validators() -> None:
    """清空所有规则（测试隔离用）。"""
    _VALIDATORS.clear()


def _validators_for(tool_name: str) -> list[Callable[[dict], Any]]:
    # 全局规则（"*"）在前，工具专属在后；两者都会跑。
    return list(_VALIDATORS.get("*", [])) + list(_VALIDATORS.get(tool_name, []))


def run_semantic_validators(tool_name: str, result: dict) -> dict | None:
    """跑一遍该工具的全部语义校验器。

    Returns:
        ``None`` —— 全部通过 / 无规则（**默认行为，与毫不改动等价**）；
        dict     —— ``{"passed": False, "reason", "severity", "anomaly": True}``。
    """
    validators = _validators_for(tool_name)
    if not validators:
        return None
    for fn in validators:
        try:
            verdict = fn(result)
        except Exception as e:  # noqa: BLE001 — 校验器 bug 按 fail-closed 处理
            return _anomaly(f"校验器执行异常（fail-closed）: {e}", "error")
        if verdict is None or verdict is False:
            continue
        if isinstance(verdict, str):
            return _anomaly(verdict, "error")
        if isinstance(verdict, dict):
            return _anomaly(verdict.get("reason", ""), verdict.get("severity", "error"))
        # 其他真值：视为"异常"但无细节
        return _anomaly(str(verdict), "error")
    return None


def _anomaly(reason: str, severity: str) -> dict:
    if severity not in ("error", "warning"):
        severity = "error"
    return {
        "passed": False,
        "reason": f"{ANOMALY_PREFIX} {reason}",
        "severity": severity,
        "anomaly": True,
    }


# ─── 内置校验器工厂 ───
#
# 全部遵循"不适用即跳过（返回 None）"——把误杀挡在源头：库存校验器不该去
# 评判一个字符串结果。只在数据类型匹配、语义确实可判时才下结论。

def _iter_numbers(data: Any) -> list[float] | None:
    """从结果数据里抽出数值列表；无法当作纯数值序列时返回 None（不适用）。

    * 单个数 → ``[x]``；
    * 数值列表/元组 → 全部元素；
    * 含非数值元素、bool、或非数值标量 → None（跳过）。
    """
    if isinstance(data, bool):
        return None
    if isinstance(data, (int, float)):
        return [data]
    if isinstance(data, (list, tuple)) and data:
        out: list[float] = []
        for x in data:
            if isinstance(x, bool) or not isinstance(x, (int, float)):
                return None
            out.append(x)
        return out
    return None


def non_negative(severity: str = "error") -> Callable[[dict], dict | None]:
    """数值/数值序列不得为负（库存、数量、金额的物理下界）。"""
    def _v(result: dict) -> dict | None:
        nums = _iter_numbers(result.get("data"))
        if nums is None:
            return None
        bad = [n for n in nums if n < 0]
        if bad:
            return {"reason": f"出现负值（业务/物理上不可能）: {bad[:5]}", "severity": severity}
        return None
    return _v


def numeric_range(lo: float | None = None, hi: float | None = None,
                  severity: str = "error") -> Callable[[dict], dict | None]:
    """数值/数值序列必须落在 [lo, hi]（端点可为 None = 单边界）。"""
    def _v(result: dict) -> dict | None:
        nums = _iter_numbers(result.get("data"))
        if nums is None:
            return None
        bad = [n for n in nums
               if (lo is not None and n < lo) or (hi is not None and n > hi)]
        if bad:
            return {"reason": f"超出允许范围 [{lo}, {hi}]: {bad[:5]}", "severity": severity}
        return None
    return _v


def matches_type(expected: type | tuple, severity: str = "error") -> Callable[[dict], dict | None]:
    """结果数据类型必须匹配（单位/结构契约：如必须是 list、必须是 dict）。"""
    def _v(result: dict) -> dict | None:
        data = result.get("data")
        if data is None:
            return None  # 空值另有判据（validate_result 主体），此处不越权
        if not isinstance(data, expected):
            names = getattr(expected, "__name__", str(expected))
            return {"reason": f"类型不符: 期望 {names} 实得 {type(data).__name__}",
                    "severity": severity}
        return None
    return _v


def one_of(allowed, severity: str = "error") -> Callable[[dict], dict | None]:
    """结果必须是白名单里的取值之一（状态码/枚举契约，防陈旧缓存串值）。"""
    allowed_set = set(allowed) if not isinstance(allowed, (set, frozenset)) else allowed

    def _v(result: dict) -> dict | None:
        data = result.get("data")
        if data is None:
            return None
        try:
            ok = data in allowed_set
        except TypeError:
            return None  # 不可哈希（如 list）→ 不适用
        if not ok:
            return {"reason": f"取值不在白名单 {sorted(allowed_set, key=str)[:5]}: {data!r}",
                    "severity": severity}
        return None
    return _v


def monotonic(order: str = "nondecreasing",
              severity: str = "error") -> Callable[[dict], dict | None]:
    """数值序列必须单调（时间序列/累计量的方向性断言）。

    Args:
        order: ``"nondecreasing"`` | ``"nonincreasing"`` | ``"increasing"``
               | ``"decreasing"``。
    """
    if order not in ("nondecreasing", "nonincreasing", "increasing", "decreasing"):
        raise ValueError(f"未知单调方向: {order}")

    def _v(result: dict) -> dict | None:
        nums = _iter_numbers(result.get("data"))
        if nums is None or len(nums) < 2:
            return None
        for a, b in zip(nums, nums[1:]):
            if order == "nondecreasing" and b < a:
                return {"reason": f"序列非单调递增（位置出现回落）: {a}→{b}",
                        "severity": severity}
            if order == "nonincreasing" and b > a:
                return {"reason": f"序列非单调递减（位置出现回升）: {a}→{b}",
                        "severity": severity}
            if order == "increasing" and b <= a:
                return {"reason": f"序列非严格递增: {a}→{b}", "severity": severity}
            if order == "decreasing" and b >= a:
                return {"reason": f"序列非严格递减: {a}→{b}", "severity": severity}
        return None
    return _v
