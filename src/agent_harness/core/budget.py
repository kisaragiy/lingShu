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
        if level == BudgetLevel.STOP:
            return False
        if level == BudgetLevel.ASK and not self.asked:
            return False  # 等用户确认
        return True

    def confirm(self) -> None:
        """用户确认继续。"""
        self.asked = True
        self.soft_limit = int(self.soft_limit * 1.5)  # 下次更宽裕

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
        }
