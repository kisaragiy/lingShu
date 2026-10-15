"""轨迹冻结 → 回归用例（P2-1）—— 把**真实 run** 固化成可回放的测试样本。

痛点
----
改了代码 / prompt 之后，旧的历史失败轨迹就作废了 —— 只能重跑**新** run 看结果，
无法拿"当年那条挂掉的轨迹"当测试用例。真正需要的回归资产是：

    **把一次真实 run 的事件/checkpoint 导出成固定样本，回放驱动新代码** ——
    只 live 跑变化的那一段，其余（cut-point 之前）直接用录制输出回放。

本模块提供两层能力（Chronicle cut-point / Temporal replay 思路）：

  1. **冻结**：``freeze_from_events`` / ``freeze_from_run_result`` 把一次 run 的
     事件序列或 ``run_multi_agent`` 结果标准化成 :class:`FrozenTrace`，可 ``save``/``load``；
  2. **回放**：``replay`` 用 handler（= 新代码）重算每个 step，``cut_point`` 之前的
     step 直接用录制输出（不 live 跑），之后才 live；``assert_trace_matches`` 拿旧轨迹
     **断言新代码行为**（回归锁定），``regression_report`` 给出差异清单。

设计约束
--------
* 纯标准库、确定性：冻结物不含时间戳/随机量，同一输入 → 同一 fixture（可 diff、可入库）。
* 只加不改：不改任何现有 API；不引重依赖。
* 缺字段兜底、绝不抛（观测/测试工具不该改变被测代码行为）。

分工说明：LangGraph 的 checkpoint 在本仓库尚未接线（``graph.invoke`` 未传 checkpointer），
故本模块从 ``agent_logs/*.jsonl`` 事件流与 ``run_multi_agent`` 返回值这两处**已有可复现
产物**导出轨迹；将来接了 checkpointer，只需在 ``freeze_from_checkpoints`` 里加一层适配。

CLI（repo 根）::

    python scripts/trace_replay.py freeze --from-log agent_logs/x.jsonl --out fixture.json
    python scripts/trace_replay.py show   --trace fixture.json
    python scripts/trace_replay.py replay --trace fixture.json --handler mypkg.mod:fn
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# 事件里属于"输入侧"的键（冻结时归入 input）
_INPUT_KEYS = ("request", "task", "worker", "goal", "prompt_hash", "tools_hash",
               "model", "provider", "session_id", "task_type")
# 事件里属于"输出侧"的键（冻结时归入 output）
_OUTPUT_KEYS = ("output", "final_output", "text", "content", "response",
                "success", "errors", "finish_reason", "completion_tokens",
                "prompt_tokens", "worker_count")


# ── 数据模型 ──────────────────────────────────────────────────

@dataclass
class TraceStep:
    """轨迹里一步：标准化后的输入 + 录制输出。"""
    index: int
    kind: str                 # 事件类型 / "worker" / "finalize" …
    input: dict = field(default_factory=dict)
    output: Any = None
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"index": self.index, "kind": self.kind,
                "input": self.input, "output": self.output, "meta": self.meta}

    @classmethod
    def from_dict(cls, d: dict) -> "TraceStep":
        return cls(index=int(d.get("index", 0)), kind=str(d.get("kind", "step")),
                   input=d.get("input") or {}, output=d.get("output"),
                   meta=d.get("meta") or {})


@dataclass
class FrozenTrace:
    """一次 run 的冻结轨迹。``steps`` 有序，可 save/load、可 diff。"""
    name: str
    steps: list[TraceStep] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.steps)

    def to_dict(self) -> dict:
        return {"name": self.name, "meta": self.meta,
                "steps": [s.to_dict() for s in self.steps]}

    @classmethod
    def from_dict(cls, d: dict) -> "FrozenTrace":
        return cls(name=str(d.get("name", "trace")), meta=d.get("meta") or {},
                   steps=[TraceStep.from_dict(s) for s in d.get("steps", [])])

    def save(self, path: str | Path) -> str:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2,
                                sort_keys=True, default=str), encoding="utf-8")
        return str(p)

    @classmethod
    def load(cls, path: str | Path) -> "FrozenTrace":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)


# ── 冻结 ──────────────────────────────────────────────────────

def _event_to_step(index: int, ev: dict) -> TraceStep:
    """把一个日志事件标准化成 step（只保留确定性字段，丢 ts 等易变量）。"""
    ev = ev if isinstance(ev, dict) else {}
    data = ev.get("data")
    data = data if isinstance(data, dict) else {}
    kind = str(ev.get("type") or "event")
    inp = {k: data[k] for k in _INPUT_KEYS if k in data}
    out = {k: data[k] for k in _OUTPUT_KEYS if k in data}
    return TraceStep(index=index, kind=kind, input=inp,
                     output=out or None, meta={})


def freeze_from_events(records: list[dict], *, name: str = "trace",
                       meta: dict | None = None) -> FrozenTrace:
    """事件记录列表 → :class:`FrozenTrace`（索引即顺序）。"""
    steps = [_event_to_step(i, ev) for i, ev in enumerate(records or [])]
    return FrozenTrace(name=name, steps=steps, meta=dict(meta or {}))


def freeze_from_log(path: str | Path, *, name: str | None = None,
                    meta: dict | None = None) -> FrozenTrace:
    """从 ``agent_logs/*.jsonl`` 冻结核轨迹。"""
    from .online_eval import load_jsonl
    events = load_jsonl(path)
    return freeze_from_events(events, name=name or Path(path).stem, meta=meta)


def freeze_from_run_result(result: dict, *, name: str = "run") -> FrozenTrace:
    """把 ``run_multi_agent`` 的返回值冻结核轨迹。

    产出：每个 worker 一步（按名字排序，保证确定性）+ 一个 finalize 步。
    worker 的 input 取子任务描述（若有），output 取 ``{success, output, errors}``。
    """
    result = result if isinstance(result, dict) else {}
    steps: list[TraceStep] = []

    worker_results = result.get("worker_results") or {}
    worker_tasks = result.get("worker_tasks") or {}
    if isinstance(worker_results, dict):
        for wname in sorted(worker_results):
            r = worker_results[wname]
            r = r if isinstance(r, dict) else {}
            steps.append(TraceStep(
                index=len(steps), kind="worker",
                input={"worker": wname, "task": worker_tasks.get(wname)},
                output={
                    "success": r.get("success"),
                    "output": r.get("output"),
                    "errors": r.get("errors") or [],
                },
                meta={"elapsed_s": r.get("elapsed_s")},
            ))

    steps.append(TraceStep(
        index=len(steps), kind="finalize",
        input={"request": result.get("request", "")},
        output={"final_output": result.get("final_output", "")},
        meta={"rounds": result.get("rounds")},
    ))

    meta = {
        "trace_id": result.get("trace_id"),
        "rounds": result.get("rounds"),
        "no_progress_tripped": result.get("no_progress_tripped"),
    }
    return FrozenTrace(name=name, steps=steps, meta=meta)


# ── 回放 ──────────────────────────────────────────────────────

def _canonical(value: Any) -> str:
    """稳定序列化，用于输出比较（消除 dict 键序 / 类型细节差异）。"""
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _equal(a: Any, b: Any) -> bool:
    return _canonical(a) == _canonical(b)


@dataclass
class StepResult:
    index: int
    kind: str
    live: bool            # True = 由 handler 新算；False = 录制输出回放
    recorded: Any
    actual: Any
    match: bool

    def to_dict(self) -> dict:
        return {"index": self.index, "kind": self.kind, "live": self.live,
                "match": self.match, "recorded": self.recorded, "actual": self.actual}


@dataclass
class ReplayReport:
    name: str
    total: int
    matched: int
    mismatched: int
    results: list[StepResult] = field(default_factory=list)

    @property
    def matched_all(self) -> bool:
        return self.mismatched == 0

    @property
    def mismatches(self) -> list[StepResult]:
        return [r for r in self.results if not r.match]

    def to_dict(self) -> dict:
        return {"name": self.name, "total": self.total, "matched": self.matched,
                "mismatched": self.mismatched, "matched_all": self.matched_all,
                "mismatches": [r.to_dict() for r in self.mismatches]}


def replay(trace: FrozenTrace, handler: Callable[[TraceStep], Any] | None = None,
           *, cut_point: int | None = None) -> ReplayReport:
    """回放轨迹。

    Args:
        trace: 冻结轨迹。
        handler: ``step -> new_output``；**新代码**在此重算。为 ``None`` 时纯回放
            （全部用录制输出 → 恒匹配，用于确定性复现）。
        cut_point: 只对 ``step.index >= cut_point`` 的步 live 跑 handler；之前的步
            直接用录制输出回放（Chronicle cut-point：假定变化只发生在该点之后）。

    Returns:
        :class:`ReplayReport`，逐步记录 recorded/actual/match。
    """
    results: list[StepResult] = []
    for step in trace.steps:
        live = handler is not None and (cut_point is None or step.index >= cut_point)
        actual = handler(step) if live else step.output
        results.append(StepResult(
            index=step.index, kind=step.kind, live=live,
            recorded=step.output, actual=actual,
            match=_equal(actual, step.output),
        ))
    matched = sum(1 for r in results if r.match)
    return ReplayReport(name=trace.name, total=len(results), matched=matched,
                        mismatched=len(results) - matched, results=results)


def regression_report(trace: FrozenTrace,
                      handler: Callable[[TraceStep], Any] | None = None,
                      *, cut_point: int | None = None) -> dict:
    """回放并给出差异清单（回归判定：``mismatched>0`` 即行为变化）。"""
    return replay(trace, handler, cut_point=cut_point).to_dict()


def assert_trace_matches(trace: FrozenTrace,
                         handler: Callable[[TraceStep], Any] | None = None,
                         *, cut_point: int | None = None,
                         ignore_kinds: set[str] | frozenset[str] = frozenset()) -> ReplayReport:
    """拿旧轨迹**断言新代码行为**：不匹配即抛 ``AssertionError``（回归用例核心）。

    ``ignore_kinds`` 里的 step 类型不参与断言（如只关心 worker 输出、忽略 finalize）。
    返回 :class:`ReplayReport` 便于调用方进一步分析。
    """
    report = replay(trace, handler, cut_point=cut_point)
    bad = [r for r in report.mismatches if r.kind not in ignore_kinds]
    if bad:
        lines = []
        for r in bad:
            lines.append(f"  step[{r.index}] {r.kind}: recorded={_canonical(r.recorded)[:160]}")
            lines.append(f"                 actual  ={_canonical(r.actual)[:160]}")
        detail = "\n".join(lines)
        raise AssertionError(
            f"轨迹回放回归：{len(bad)}/{report.total} 步行为改变（{trace.name}）\n{detail}"
        )
    return report
