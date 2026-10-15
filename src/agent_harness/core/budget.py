"""
Token 预算管理 — 防止 agent 跑飞烧钱。

三层机制:
  1. WARN (70%):   提醒用户 token 快用完了
  2. ASK (100%):   暂停并请求确认是否继续
  3. STOP (200%):  强制停止（跑飞检测）
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class BudgetLevel(Enum):
    OK = "ok"
    WARN = "warn"
    ASK = "ask"
    STOP = "stop"


class BudgetExceeded(Exception):
    """预算超限。"""
    code = "BUDGET_EXCEEDED"
    recoverable = True


class DailyLevel(Enum):
    """日聚合预算级别。"""
    OK = "ok"
    WARN = "warn"
    HALT = "halt"


def _today_key() -> str:
    """当天日期键（本地时区 YYYY-MM-DD），用于日聚合的跨日重置。"""
    return time.strftime("%Y-%m-%d", time.localtime())



@dataclass
class TokenBudget:
    """单次会话的 token 预算追踪。

    Args:
        soft_limit: 提醒阈值（默认 200K tokens ≈ $0.06）
        hard_limit: 强制停止阈值（默认 500K tokens ≈ $0.15）
        warn_at: 提醒比例（0.7 = 70%）
        ask_at: 确认比例（1.0 = 100%）
        stop_at: 停止比例（2.0 = 200%）
    """

    soft_limit: int = 200_000
    hard_limit: int = 500_000
    warn_at: float = 0.7
    ask_at: float = 1.0
    stop_at: float = 2.0

    used: int = 0
    warned: bool = False
    asked: bool = False
    started_at: float = field(default_factory=time.time)

    # ─── P0-1: cache 命中统计（只加字段，默认值不改变原有行为）───
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0

    # ─── P0-4: session kill switch ───
    killed: bool = False
    kill_reason: str = ""

    def add(self, tokens: int) -> BudgetLevel:
        """记录 token 消耗，返回当前预算级别。"""
        self.used += tokens
        return self.check()

    def check(self) -> BudgetLevel:
        """检查当前预算状态。"""
        # 硬限制 —— 跑飞了
        if self.used >= int(self.soft_limit * self.stop_at):
            return BudgetLevel.STOP

        # 确认限制 —— 超过预算了，需要用户确认
        if self.used >= self.soft_limit:
            return BudgetLevel.ASK

        # 警告限制 —— 快用完了
        warn_threshold = int(self.soft_limit * self.warn_at)
        if self.used >= warn_threshold and not self.warned:
            self.warned = True
            return BudgetLevel.WARN

        return BudgetLevel.OK

    def can_continue(self, level: BudgetLevel) -> bool:
        """根据预算级别判断是否可继续。"""
        if self.killed:
            return False  # P0-4: session kill switch — 触发后一律停止
        if level == BudgetLevel.STOP:
            return False
        if level == BudgetLevel.ASK and not self.asked:
            return False  # 等用户确认
        return True

    def confirm(self) -> None:
        """用户确认继续。"""
        self.asked = True
        self.soft_limit = int(self.soft_limit * 1.5)  # 下次更宽裕

    # ─── P0-1: cache 指标 ───

    def record_cache(self, hit_tokens: int = 0, miss_tokens: int = 0) -> None:
        """记录一次 LLM 调用的缓存命中/未命中 token 数。"""
        if hit_tokens > 0:
            self.cache_hit_tokens += int(hit_tokens)
        if miss_tokens > 0:
            self.cache_miss_tokens += int(miss_tokens)

    @property
    def cache_hit_rate(self) -> float:
        """缓存命中率（按 token 计）；无数据时返回 0.0，避免 0/0。"""
        total = self.cache_hit_tokens + self.cache_miss_tokens
        if total <= 0:
            return 0.0
        return self.cache_hit_tokens / total

    # ─── P0-4: session kill switch ───

    def kill(self, reason: str = "") -> None:
        """触发 session 级急停开关：之后 can_continue 恒为 False。"""
        self.killed = True
        if reason:
            self.kill_reason = reason

    @property
    def estimated_cost(self) -> float:
        """估算当前消耗成本（基于 DeepSeek Flash 价格）。"""
        price_per_m = float(os.environ.get("HARNESS_TOKEN_PRICE", "0.3"))
        return (self.used / 1_000_000) * price_per_m

    def summary(self) -> dict[str, Any]:
        """当前状态摘要。"""
        return {
            "used": self.used,
            "limit": self.soft_limit,
            "hard_limit": self.hard_limit,
            "pct": round(self.used / self.soft_limit * 100, 1) if self.soft_limit else 0,
            "cost_usd": round(self.estimated_cost, 4),
            "level": self.check().value,
            "elapsed_s": int(time.time() - self.started_at),
            # P0-1: cache 指标
            "cache_hit_tokens": self.cache_hit_tokens,
            "cache_miss_tokens": self.cache_miss_tokens,
            "cache_hit_rate": round(self.cache_hit_rate, 4),
            # P0-4: kill switch
            "killed": self.killed,
        }


@dataclass
class DailyBudget:
    """日聚合预算熔断 —— 按日累计成本，超阈值返回停机信号。

    与单会话的 :class:`TokenBudget` 互补：TokenBudget 管「一次会话跑飞」，
    DailyBudget 管「一整天累计烧钱」。跨日自动归零。

    Args:
        daily_limit_usd: 当日成本上限（美元）
        warn_at: 预警比例（默认 0.7 = 70%）
        halt_at: 停机比例（默认 1.0 = 100%）
    """

    daily_limit_usd: float = 1.0
    warn_at: float = 0.7
    halt_at: float = 1.0
    used_cost: float = 0.0
    warned: bool = False
    halted: bool = False
    day: str = field(default_factory=_today_key)

    def _rollover(self) -> None:
        """跨日则重置当日累计。"""
        today = _today_key()
        if today != self.day:
            self.day = today
            self.used_cost = 0.0
            self.warned = False
            self.halted = False

    def add_cost(self, cost: float) -> DailyLevel:
        """记录一笔成本，返回当前日级别（含停机信号）。"""
        self._rollover()
        try:
            self.used_cost += float(cost or 0.0)
        except (TypeError, ValueError):
            pass
        return self.check()

    def check(self) -> DailyLevel:
        """检查当日预算状态。"""
        self._rollover()
        if self.halted:
            return DailyLevel.HALT
        if self.daily_limit_usd and self.used_cost >= self.daily_limit_usd * self.halt_at:
            self.halted = True
            return DailyLevel.HALT  # 停机信号
        if self.daily_limit_usd and self.used_cost >= self.daily_limit_usd * self.warn_at:
            self.warned = True
            return DailyLevel.WARN
        return DailyLevel.OK

    def can_continue(self) -> bool:
        """是否可继续（HALT 时为 False）。"""
        return self.check() != DailyLevel.HALT

    @property
    def remaining_usd(self) -> float:
        """当日剩余可用额度。"""
        return max(0.0, self.daily_limit_usd - self.used_cost)

    def summary(self) -> dict[str, Any]:
        """当日状态摘要。"""
        return {
            "day": self.day,
            "used_cost_usd": round(self.used_cost, 4),
            "daily_limit_usd": self.daily_limit_usd,
            "remaining_usd": round(self.remaining_usd, 4),
            "pct": round(self.used_cost / self.daily_limit_usd * 100, 1) if self.daily_limit_usd else 0,
            "level": self.check().value,
            "halted": self.halted,
        }
