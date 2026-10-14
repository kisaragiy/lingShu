"""LWP19 — 技能生命周期（trial / active / stable / retired 状态机）。

对标 **SkillForge**（arXiv:2610.09832, *Co-Evolving Skills and Agents via Dynamic
Skill Lifecycles*, NeurIPS 2026）。源论文的核心结构主张：把 skill 库当成**动态种群**
而非只增不减的档案 —— 每条 skill 携带一个 **fitness**（成功率），并沿四态生命周期
``trial → active → stable → retired`` 迁移；retired 的 skill **不再进入检索**，从而
避免"延迟过时（delayed obsolescence）"的低质 skill 污染 agent 上下文。

源论文的机制（Eq. 6/8/9）：

  * **fitness**（Eq. 6）：``f_i = C_i / U_i`` 当 ``U_i ≥ N_warm``，否则取中性先验
    ``f_default``（新 skill 使用次数不足时不轻信经验成功率）。
  * **迟滞阈值**（§4.3）：``δ_retire < δ_demote < δ_stable`` —— ``δ_demote`` 严格
    夹在中间，使一个 stable skill 的 fitness 跌破时**必须先降级回 active**，才可能被
    retirement 命中（迟滞，避免 fitness 抖动导致 stable↔retired 反复横跳）。
  * **代际感知的退役门槛**（Eq. 8）：``N_retire^(g) = N_protect`` 当 ``g = 0``（蒸馏
    来的原生 skill 需要更多使用量才允许退役），否则 ``N_retire``。
  * **迁移规则**（Eq. 9）：
      - ``trial → active``   当 ``U_i ≥ N_promote``
      - ``active → stable``  当 ``f_i ≥ δ_stable ∧ U_i ≥ N_stable``
      - ``stable → active``  当 ``f_i < δ_demote``（迟滞降级）
      - ``active → retired`` 当 ``f_i < δ_retire ∧ U_i ≥ N_retire^(g_i)``

本模块只取那**最小、可离线、确定性**的一半，落成可机械验证的状态机：

  1. **四态机**（:class:`SkillState` / :func:`next_state`）—— 逐条 skill 按 Eq. 9 迁移。
  2. **fitness 累计**（:class:`SkillRecord` / :func:`record_episode` / :func:`fitness`）
     —— 复用现有 skill 元数据（``name`` + SKILL.md frontmatter 里的
     ``description`` / ``category`` / ``trigger_keywords``）。
  3. **加载闸门**（:func:`is_loadable` / :func:`loadable_skills`）—— retired **不加载**，
     对齐灵枢现有 ``_disabled/`` 目录约定（retired ⇔ 移出可加载集）。
  4. **建议（不自动淘汰）**（:func:`retire_candidates` / :func:`mutation_candidates`）
     —— 只**产建议**：谁该退役、谁落在变异带 ``[δ_low, δ_high]``（边界态，可交 LLM
     改写）。**决策权在人**（PLAN LWP19「不做自动淘汰，先手动+建议」）。

用法::

    from agent_harness.core.skill_lifecycle import (
        SkillRecord, LifecyclePolicy, advance, loadable_skills)

    policy = LifecyclePolicy()                      # 默认阈值 = 论文量级
    records = [SkillRecord("search_web", usage=10, success=2)]   # 低质
    report = advance(records, policy)
    [r.name for r in loadable_skills(report.records)]   # 低质 skill 已 retired → 不加载

设计约束
--------
* **纯标准库**、无网络、无 LLM、确定性（同输入 → 同报告，``to_dict`` 可 diff）。
* **只加不改**：不触碰任何现有模块的 API；不移动文件、不写盘（本模块只算状态与建议，
  落盘/移动交由调用方，保零回归）。
* **不自动淘汰**：只产 ``retire_candidates`` 建议清单，状态迁移由 :func:`advance` 显式
  调用（人/调度器主导）。

不做
----
* 不做 LLM-guided mutation 的**改写**本身（只标记落在变异带的 skill，改写交 LLM/人）。
* 不做 RL 协同进化（SkillForge 的 GRPO + 冷启动 SFT）——PLAN LWP19 不做。
* 不接线进 ``graph`` / 技能加载器；本模块可离线单测、独立运转。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable


# ═══════════════════════════════════════════════════════════════════════════
# 一、四态生命周期
# ═══════════════════════════════════════════════════════════════════════════


class SkillState(str, Enum):
    """skill 生命周期四态（源论文 Eq. 9）。``str`` mixin → 可 JSON 序列化。"""

    TRIAL = "trial"       # 新引入：尚未积累足够使用量
    ACTIVE = "active"     # 正式可用：进入检索集
    STABLE = "stable"     # 稳定：高 fitness + 足够使用量
    RETIRED = "retired"   # 退役：低质量，**不再加载**

    @property
    def loadable(self) -> bool:
        """只有 retired 不可加载 —— 这是「低质量 skill 不再加载」的唯一判据。"""
        return self is not SkillState.RETIRED

    def __str__(self) -> str:  # 便于日志/报告
        return self.value


# ═══════════════════════════════════════════════════════════════════════════
# 二、阈值策略（源论文 §4.3 / Eq. 6/8）
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class LifecyclePolicy:
    """生命周期阈值。默认值取自源论文报告的量级（δ_stable=0.7 等）。

    * 迟滞阈值必须满足 ``delta_retire < delta_demote < delta_stable``（构造时校验）。
    * ``n_warm``：使用量低于此值不轻信经验成功率（Eq. 6）。
    * ``n_protect > n_retire``：代际 0（蒸馏原生）skill 更受保护（Eq. 8）。
    """

    delta_retire: float = 0.3    # 低于此 fitness → 可退役
    delta_demote: float = 0.5    # stable 跌破此值 → 先降级回 active（迟滞）
    delta_stable: float = 0.7    # 达到此 fitness → 可升 stable
    delta_low: float = 0.3       # 变异带下界（落在带内 → 建议改写）
    delta_high: float = 0.7      # 变异带上界

    n_warm: int = 3              # 信任经验成功率的最小使用量（Eq. 6）
    n_promote: int = 1           # trial → active 的最小使用量
    n_stable: int = 5            # active → stable 的最小使用量
    n_retire: int = 5            # 退役最小使用量（非原生）
    n_protect: int = 15          # 退役最小使用量（代际 0，更受保护）
    f_default: float = 0.5       # 使用量不足时的中性先验（Eq. 6）

    def __post_init__(self) -> None:
        if not (self.delta_retire < self.delta_demote < self.delta_stable):
            raise ValueError(
                "迟滞阈值须满足 delta_retire < delta_demote < delta_stable，"
                f"得到 {self.delta_retire} / {self.delta_demote} / {self.delta_stable}")
        if self.n_protect < self.n_retire:
            raise ValueError("n_protect 应 ≥ n_retire（代际 0 更受保护）")

    def retire_bar(self, generation: int) -> int:
        """代际感知的退役门槛（Eq. 8）：代际 0 用 n_protect，否则 n_retire。"""
        return self.n_protect if generation == 0 else self.n_retire


# ═══════════════════════════════════════════════════════════════════════════
# 三、skill 记录 + fitness（复用现有 skill 元数据）
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class SkillRecord:
    """一条 skill 的生命周期记录。

    复用灵枢现有 skill 元数据：``name`` 是目录名，``description`` / ``category`` /
    ``trigger_keywords`` 直接来自 SKILL.md frontmatter（由
    :meth:`from_skill_meta` 装配）。生命周期字段 ``usage`` / ``success`` /
    ``generation`` / ``state`` 是本模块新增的**状态字段**。
    """

    name: str
    usage: int = 0                  # U_i：被检索到的 episode 数（Eq. 7）
    success: int = 0                # C_i：其中成功的 episode 数
    generation: int = 0             # g_i：变异代际；0 = 蒸馏原生（Eq. 8）
    state: SkillState = SkillState.TRIAL
    description: str = ""
    category: str = ""
    trigger_keywords: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.usage < 0 or self.success < 0:
            raise ValueError("usage/success 不能为负")
        if self.success > self.usage:
            raise ValueError(f"success({self.success}) 不能大于 usage({self.usage})")
        if not isinstance(self.state, SkillState):
            self.state = SkillState(self.state)

    @classmethod
    def from_skill_meta(
        cls,
        name: str,
        meta: dict[str, Any] | None = None,
        *,
        usage: int = 0,
        success: int = 0,
        generation: int = 0,
        state: SkillState = SkillState.TRIAL,
    ) -> "SkillRecord":
        """从现有 SKILL.md frontmatter（dict）装配记录 —— 「复用现有 skill 元数据」。

        ``meta`` 缺字段一律兜底（缺 meta / 缺 description 不报错）。
        """
        meta = meta or {}
        kw = meta.get("trigger_keywords") or []
        if isinstance(kw, str):
            kw = [k.strip() for k in kw.split(",") if k.strip()]
        return cls(
            name=name,
            usage=usage,
            success=success,
            generation=generation,
            state=state,
            description=str(meta.get("description", "") or ""),
            category=str(meta.get("category", "") or ""),
            trigger_keywords=list(kw),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "fitness": round(fitness(self), 4),
            "usage": self.usage,
            "success": self.success,
            "generation": self.generation,
            "description": self.description,
            "category": self.category,
            "trigger_keywords": list(self.trigger_keywords),
        }


def fitness(record: SkillRecord, policy: LifecyclePolicy | None = None) -> float:
    """经验成功率（源论文 Eq. 6）：``C/U`` 当 ``U ≥ N_warm``，否则中性先验 ``f_default``。

    使用量不足时**不轻信**经验值（新 skill 用中性先验，避免一次失败即被判死）。
    """
    p = policy or LifecyclePolicy()
    if record.usage >= p.n_warm:
        return record.success / record.usage
    return p.f_default


def record_episode(record: SkillRecord, *, success: bool) -> SkillRecord:
    """记一次 episode（源论文 Eq. 7）：被检索即 ``U+1``，成功再 ``C+1``。

    就地更新并返回同一对象（fitness 是运行时累计量）。不改状态 —— 状态迁移走
    :func:`next_state` / :func:`advance`。
    """
    record.usage += 1
    if success:
        record.success += 1
    return record


# ═══════════════════════════════════════════════════════════════════════════
# 四、状态迁移规则（源论文 Eq. 9）
# ═══════════════════════════════════════════════════════════════════════════


def next_state(
    record: SkillRecord, policy: LifecyclePolicy | None = None
) -> SkillState:
    """按 Eq. 9 计算 record 的**下一状态**（一步迁移，不迭代到不动点）。

    迁移按当前状态分派：

      * ``TRIAL``  → ``ACTIVE``  若 ``U ≥ n_promote``
      * ``ACTIVE`` → ``STABLE``  若 ``f ≥ δ_stable ∧ U ≥ n_stable``
      * ``ACTIVE`` → ``RETIRED`` 若 ``f < δ_retire ∧ U ≥ N_retire^(g)``
      * ``STABLE`` → ``ACTIVE``  若 ``f < δ_demote``（迟滞降级，**先降级再谈退役**）
      * 其余保持原状
    """
    p = policy or LifecyclePolicy()
    s = record.state
    f = fitness(record, p)

    if s is SkillState.RETIRED:
        return SkillState.RETIRED  # 退役是终态（重新引入 = 新记录/新代际）

    if s is SkillState.TRIAL:
        return SkillState.ACTIVE if record.usage >= p.n_promote else SkillState.TRIAL

    if s is SkillState.ACTIVE:
        # 先判退役（低质量 + 够量），再判升 stable
        if f < p.delta_retire and record.usage >= p.retire_bar(record.generation):
            return SkillState.RETIRED
        if f >= p.delta_stable and record.usage >= p.n_stable:
            return SkillState.STABLE
        return SkillState.ACTIVE

    if s is SkillState.STABLE:
        # 迟滞：stable 必须先跌破 δ_demote 降级回 active，才可能被退役命中
        if f < p.delta_demote:
            return SkillState.ACTIVE
        return SkillState.STABLE

    return s


def advance(
    records: Iterable[SkillRecord], policy: LifecyclePolicy | None = None
) -> list[SkillRecord]:
    """对一批 skill 各做**一步**状态迁移，返回新记录列表（不改原对象）。

    只做一步（不迭代到不动点）—— 与源论文"每个训练迭代读一次 fitness、迁移一次"
    的语义一致；反复调用 :func:`advance` 即多轮收敛。
    """
    p = policy or LifecyclePolicy()
    out: list[SkillRecord] = []
    for r in records:
        ns = next_state(r, p)
        out.append(
            SkillRecord(
                name=r.name, usage=r.usage, success=r.success,
                generation=r.generation, state=ns,
                description=r.description, category=r.category,
                trigger_keywords=list(r.trigger_keywords),
            )
        )
    return out


# ═══════════════════════════════════════════════════════════════════════════
# 五、加载闸门（retired 不再加载）
# ═══════════════════════════════════════════════════════════════════════════


def is_loadable(record: SkillRecord) -> bool:
    """该 skill 是否可加载进检索集 —— **retired 返回 False**。

    对齐灵枢现有 ``_disabled/`` 目录约定：retired ⇔ 不在可加载目录里。
    """
    return record.state.loadable


def loadable_skills(records: Iterable[SkillRecord]) -> list[SkillRecord]:
    """过滤出可加载（非 retired）的 skill（PLAN LWP19 验收：「不再加载」）。"""
    return [r for r in records if is_loadable(r)]


# ═══════════════════════════════════════════════════════════════════════════
# 六、建议（手动 + 建议，不自动淘汰）
# ═══════════════════════════════════════════════════════════════════════════


def retire_candidates(
    records: Iterable[SkillRecord], policy: LifecyclePolicy | None = None
) -> list[str]:
    """**建议**退役的 skill 名（plan LWP19「先手动+建议」）—— 只产清单，不动状态。

    判据 = 下一步迁移会落到 RETIRED 的 active skill（``next_state == RETIRED``）。
    """
    p = policy or LifecyclePolicy()
    return [
        r.name for r in records
        if r.state is not SkillState.RETIRED and next_state(r, p) is SkillState.RETIRED
    ]


def mutation_candidates(
    records: Iterable[SkillRecord], policy: LifecyclePolicy | None = None
) -> list[str]:
    """落在变异带 ``[δ_low, δ_high]`` 的 skill 名（边界态，建议 LLM 改写）。

    只标记、不改写 —— mutation 的 LLM 操作不在本模块（PLAN LWP19 不做自动淘汰）。
    """
    p = policy or LifecyclePolicy()
    return [
        r.name for r in records
        if r.state is not SkillState.RETIRED and p.delta_low <= fitness(r, p) <= p.delta_high
    ]


# ═══════════════════════════════════════════════════════════════════════════
# 七、报告
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class LifecycleReport:
    """一次生命周期评估的报告（确定性，可 diff / 入库）。"""

    records: list[SkillRecord]
    policy: LifecyclePolicy

    def by_state(self, state: SkillState) -> list[SkillRecord]:
        return [r for r in self.records if r.state is state]

    @property
    def counts(self) -> dict[str, int]:
        return {s.value: len(self.by_state(s)) for s in SkillState}

    @property
    def loadable(self) -> list[SkillRecord]:
        return loadable_skills(self.records)

    @property
    def retired(self) -> list[SkillRecord]:
        return self.by_state(SkillState.RETIRED)

    @property
    def compactness(self) -> float:
        """可加载率 = |非 retired| / |总数|（库越紧凑越接近源论文"keep compact"）。"""
        if not self.records:
            return 0.0
        return len(self.loadable) / len(self.records)

    def to_dict(self) -> dict:
        return {
            "counts": self.counts,
            "compactness": round(self.compactness, 4),
            "loadable": [r.name for r in self.loadable],
            "retired": [r.name for r in self.retired],
            "skills": [r.to_dict() for r in self.records],
        }


def assess(
    records: Iterable[SkillRecord], policy: LifecyclePolicy | None = None
) -> LifecycleReport:
    """对一批 skill 做**一步**迁移并生成报告（= advance + 报告）。"""
    p = policy or LifecyclePolicy()
    return LifecycleReport(advance(records, p), p)
