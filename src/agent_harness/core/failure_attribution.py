"""失败归因（决定性错误）—— LWP17。

对标 EMFA：*Error-Propagation Modeling for Failure Attribution in LLM-Based
Multi-Agent Systems* (arXiv:2610.11600)。核心观察：LLM 多 Agent 系统里，**观测到的
失败结果往往不直接暴露真正的错误** —— 一个错误的 ``agent-step`` 会在后续交互里
*级联传播*（cascading propagation），也会在*未解的交互循环*里反复出现
（persistent interaction loop）。因此"可疑步骤" ≠ 根因；需要把下游症状与真正的
原因区分开。

本模块的归因目标（与源论文一致）是 **决定性错误（decisive error）**：

    决定性错误 = 那个 ``agent-step`` 对，**只要把它纠正，失败执行就能救活**。

实现最小 EMFA 三件套（纯标准库 · 确定性 · 只读，不改任何现有行为）：

  1. **结构化轨迹表示** —— :class:`AttributionStep`（agent / action / ok / 上游依赖
     ``depends_on``）构成一张有向依赖图。
  2. **传播建模** ——
     * *级联传播*：某步失败若可由上游错误解释 → 标记为下游**症状**(symptom)，不选作
       根因；
     * *持久循环*：同一 ``(agent, action)`` 反复出现且仍在失败 → 未解循环，入候选。
  3. **传播感知候选筛选 + 反事实验证** ——
     * 候选 = 失败且**非症状**的步（依赖链上最早的因）+ 未解循环锚点；
     * 对每个候选做**反事实**：假定纠正该步（连带清掉由它传播出的症状），看整条
       执行是否恢复；能救活且清掉的失败最多者 = 决定性错误（并列时取更早的 = 更靠近
       根因）。

用法::

    from agent_harness.core.failure_attribution import (
        AttributionStep, attribute, attribute_from_run_result)

    report = attribute_from_run_result(run_result)   # 灵枢 run_multi_agent 的返回值
    if not report.recovered:
        print(report.decisive_agent, report.decisive_action)
        print(report.explanation)

设计约束
--------
* 纯标准库、无重依赖；同一输入 → 同一报告（``to_dict`` 可 diff / 入库）。
* 缺字段兜底、绝不抛（观测/归因工具不该改变被测代码行为）。
* 只加不改：不触碰任何现有模块的 API。

不做
----
不上重型归因模型（不训练、不调 LLM 判定）；本模块只做图上的结构化传播推断 +
反事实筛选，作为灵枢 ``Finalizer`` 质量不达标时的**离线/在线归因提示**。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence


# ── 数据模型 ──────────────────────────────────────────────────

@dataclass
class AttributionStep:
    """轨迹里的一步：一个 ``agent`` 执行的一个 ``action``。

    ``depends_on`` 给出该步**消费了哪些上游步**的索引（依赖图的边）。灵枢的
    Supervisor-Worker 是并行 worker，默认彼此独立（``depends_on`` 为空 → 无级联）；
    ``finalize`` 步骤则依赖全部 worker，因此任何一个 worker 失败都会把它标成症状。
    """
    index: int
    agent: str
    action: str
    ok: bool = True
    detail: str = ""
    depends_on: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "agent": self.agent,
            "action": self.action,
            "ok": bool(self.ok),
            "detail": self.detail,
            "depends_on": list(self.depends_on),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AttributionStep":
        return cls(
            index=int(d.get("index", 0)),
            agent=str(d.get("agent", "")),
            action=str(d.get("action", "")),
            ok=bool(d.get("ok", True)),
            detail=str(d.get("detail", "")),
            depends_on=[int(i) for i in (d.get("depends_on") or [])],
        )


@dataclass
class Candidate:
    """一个候选决定性错误，带反事实验证结果。"""
    index: int
    agent: str
    action: str
    kind: str                 # "root"（非症状的失败根） | "loop"（未解循环锚点）
    resolves: int             # 纠正它能清掉的下游失败步数
    recovers_run: bool        # 纠正它之后整条执行是否恢复
    downstream: list[int]     # 会随之恢复的下游（症状）步

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "agent": self.agent,
            "action": self.action,
            "kind": self.kind,
            "resolves": self.resolves,
            "recovers_run": self.recovers_run,
            "downstream": list(self.downstream),
        }


@dataclass
class AttributionReport:
    """一次归因的完整结果。"""
    total_steps: int
    failed_steps: list[int]
    decisive_index: int | None
    decisive_agent: str
    decisive_action: str
    recovered: bool                       # 纠正决定性错误后能否救活
    confidence: float                     # 0..1：单点纠正对失败的覆盖度
    candidates: list[Candidate] = field(default_factory=list)
    symptoms: list[int] = field(default_factory=list)
    loops: list[dict] = field(default_factory=list)
    explanation: str = ""

    @property
    def decisive(self) -> tuple[str, str] | None:
        if self.decisive_index is None:
            return None
        return (self.decisive_agent, self.decisive_action)

    def to_dict(self) -> dict:
        return {
            "total_steps": self.total_steps,
            "failed_steps": list(self.failed_steps),
            "decisive_index": self.decisive_index,
            "decisive_agent": self.decisive_agent,
            "decisive_action": self.decisive_action,
            "recovered": self.recovered,
            "confidence": round(self.confidence, 3),
            "candidates": [c.to_dict() for c in self.candidates],
            "symptoms": list(self.symptoms),
            "loops": list(self.loops),
            "explanation": self.explanation,
        }


# ── 图基元 ────────────────────────────────────────────────────

def _normalise(steps: Sequence[Any]) -> list[AttributionStep]:
    out: list[AttributionStep] = []
    for i, s in enumerate(steps or []):
        if isinstance(s, AttributionStep):
            st = s
            if st.index != i and st.index >= 0:
                # 保持传入顺序为权威索引（确定性）
                st = AttributionStep(i, st.agent, st.action, st.ok, st.detail, st.depends_on)
        elif isinstance(s, dict):
            d = dict(s)
            d.setdefault("index", i)
            d["index"] = i
            st = AttributionStep.from_dict(d)
        else:
            continue
        out.append(st)
    return out


def _children_map(steps: list[AttributionStep]) -> dict[int, list[int]]:
    """父 → 子 邻接（只保留合法边，杜绝自环/越界/重复）。"""
    n = len(steps)
    children: dict[int, list[int]] = {i: [] for i in range(n)}
    for st in steps:
        for p in st.depends_on:
            if 0 <= p < n and p != st.index and st.index not in children[p]:
                children[p].append(st.index)
    for k in children:
        children[k].sort()
    return children


def _reachable(children: dict[int, list[int]], start: int) -> set[int]:
    """从 ``start`` 出发沿依赖边可达的全部下游索引（不含自身）。"""
    seen: set[int] = set()
    stack = list(children.get(start, []))
    while stack:
        x = stack.pop()
        if x in seen:
            continue
        seen.add(x)
        stack.extend(children.get(x, []))
    return seen


# ── 传播建模 ──────────────────────────────────────────────────

def _detect_loops(steps: list[AttributionStep]) -> list[dict]:
    """持久交互循环：同一 ``(agent, action)`` 出现 ≥2 次且仍有失败。

    返回 ``[{"agent","action","count","fails","anchor"}]``，anchor = 首个失败出现的
    索引（确定性）。只在确实有失败时算作"未解循环"。
    """
    by_key: dict[tuple[str, str], list[AttributionStep]] = {}
    for st in steps:
        by_key.setdefault((st.agent, st.action), []).append(st)

    loops: list[dict] = []
    for (agent, action), group in by_key.items():
        if len(group) < 2:
            continue
        failing = [s for s in group if not s.ok]
        if not failing:
            continue
        loops.append({
            "agent": agent,
            "action": action,
            "count": len(group),
            "fails": len(failing),
            "anchor": failing[0].index,
        })
    loops.sort(key=lambda d: d["anchor"])
    return loops


def _is_symptom(idx: int, steps: list[AttributionStep], upstream: dict[int, set[int]]) -> bool:
    """某失败步是否为**下游症状**（其祖先里存在另一个失败步 → 可能被传播污染）。"""
    if steps[idx].ok:
        return False
    return any(not steps[j].ok for j in upstream.get(idx, set()))


# ── 反事实 ────────────────────────────────────────────────────

def _counterfactual(idx: int, steps: list[AttributionStep],
                    children: dict[int, list[int]]) -> tuple[bool, int, list[int]]:
    """假定"纠正 ``idx``"后，整条执行是否恢复？清掉多少失败步？波及哪些下游？

    模型（保守 · 确定性）：
      * 纠正 ``idx`` → ``idx`` 自身不再是失败；
      * 沿依赖边可达、且原本失败的下游步（症状）一并恢复（其唯一可见污染源被清除）；
      * 其余位置的失败保持不变 → 若仍有残留失败，则"单点纠正"未救活整条执行。
    """
    if steps[idx].ok:
        return (False, 0, [])
    downstream = _reachable(children, idx)
    restored = [idx] + sorted(d for d in downstream if not steps[d].ok)
    remaining = [s.index for s in steps
                 if not s.ok and s.index not in set(restored)]
    recovers = len(remaining) == 0
    return (recovers, len(restored), [d for d in restored if d != idx])


# ── 主入口 ────────────────────────────────────────────────────

def attribute(steps: Sequence[Any]) -> AttributionReport:
    """对一条失败轨迹做传播感知归因，返回 :class:`AttributionReport`。

    Args:
        steps: ``AttributionStep`` 或等值 dict 的有序序列。索引以序列位置为准。

    Returns:
        报告。``decisive_index`` 为 ``None`` 表示"无可归因的失败"（全部成功）。
    """
    norm = _normalise(steps)
    children = _children_map(norm)
    # 计算每个点的祖先集合（含全部间接上游）
    ancestors: dict[int, set[int]] = {i: set() for i in range(len(norm))}
    for st in norm:
        for p in st.depends_on:
            if 0 <= p < len(norm) and p != st.index:
                ancestors[st.index].add(p)
                ancestors[st.index] |= ancestors.get(p, set())
    # 重新按拓扑序补全（索引可能非拓扑序，做一次闭包）
    changed = True
    while changed:
        changed = False
        for i in range(len(norm)):
            for p in list(ancestors[i]):
                if not ancestors[p] <= ancestors[i]:
                    ancestors[i] |= ancestors[p]
                    changed = True
    # 自环 / 成环时祖先集合可能含自身 → 剔除（"自己"不是自己的传播症状）
    for i in range(len(norm)):
        ancestors[i].discard(i)

    failed = [s.index for s in norm if not s.ok]
    loops = _detect_loops(norm)
    loop_anchors = {lp["anchor"] for lp in loops}
    symptoms = [i for i in failed if _is_symptom(i, norm, ancestors)]
    symptom_set = set(symptoms)

    # 候选筛选：非症状的失败根 + 未解循环锚点（去重）
    candidate_idx = [i for i in failed if i not in symptom_set]
    for a in sorted(loop_anchors):
        if a not in candidate_idx and a in failed:
            candidate_idx.append(a)
    candidate_idx.sort()

    candidates: list[Candidate] = []
    for i in candidate_idx:
        recovers, resolves, downstream = _counterfactual(i, norm, children)
        kind = "loop" if i in loop_anchors else "root"
        candidates.append(Candidate(
            index=i, agent=norm[i].agent, action=norm[i].action,
            kind=kind, resolves=resolves, recovers_run=recovers,
            downstream=downstream))

    # 排序：先"能救活"的，再"清掉失败多"的，最后取更早索引（更靠近根因）
    candidates.sort(key=lambda c: (not c.recovers_run, -c.resolves, c.index))

    decisive = candidates[0] if candidates else None
    recovered = bool(decisive and decisive.recovers_run) if decisive else not failed
    conf = _confidence(decisive, failed)
    explanation = _explain(norm, decisive, symptoms, loops, failed)

    return AttributionReport(
        total_steps=len(norm),
        failed_steps=failed,
        decisive_index=decisive.index if decisive else None,
        decisive_agent=decisive.agent if decisive else "",
        decisive_action=decisive.action if decisive else "",
        recovered=recovered,
        confidence=conf,
        candidates=candidates,
        symptoms=symptoms,
        loops=loops,
        explanation=explanation,
    )


def _confidence(decisive: Candidate | None, failed: list[int]) -> float:
    """单点纠正对失败的覆盖度：全部救活 → 1.0。"""
    if not failed:
        return 1.0
    if decisive is None:
        return 0.0
    return min(1.0, decisive.resolves / len(failed))


def _explain(steps: list[AttributionStep], decisive: Candidate | None,
             symptoms: list[int], loops: list[dict], failed: list[int]) -> str:
    if not failed:
        return "轨迹全部成功，无可归因的决定性错误。"
    if decisive is None:
        return f"存在 {len(failed)} 个失败步，但未能收敛到单一候选（无可用根因）。"
    parts: list[str] = []
    verdict = "可救活" if decisive.recovers_run else "不能单独救活"
    parts.append(
        f"决定性错误 = [{decisive.index}] {decisive.agent}::{decisive.action}"
        f"（{verdict}；纠正后清掉 {decisive.resolves} 个失败步）")
    if decisive.downstream:
        parts.append("传播症状（随之恢复）: " + ", ".join(f"[{d}]" for d in decisive.downstream))
    others = [i for i in symptoms if i not in set(decisive.downstream)]
    if others:
        parts.append("其它下游症状（非根因）: " + ", ".join(f"[{i}]" for i in others))
    if loops:
        tags = "; ".join(f"{lp['agent']}::{lp['action']}×{lp['count']}" for lp in loops)
        parts.append(f"未解循环: {tags}")
    if not decisive.recovers_run:
        residual = [i for i in failed if i != decisive.index and i not in set(decisive.downstream)]
        parts.append("单点纠正后仍残留失败: " + ", ".join(f"[{i}]" for i in residual))
    return " | ".join(parts)


# ── 灵枢适配 ──────────────────────────────────────────────────

def attribute_from_run_result(result: dict, *,
                              depends_on: dict[str, Iterable[str]] | None = None,
                              ) -> AttributionReport:
    """把灵枢 ``run_multi_agent`` 的返回值转成轨迹并归因。

    轨迹构造（确定性）：
      * 每个 worker 一步：``agent``=worker 名，``action``=子任务描述（无则用名），
        ``ok``=``success``，``detail``=输出/错误摘要；
      * 一个 ``finalize`` 步：依赖**全部 worker** → 任一 worker 失败都会把 finalize
        标成**症状**（这正是"观测失败不暴露根因"的最小复现）；
        ``ok`` = final_output 非空且完成契约 complete（缺契约时退化为"有输出"）。

    ``depends_on``：可选 ``{worker: [上游 worker, ...]}``，用于声明 worker 间真实的
    数据依赖（灵枢默认并行 → 为空）。
    """
    result = result if isinstance(result, dict) else {}
    worker_results = result.get("worker_results")
    worker_results = worker_results if isinstance(worker_results, dict) else {}
    worker_tasks = result.get("worker_tasks")
    worker_tasks = worker_tasks if isinstance(worker_tasks, dict) else {}

    steps: list[AttributionStep] = []
    order: list[str] = []
    if isinstance(worker_results, dict):
        order = sorted(worker_results)  # 名字排序 → 确定性
    dep_map = depends_on or {}

    worker_index: dict[str, int] = {}
    for wname in order:
        r = worker_results.get(wname)
        r = r if isinstance(r, dict) else {}
        errors = r.get("errors") or []
        detail = (r.get("output") or "")
        if errors:
            detail = (detail + " " + "; ".join(str(e) for e in errors)).strip()
        idx = len(steps)
        worker_index[wname] = idx
        steps.append(AttributionStep(
            index=idx, agent=wname,
            action=str(worker_tasks.get(wname) or wname),
            ok=bool(r.get("success", True)),
            detail=str(detail)[:500],
            depends_on=[],
        ))

    # 第二遍解析依赖边（此时 worker_index 已完整 → 不依赖排序先后）
    for wname in order:
        deps: list[int] = []
        for up in dep_map.get(wname, []) or []:
            j = worker_index.get(str(up))
            if j is not None and j != worker_index[wname]:
                deps.append(j)
        steps[worker_index[wname]].depends_on = deps

    # finalize 步：依赖全部 worker → 级联传播的落点
    contract = result.get("completion_contract") or {}
    final_out = result.get("final_output") or ""
    if contract and isinstance(contract, dict) and contract.get("features"):
        fin_ok = bool(contract.get("complete"))
    else:
        fin_ok = bool(str(final_out).strip())
    steps.append(AttributionStep(
        index=len(steps), agent="supervisor", action="finalize",
        ok=fin_ok,
        detail=str(final_out)[:500],
        depends_on=list(range(len(order))),
    ))

    return attribute(steps)
