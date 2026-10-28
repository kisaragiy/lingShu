"""
熔断器 — token 预算、时间预算、无进展检测（三态状态机）

在 executor_node 和 loop_run 中集成，防止无限循环烧 token。

状态机:
    CLOSED ──(硬错误: token 超支/超时)──> OPEN(hard=True, 永久停，不冷却)
    CLOSED ──(软熔断: 窗口内输出指纹无变化)──> OPEN(hard=False, 冷却 60s)
    OPEN(soft) ──(冷却结束)──> HALF_OPEN（放行一次探测）
    HALF_OPEN ──(探测通过: 所有 worker 指纹相对快照都变了)──> CLOSED
    HALF_OPEN ──(探测失败: 仍有 worker 指纹未变)──> OPEN(soft, 冷却重置)

硬/软二分：token 与墙钟超支是"已经花掉的钱"，等 60s 不会回来 → 永久停；
无进展是"可能卡住了"，给一次冷却后的探测机会。

用法:
    cb = CircuitBreaker()
    cb.add_tokens(1000)
    cb.record_output("searcher", "找到了 3 条结果")
    status = cb.check()
"""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Callable, Optional

# ─── 默认阈值 ───
MAX_TOKENS_PER_TASK = 100000        # ~2.5 元（deepseek-v4 单价）
MAX_WALL_TIME_SECONDS = 600         # 最长 10 分钟
MAX_NO_PROGRESS_STEPS = 5           # 滚动窗口大小（窗口内输出指纹无变化则软熔断）
COOLDOWN_SOFT_S = 60                # 软熔断冷却 60s；硬熔断不进冷却
NO_PROGRESS_WINDOW = MAX_NO_PROGRESS_STEPS   # 兼容旧常量名
NO_PROGRESS_UNIQUE_MAX = 2          # 窗口内唯一指纹数 <= 此值 → 视为无实质进展


