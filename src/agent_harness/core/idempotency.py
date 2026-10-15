"""P0-3（L 部分）：**幂等 key** 与 **决策/执行分离**。

痛点：LLM 调用**实际成功但响应超时** → 上层重试 → 重复副作用
（给客户发两封信、扣两次款）。根因是"决定做什么"（LLM 推理）与
"去做"（写外部系统）被耦合在同一次可重试调用里。

对策两件套：

1. **决策/执行分离**（:class:`DecisionExecutor`）——
   ``decide()`` 只跑一次并把决策**持久化**；``execute()`` 重试时**重放**
   已持久化的决策，绝不重问 LLM。于是"重试"只重放副作用，不重跑推理。

2. **幂等 key**（:class:`IdempotencyStore` / :func:`run_once` /
   :func:`call_idempotent`）——
   外部写操作前先查"该 key 是否已执行过"，已执行则**跳过**并返回上次结果。

存储默认内存态；给 ``path`` 即落 **JSON 文件**（原子写：临时文件 + os.replace），
可跨进程/跨重试存活。全部线程安全。

范围说明：覆盖"单 agent 顺序重试"这一主场景（timeout→retry）。同 key 的
**并发**抢占（两线程同时 check-miss 后同时执行）不在本层内解决——那需要
分布式锁；此处保守不做，避免引入会改变现有回路的复杂度。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

#: 默认可重试异常（与 core/retry.py 保持一致语义）。
DEFAULT_RETRYABLE: tuple[type[Exception], ...] = (ConnectionError, TimeoutError, OSError)


# ─── 幂等存储 ───

class IdempotencyStore:
    """已执行 key 的台账。内存态或 JSON 文件（原子持久化）。

    记录格式：``key -> {"ts": <unix秒>, "value": <payload>}``。
    """

    def __init__(self, path: str | os.PathLike | None = None):
        self._path = Path(path) if path is not None else None
        self._lock = threading.RLock()
        self._mem: dict[str, Any] = {}
        if self._path is not None:
            self._load()

    # 内部：从磁盘读回（失败视为空——文件还没建/损坏都不该炸）
    def _load(self) -> None:
        if self._path is None:
            return
        try:
            data = json.loads(self._path.read_text("utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        if isinstance(data, dict):
            self._mem = data

    def _flush(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(self._mem, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self._path)  # 原子替换——不会读到半截文件

    # 公共 API
    def is_executed(self, key: str) -> bool:
        """该 key 是否已执行过（文件态下会先刷新一次，容忍他进程写入）。"""
        with self._lock:
            if key in self._mem:
                return True
            if self._path is not None:
                self._load()
            return key in self._mem

    def get(self, key: str) -> Any:
        """取回上次记录的值（未执行返回 None）。"""
        with self._lock:
            if key not in self._mem and self._path is not None:
                self._load()
            rec = self._mem.get(key)
        return rec.get("value") if isinstance(rec, dict) else None

    def get_record(self, key: str) -> dict | None:
        """取回完整记录 ``{"ts", "value"}``（未执行返回 None）。"""
        with self._lock:
            if key not in self._mem and self._path is not None:
                self._load()
            rec = self._mem.get(key)
        return dict(rec) if isinstance(rec, dict) else None

    def mark(self, key: str, value: Any = None) -> None:
        """登记 key 已执行（value 为可重放的结果载荷）。"""
        with self._lock:
            self._mem[key] = {"ts": time.time(), "value": value}
            self._flush()

    def __contains__(self, key: str) -> bool:
        return self.is_executed(key)

    def __len__(self) -> int:
        with self._lock:
            return len(self._mem)

    def reset(self) -> None:
        """清空台账（测试隔离用）。"""
        with self._lock:
            self._mem = {}
            self._flush()


# ─── 幂等执行原语 ───

def run_once(store: IdempotencyStore, key: str, fn: Callable, *args, **kwargs) -> Any:
    """幂等执行：key 已执行 → 直接返回上次结果（**不调用 fn**）。

    否则调用 ``fn(*args, **kwargs)``，成功后登记，返回结果。
    """
    if store.is_executed(key):
        return store.get(key)
    result = fn(*args, **kwargs)
    store.mark(key, result)
    return result


#: 默认幂等台账（供 :func:`call_idempotent` 缺省复用）。
_DEFAULT_STORE: IdempotencyStore | None = None


def default_store() -> IdempotencyStore:
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = IdempotencyStore()
    return _DEFAULT_STORE


def call_idempotent(name: str, idempotency_key: str, *, store: IdempotencyStore | None = None,
                    **kwargs) -> dict:
    """带幂等 key 的工具调用：外部写操作的安全入口。

    语义：
      * 先查 ``idempotency_key``；**已执行** → 直接返回上次结果，
        额外带 ``idempotent_replay: True``，**绝不重复调用工具**（防重复副作用）；
      * 未执行 → 调 :func:`call_tool`；
      * **只登记成功**（``success=True``）的调用——失败/超时未产生（或不确定产生）
        副作用的调用允许重试；成功即登记，后续同 key 一律重放。

    这是"外部写操作前先查 key 是否已执行过"的落点。
    """
    store = store if store is not None else default_store()
    if store.is_executed(idempotency_key):
        stored = store.get(idempotency_key)
        replay = dict(stored) if isinstance(stored, dict) else {"success": True, "error": None, "data": stored}
        replay["idempotent_replay"] = True
        return replay

    # 延迟 import：避免 core.idempotency 依赖 tools 包造成循环。
    from .tools.registry import call_tool
    result = call_tool(name, **kwargs)
    if isinstance(result, dict) and result.get("success"):
        store.mark(idempotency_key, result)
    return result


# ─── 决策 / 执行分离 ───

class DecisionExecutor:
    """把"LLM 决定"与"副作用执行"分开，各自持有幂等台账。

    典型用法（外部写操作，如发信 / 扣款）::

        ex = DecisionExecutor(decision_store=..., effect_store=...)
        result = ex.run(
            decide_key="email:order-42:decision",     # 决策只做一次
            effect_key="email:order-42:effect",       # 副作用只做一次
            decide_fn=llm_choose_recipient,           # 慢、可能超时、重试代价高
            execute_fn=send_email,                    # 幂等重放，可重试
            max_attempts=3,
        )

    关键性质：
      * ``decide_fn`` 在**整个 key 生命周期内只被调用一次**（决策持久化）；
      * ``execute_fn`` 重试时收到的始终是**同一份持久化决策**——绝不重问 LLM；
      * ``execute_fn`` 成功一次即登记 effect_key，之后同 key 调用直接重放结果。
    """

    def __init__(self, decision_store: IdempotencyStore | None = None,
                 effect_store: IdempotencyStore | None = None):
        # 注意：不能用 `x or IdempotencyStore()` —— IdempotencyStore 有 __len__，
        # 空台账会变成 falsy，从而把传入的文件台账悄悄换成内存台账（丢持久化）。
        self.decisions = decision_store if decision_store is not None else IdempotencyStore()
        self.effects = effect_store if effect_store is not None else IdempotencyStore()

    def decide(self, key: str, decide_fn: Callable, *args, **kwargs) -> Any:
        """跑一次决策并持久化；已存在则**重放**（不调用 decide_fn）。"""
        if self.decisions.is_executed(key):
            return self.decisions.get(key)
        decision = decide_fn(*args, **kwargs)
        self.decisions.mark(key, decision)
        return decision

    def execute(self, effect_key: str, execute_fn: Callable, decision: Any, *,
                max_attempts: int = 1,
                retryable: tuple[type[Exception], ...] = DEFAULT_RETRYABLE) -> Any:
        """执行副作用（幂等 + 可重试，重放传入的 decision）。

        已执行 → 重放上次结果；否则按 ``max_attempts`` 重试 ``execute_fn(decision)``，
        全部失败则抛出最后一次异常。
        """
        if self.effects.is_executed(effect_key):
            return self.effects.get(effect_key)
        if max_attempts < 1:
            raise ValueError("max_attempts 必须 >= 1")
        last_exc: Exception | None = None
        for _attempt in range(max_attempts):
            try:
                result = execute_fn(decision)
            except retryable as e:
                last_exc = e
                continue
            self.effects.mark(effect_key, result)
            return result
        assert last_exc is not None
        raise last_exc

    def run(self, decide_key: str, effect_key: str, decide_fn: Callable,
            execute_fn: Callable, *decide_args, max_attempts: int = 1,
            retryable: tuple[type[Exception], ...] = DEFAULT_RETRYABLE, **decide_kwargs) -> Any:
        """决策一次（持久化）+ 执行（可重试、重放决策、幂等）。"""
        decision = self.decide(decide_key, decide_fn, *decide_args, **decide_kwargs)
        return self.execute(effect_key, execute_fn, decision,
                            max_attempts=max_attempts, retryable=retryable)
