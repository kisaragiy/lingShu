"""评分的时间/成本维度：按「相对基线」算，而不是绝对秒数/token 数。

为什么改（P1-11）：原口径是 latency ≤5s 满分、>30s 得 0；tokens ≤500 满分、
>5000 得 0 —— 这是按**单 Agent 一次调用**定的。但 multi-agent 路径是 3 worker ×
多轮 replan、熔断上限 600s，真实 run 在这两维几乎恒为 0，总分被压在 70；
而 `passed = total >= 60`，任一内容维度失手就 fail。结果是**评分器惩罚任务规模
而不是质量**，eval 报告系统性偏低，没法当"质量证据"对外。

改法：`ratio = 实测 / 基线`，分档 1.2 以内满分、2.0 以内半分、3.0 以内 20%、
超出 0。基线来源优先级：
  1. `task["budget"] = {"latency_s": .., "tokens": ..}` —— 逐题声明，最准
  2. 调用方传入的 baselines 映射（历史首次通过值，可落 JSON）
  3. `DEFAULT_BASELINE` —— 多 Agent 场景默认值

为什么默认值这么宽：默认值宁可宽也不要窄。窄了就回到原问题（正常规模的 run
被判 0 分，而且没人会去调）；宽了最多少扣几分，而分档在 2×/3× 处仍能抓住
真正的退化（例如 400s/40000tok）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class BudgetBaseline:
    """一个任务的参考耗时与 token 量。"""

    latency_s: float
    tokens: int
    source: str = "explicit"

    def to_dict(self) -> dict:
        return {"latency_s": self.latency_s, "tokens": self.tokens, "source": self.source}


# 多 Agent 现实规模：3 worker × 多轮 replan，熔断墙钟 600s、token 预算 100k。
# 取 600s 的 30% 与 100k 的 15% 作为"正常 run"参考，超出 2× 才算退化。
DEFAULT_BASELINE = BudgetBaseline(latency_s=180.0, tokens=15000, source="default_multi_agent")

# 分档边界（实测/基线）
FULL_RATIO = 1.2
HALF_RATIO = 2.0
LOW_RATIO = 3.0
LOW_FRACTION = 0.2


def ratio_score(value: float, reference: float, max_points: float) -> float:
    """按比值给分。reference <= 0 时退回「给足分」而不是除零崩掉。"""
    if reference <= 0:
        return max_points
    ratio = float(value) / float(reference)
    if ratio <= FULL_RATIO:
        return max_points
    if ratio <= HALF_RATIO:
        return max_points * 0.5
    if ratio <= LOW_RATIO:
        return max_points * LOW_FRACTION
    return 0.0


def resolve_baseline(
    task: Mapping | None = None,
    baselines: Mapping[str, Mapping] | None = None,
) -> BudgetBaseline:
    """按 task 声明 → 传入映射 → 默认值 的顺序取基线。"""
    task = task or {}
    task_id = task.get("id", "")

    declared = task.get("budget")
    if isinstance(declared, Mapping) and declared.get("latency_s") and declared.get("tokens"):
        return BudgetBaseline(
            latency_s=float(declared["latency_s"]),
            tokens=int(declared["tokens"]),
            source="task.budget",
        )

    if baselines and task_id in baselines:
        record = baselines[task_id]
        if record.get("latency_s") and record.get("tokens"):
            return BudgetBaseline(
                latency_s=float(record["latency_s"]),
                tokens=int(record["tokens"]),
                source=f"baselines[{task_id}]",
            )

    return DEFAULT_BASELINE