class CircuitBreaker:
    """熔断器：追踪 token/时间/进展，按三态状态机判断是否终止。

    实例属性（非模块全局）：
        _state: CLOSED / OPEN / HALF_OPEN
        _open_since: 进入 OPEN 的时间戳
        _open_hard: True=硬熔断（token/超时，永久停）；False=软熔断（无进展，可冷却恢复）
        _worker_fingerprints: {wname: md5(output[:200])}
        _recent_fps: 最近 window 条指纹的滚动窗口
        _pre_open_snapshot: 软熔断瞬间的 worker 指纹快照，供半开期对比"探测是否真有变化"
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self,
                 max_tokens: int = MAX_TOKENS_PER_TASK,
                 max_time_s: int = MAX_WALL_TIME_SECONDS,
                 max_no_progress: int = MAX_NO_PROGRESS_STEPS,
                 time_fn: Optional[Callable[[], float]] = None):
        self.max_tokens = max_tokens
        self.max_time_s = max_time_s
        self.max_no_progress = max_no_progress
        # 滚动窗口长度跟随 max_no_progress —— 否则该构造参数是死参数（传了不生效）
        self._window = max(1, int(max_no_progress))
        # 时钟可注入：默认调用时查 time.time（兼容 monkeypatch）
        self._time_fn: Callable[[], float] = time_fn or (lambda: time.time())

        self.tokens_used = 0
        self.start_time: float = self._now()

        # 三态状态机
        self._state: str = self.CLOSED
        self._open_since: float = 0.0
        self._open_hard: bool = False

        # 无进展检测：按 worker 分组的当前指纹 + 滚动窗口
        self._worker_fingerprints: dict[str, str] = {}
        self._recent_fps: list[str] = []
        # 软熔断瞬间的 worker 指纹快照，用于半开期对比"探测是否真的有变化"
        self._pre_open_snapshot: dict[str, str] = {}

        # 并发安全
        self._lock = threading.Lock()

        # 兼容旧属性（旧代码可能读 cb._tripped）
        self._tripped = False

    # ── 持久化（checkpoint 序列化）──
    def __getstate__(self) -> dict:
        """给 checkpointer 用：锁和 lambda 时钟不可序列化，只存可重建的状态。

        没有这两个方法时，SQLite checkpointer 的 put 会直接失败（TypeError:
        Type is not msgpack serializable / Can't pickle local object '<lambda>'），
        表现为 checkpoint 一条都写不进 —— 崩了照样从零重跑。
        自定义 time_fn 不跨进程传递（函数对象跨进程无意义），恢复时回落默认时钟。
        """
        st = self.__dict__.copy()
        st.pop("_lock", None)
        st.pop("_time_fn", None)
        return st

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)
        self._lock = threading.Lock()
        self._time_fn = lambda: time.time()  # noqa: E731

    # ── 时钟 ──
    def _now(self) -> float:
        return float(self._time_fn())

    # ── 记录接口 ──
    def add_tokens(self, n: int):
        """记录本轮消耗的 token 数"""
        with self._lock:
            self.tokens_used += n

    def record_output(self, wname: str, output: str):
        """按 worker 记录输出指纹（md5 of output[:200]）。

        wname 作为分组 key，不拼进被比较的文本——否则不同 worker 的输出
        因前缀差异永远不相等，无进展检测会形同虚设。
        """
        text = str(output)[:200] if output else ""
        fp = hashlib.md5(text.encode("utf-8", "ignore"), usedforsecurity=False).hexdigest()
        with self._lock:
            self._worker_fingerprints[wname] = fp
            self._recent_fps.append(fp)
            while len(self._recent_fps) > self._window:
                self._recent_fps.pop(0)

    # ── 无进展检测 ──
    def _detect_no_progress(self) -> bool:
        """窗口填满后，若唯一指纹数 <= 阈值 → 视为无实质进展。"""
        if len(self._recent_fps) < self._window:
            return False
        uniq = len(set(self._recent_fps))
        return uniq <= NO_PROGRESS_UNIQUE_MAX

    def _check_hard(self, now: float) -> list[str]:
        """硬熔断条件：token 超支 / 墙钟超时。这两类钱不会因为等 60s 回来。"""
        reasons: list[str] = []
        if self.tokens_used > self.max_tokens:
            reasons.append(f"token 超预算: {self.tokens_used}/{self.max_tokens}")
        elapsed = now - self.start_time
        if elapsed > self.max_time_s:
            reasons.append(f"超时: {elapsed:.0f}s/{self.max_time_s}s")
        return reasons

    def _enter_open(self, now: float, hard: bool):
        self._state = self.OPEN
        self._open_since = now
        self._open_hard = hard
        self._tripped = True
        if not hard:
            # 软熔断：快照当前 worker 指纹，供半开期对比"探测是否真的有变化"
            self._pre_open_snapshot = dict(self._worker_fingerprints)
        else:
            self._pre_open_snapshot = {}

    # ── 状态机主入口 ──
    def check(self) -> dict:
        """检查是否触发熔断。

        Returns:
            {"tripped": bool, "reasons": [str, ...]}
        """
        with self._lock:
            now = self._now()

            # ── OPEN 状态 ──
            if self._state == self.OPEN:
                if self._open_hard:
                    self._tripped = True
                    return {"tripped": True,
                            "reasons": ["硬熔断（token/超时），不自动恢复"]}
                # 软熔断：冷却中？
                elapsed_open = now - self._open_since
                if elapsed_open < COOLDOWN_SOFT_S:
                    remain = int(COOLDOWN_SOFT_S - elapsed_open)
                    return {"tripped": True,
                            "reasons": [f"冷却中（{remain}s）"]}
                # 冷却结束 → 半开放行一次探测
                self._state = self.HALF_OPEN
                self._recent_fps = []   # 清窗口，让探测从干净状态开始
                return {"tripped": False, "reasons": ["半开探测放行"]}

            # ── HALF_OPEN 状态：探测是否真的有新输出 ──
            if self._state == self.HALF_OPEN:
                # 半开期硬错误仍直接硬熔断（钱不会因为探测一次就回来）
                hard = self._check_hard(now)
                if hard:
                    self._enter_open(now, hard=True)
                    return {"tripped": True, "reasons": hard}
                # 对比快照：每个 worker 的指纹是否真的变了
                stuck: list[str] = []
                for w, old_fp in self._pre_open_snapshot.items():
                    new_fp = self._worker_fingerprints.get(w)
                    if new_fp is None or new_fp == old_fp:
                        stuck.append(w)
                if stuck:
                    # 探测失败 → 重新软熔断，冷却重置
                    reason = f"半开探测仍无进展（worker: {','.join(stuck)}）"
                    self._enter_open(now, hard=False)
                    return {"tripped": True, "reasons": [reason]}
                # 探测通过 → 恢复 closed
                self._state = self.CLOSED
                self._pre_open_snapshot = {}
                self._tripped = False
                return {"tripped": False, "reasons": []}

            # ── CLOSED 状态：正常检查 ──
            hard = self._check_hard(now)
            if hard:
                self._enter_open(now, hard=True)
                return {"tripped": True, "reasons": hard}

            if self._detect_no_progress():
                reasons = [
                    f"窗口{self._window}轮输出无实质变化"
                    f"（唯一指纹<= {NO_PROGRESS_UNIQUE_MAX}）"
                ]
                self._enter_open(now, hard=False)
                return {"tripped": True, "reasons": reasons}

            self._tripped = False
            return {"tripped": False, "reasons": []}

    # ── 重置 ──
    def reset(self):
        """重置熔断器（新任务时调用）。"""
        with self._lock:
            self.tokens_used = 0
            self.start_time = self._now()
            self._state = self.CLOSED
            self._open_since = 0.0
            self._open_hard = False
            self._tripped = False
            self._worker_fingerprints = {}
            self._recent_fps = []
            self._pre_open_snapshot = {}

    def to_dict(self) -> dict:
        return {
            "tokens_used": self.tokens_used,
            "elapsed_s": round(self._now() - self.start_time, 1),
            "state": self._state,
            "tripped": self._tripped,
            "open_hard": self._open_hard,
        }


# ─── 作用域正确的构造入口 ───

def _ledger_snapshot() -> int:
    """当前全局 token 台账累计（延迟 import，避免 pipeline 包内循环依赖）。"""
    from .llm import ledger_total

    return ledger_total()


def new_scoped_breaker(**kwargs) -> CircuitBreaker:
    """构造 run 级熔断器，并把 token 水位线锚在当前全局台账累计上。

    背景：``pipeline.llm._LLM_TOKEN_LEDGER`` 是**进程级**累计（所有 run 共享），
    而熔断器是**每 run 新建**。若水位线从 0 起算，首次喂数会把历史所有 run 的
    token 算进本次 —— 进程累计一旦超过 ``max_tokens``，之后每个新任务开局即硬
    熔断（进 OPEN 且 ``_open_hard=True``，永久停、不冷却），外层表现为空报告。

    锚定起飞点后，喂数 delta 恒等于本 run 的真实消耗。

    Args:
        **kwargs: 透传给 ``CircuitBreaker``（max_tokens / max_time_s /
            max_no_progress / time_fn）。

    Returns:
        已设置 ``_ledger_total`` 水位线的 ``CircuitBreaker`` 实例。
    """
    cb = CircuitBreaker(**kwargs)
    cb._ledger_total = _ledger_snapshot()  # type: ignore[attr-defined]
    return cb
