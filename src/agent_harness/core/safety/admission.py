"""
记忆/状态写入准入（admission）— 间接注入防护的裁决层

provenance.py 负责"标记"，本模块负责"裁决"：
外源（不可信）内容在**写入持久记忆或状态之前**必须过一道 admission 裁决。
默认策略保守：**不可信内容不得提升为持久事实**，除非**显式放行**。

三种可放行的情形：
  1. 内容本身就是 ``trusted``；
  2. 调用方带 ``released`` / 传入 :func:`release` 生成的信封（显式放行）；
  3. 其 origin 落在策略白名单 ``Policy.allowed_origins`` 内。

未标记（``unknown``）内容默认**不放行**（``Policy.allow_unknown=False``），
避免"忘了标记"变成"默认可信"。

对外入口：
    admit(item, ...)          → Decision（只判断，不落盘）
    guard_write(item, sink)   → 判断通过才调用 sink；否则短路返回 blocked
    release(item, ...)        → 显式放行信封
    install_memory_admission()/uninstall_memory_admission()  → 可选：把裁决接到工具层
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from . import provenance as prov
from .provenance import TRUSTED, UNTRUSTED, TaggedContent, trust_of, unwrap

# 默认会被当作"持久记忆写入"的工具（install_memory_admission 用）
DEFAULT_MEMORY_SINKS: tuple[str, ...] = ("rag_index", "memory_import")


@dataclass(frozen=True)
class Policy:
    """准入策略。默认即保守策略。"""

    allowed_origins: frozenset[str] = frozenset()
    allow_unknown: bool = False


DEFAULT_POLICY = Policy()


@dataclass(frozen=True)
class Released:
    """显式放行信封：包裹一段被人工/上层明确放行的内容。"""

    item: Any
    reason: str = ""
    operator: str = ""


@dataclass(frozen=True)
class Decision:
    """裁决结果。"""

    admitted: bool
    trust: str
    reason: str
    released: bool = False
    origin: str = ""


def release(item: Any, *, reason: str = "", operator: str = "") -> Released:
    """显式放行一段内容（供上层在人工确认后调用）。"""
    return Released(item=item, reason=reason, operator=operator)


def _normalize(item: Any) -> tuple[Any, bool, str]:
    """拆出（真正的内容, 是否显式放行, 放行说明）。"""
    if isinstance(item, Released):
        return item.item, True, item.reason
    return item, False, ""


def admit(item: Any, *, policy: Policy = DEFAULT_POLICY, released: bool = False,
          release_reason: str = "", purpose: str = "memory") -> Decision:
    """裁决一次写入是否准入。

    Args:
        item: 待写入内容（TaggedContent / Released / 裸内容）
        policy: 准入策略
        released: 调用方直接声明显式放行
        release_reason: 放行说明（审计用）
        purpose: 写入目的（memory / state …，仅用于说明）

    Returns:
        Decision(admitted=..., trust=..., reason=..., released=..., origin=...)
    """
    inner, wrapped_release, wrap_reason = _normalize(item)
    is_released = released or wrapped_release
    trust = trust_of(inner)
    origin = ""
    p = prov.provenance_of(inner)
    if p is not None:
        origin = p.origin

    if trust == TRUSTED:
        return Decision(True, trust, "内容可信", is_released, origin)

    if is_released:
        reason = release_reason or wrap_reason or "显式放行"
        return Decision(True, trust, f"显式放行（{reason}）", True, origin)

    if trust == UNTRUSTED:
        if origin and origin in policy.allowed_origins:
            return Decision(True, trust, f"来源 {origin!r} 在放行白名单内", False, origin)
        return Decision(
            False, trust,
            f"不可信外源内容不得提升为持久{purpose}（除非显式放行）",
            False, origin,
        )

    # UNKNOWN（未标记）
    if policy.allow_unknown:
        return Decision(True, trust, "未标记但策略允许", False, origin)
    return Decision(
        False, trust,
        f"未标记内容默认不得提升为持久{purpose}（未标记≠可信）",
        False, origin,
    )


def guard_write(item: Any, sink: Callable[[Any], Any], *, policy: Policy = DEFAULT_POLICY,
                released: bool = False, release_reason: str = "",
                purpose: str = "memory") -> dict:
    """过裁决后再写入的封装。

    只裁决通过才调用 ``sink(unwrapped_content)``；被拒时**绝不调用 sink**。

    Returns:
        通过：``{"ok": True, "admitted": True, "trust": ..., "result": <sink 返回值>}``
        被拒：``{"ok": False, "blocked": True, "admitted": False, "trust": ..., "reason": ...}``
    """
    decision = admit(item, policy=policy, released=released,
                     release_reason=release_reason, purpose=purpose)
    if not decision.admitted:
        return {
            "ok": False, "blocked": True, "admitted": False,
            "trust": decision.trust, "reason": decision.reason,
            "origin": decision.origin,
        }
    inner = _normalize(item)[0]
    result = sink(unwrap(inner))
    return {
        "ok": True, "admitted": True, "blocked": False,
        "trust": decision.trust, "reason": decision.reason, "result": result,
    }


# ─── 可选：把裁决接到工具层（显式启用，默认不接，零回路改动） ───

def _marker_is_untrusted(kwargs: dict) -> bool:
    """从调用参数里读取不可信标记。

    调用方在 kwargs 里带 ``_trust="untrusted"`` 或 ``_provenance``（TaggedContent）。
    """
    if kwargs.get("_trust") == UNTRUSTED:
        return True
    p = kwargs.get("_provenance")
    return prov.trust_of(p) == UNTRUSTED if p is not None else False


def _marker_is_released(kwargs: dict) -> bool:
    return bool(kwargs.get("_released") or kwargs.get("_release"))


def make_memory_admission_hook(sinks: tuple[str, ...] = DEFAULT_MEMORY_SINKS,
                               policy: Policy = DEFAULT_POLICY):
    """构造一个 PRE 钩子 ``fn(name, kwargs) -> block_dict | None``。

    仅对 ``sinks`` 里的工具生效：命中不可信且未放行 → 返回 block_dict 短路调用；
    其余一律返回 ``None``（放行，回路不变）。未带标记的调用不受影响。

    放行时会**剥掉**控制参数（``_trust`` / ``_released`` / ``_release`` /
    ``_provenance``），避免它们漏进真正的工具函数。
    """
    control_keys = ("_trust", "_released", "_release", "_provenance")

    def _strip(kwargs: dict) -> None:
        for k in control_keys:
            kwargs.pop(k, None)

    def _hook(name: str, kwargs: dict):
        if name not in sinks:
            return None
        if not _marker_is_untrusted(kwargs):
            _strip(kwargs)   # 即使是可信/未标记，也别让控制参数漏进工具
            return None
        decision = admit(
            TaggedContent(content=kwargs, provenance=prov.Provenance(trust=UNTRUSTED)),
            policy=policy,
            released=_marker_is_released(kwargs),
            purpose="memory",
        )
        if decision.admitted:
            _strip(kwargs)
            return None
        return {
            "success": False,
            "error": f"[记忆准入拒绝] {name}: {decision.reason}",
            "data": {"blocked": True, "trust": decision.trust, "reason": decision.reason},
        }

    return _hook


def install_memory_admission(sinks: tuple[str, ...] = DEFAULT_MEMORY_SINKS,
                             policy: Policy = DEFAULT_POLICY):
    """把记忆准入钩子注册进工具层 PRE 阶段。返回钩子（供卸载）。"""
    from ..tools.registry import register_pre_hook

    hook = make_memory_admission_hook(sinks=sinks, policy=policy)
    register_pre_hook(hook)
    return hook


def uninstall_memory_admission(hook) -> bool:
    """移除此前安装的记忆准入钩子。"""
    from ..tools.registry import PRE_HOOKS

    if hook in PRE_HOOKS:
        PRE_HOOKS.remove(hook)
        return True
    return False
