"""
熔断器 — token 预算、时间预算、无进展检测、重试限流

在 executor_node 和 loop_run 中集成，防止无限循环烧 token。

用法:
    cb = CircuitBreaker()
    cb.add_tokens(1000)  # 每次 LLM 调用后更新
    cb.record_output(text)  # 每次步骤输出后更新
    status = cb.check()  # 检查是否触发
"""

import time

# ─── 默认阈值 ───
MAX_TOKENS_PER_TASK = 100000       # ~2.5 元（deepseek-v4 单价）
MAX_WALL_TIME_SECONDS = 600        # 最长 10 分钟
MAX_NO_PROGRESS_STEPS = 5          # 连续 N 步输出不变则停止


class CircuitBreaker:
    """熔断器：追踪 token/时间/进展并判断是否需终止"""

    def __init__(self, max_tokens: int = MAX_TOKENS_PER_TASK,
                 max_time_s: int = MAX_WALL_TIME_SECONDS,
                 max_no_progress: int = MAX_NO_PROGRESS_STEPS):
        self.max_tokens = max_tokens
        self.max_time_s = max_time_s
        self.max_no_progress = max_no_progress
        self.tokens_used = 0
        self.start_time = time.time()
        self.last_outputs: list[str] = []
        self._tripped = False

    def add_tokens(self, n: int):
        """记录本轮消耗的 token 数"""
        self.tokens_used += n

    def record_output(self, output: str):
        """记录步骤输出，用于无进展检测"""
        summary = str(output)[:100] if output else ""
        self.last_outputs.append(summary)
        if len(self.last_outputs) > self.max_no_progress:
            self.last_outputs.pop(0)

    def check(self) -> dict:
        """检查是否触发熔断

        Returns:
            {"tripped": True/False, "reasons": ["..."]}
        """
        if self._tripped:
            return {"tripped": True, "reasons": ["熔断器已触发"]}

        elapsed = time.time() - self.start_time
        reasons = []

        if self.tokens_used > self.max_tokens:
            reasons.append(f"token 超预算: {self.tokens_used}/{self.max_tokens}")

        if elapsed > self.max_time_s:
            reasons.append(f"超时: {elapsed:.0f}s/{self.max_time_s}s")

        if len(self.last_outputs) >= self.max_no_progress:
            unique = len(set(self.last_outputs))
            if unique <= 1:
                reasons.append(f"连续 {self.max_no_progress} 步输出无变化")

        if reasons:
            self._tripped = True

        return {"tripped": len(reasons) > 0, "reasons": reasons}

    def reset(self):
        """重置熔断器（新任务时调用）"""
        self.tokens_used = 0
        self.start_time = time.time()
        self.last_outputs = []
        self._tripped = False

    def to_dict(self) -> dict:
        return {
            "tokens_used": self.tokens_used,
            "elapsed_s": round(time.time() - self.start_time, 1),
            "tripped": self._tripped,
        }
