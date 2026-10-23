"""Multi-Agent Graph — Supervisor-Worker orchestration using LangGraph.

Architecture:
    User Request
        ↓
    supervisor_analyze (classify task, assign workers)
        ↓
    supervisor_dispatch (fan-out to workers — parallel execution)
        ├→ search_worker    (web search, RAG, fetch)
        ├→ analyze_worker   (data processing, code exec, summarize)
        └→ execute_worker   (desktop, browser, ComfyUI, messaging)
        ↓
    supervisor_collect (gather results, check completeness)
        ↓
    supervisor_route
        ├→ supervisor_replan (not done, re-assign) → supervisor_dispatch
        └→ finalizer (done, synthesize final response)
"""

import concurrent.futures
import contextvars
import json
import os
import re
import sys
import time
import uuid
from typing import Any, Literal

from langgraph.graph import END, StateGraph

from ..agents import (
    run_worker,
    supervisor_analyze,
    supervisor_collect,
    supervisor_replan,
)
from ..config import SUPERVISOR_MAX_ROUNDS

# ─── Cancel event mechanism ───
from ..pipeline.cancel import (
    is_cancelled,
)
from ..budget import BudgetLevel, TokenBudget
from ..pipeline.circuit_breaker import CircuitBreaker, new_scoped_breaker
from ..pipeline.llm import ledger_total
from ..pipeline.state import SupervisorState, WorkerResult
from ..pipeline.tracing import TraceCollector
from ..retry import NoProgressError, _default_call_key, with_no_progress_guard

# Module-level trace collector — set before graph invocation
_trace_collector: TraceCollector | None = None
# Module-level progress queue — set before graph invocation for SSE streaming
_progress_queue = None


# ─── Circuit-breaker feeding helpers ───
# Token counts are accumulated in pipeline.llm's module ledger by every LLM call
# site (supervisor / workers / llm.py). We feed the breaker with the delta since
# the last feed so token-budget + no-progress detection actually fire mid-run.
# The last-fed watermark is stashed on the breaker instance.
#
# 注意：实例每 run 新建，**但台账是进程级累计**。水位线必须锚在 run 起飞点
# （new_scoped_breaker），否则首次喂数会把历史所有 run 的 token 算进本次。

def _new_circuit_breaker() -> CircuitBreaker:
    """构造 run 级熔断器，token 水位线锚在当前全局台账累计（见 new_scoped_breaker）。"""
    return new_scoped_breaker()


def _feed_breaker_tokens(cb: CircuitBreaker) -> None:
    """Feed cumulative LLM token delta into the breaker."""
    cur = ledger_total()
    prev = getattr(cb, "_ledger_total", 0)
    delta = cur - prev
    if delta > 0:
        cb.add_tokens(delta)
        cb._ledger_total = cur  # type: ignore[attr-defined]


def _feed_breaker_output(cb: CircuitBreaker, wname: str, output: Any) -> None:
    """Record a worker's output for no-progress detection."""
    cb.record_output(wname, str(output)[:200])


def _breaker_tripped(cb: CircuitBreaker | None) -> bool:
    """Feed any pending tokens then return whether the breaker has tripped."""
    if cb is None:
        return False
    _feed_breaker_tokens(cb)
    return bool(cb.check()["tripped"])


# ─── Token 预算三层软限制（WARN / ASK / STOP）接线 ───
# `core/budget.py` 的 `TokenBudget` 原语齐备（WARN 提醒 / ASK 请求确认 / STOP 急停
# + session kill switch + cache 指标），但此前在生产调用链上 0 命中 —— 有芯未接线。
# 这里把它挂到与硬熔断**同一个每轮检查点**（supervisor_dispatch 收尾 +
# supervisor_route 判路），共用 pipeline.llm 的全局 token 台账。两者分工：
#   * CircuitBreaker：硬阈值，撞上即停（token / 超时 / 无进展）。
#   * TokenBudget  ：软限制三层 —— 先记日志提醒，再告警请求确认，最后强制急停。
# 预算实例存在 `state["token_budget"]`；喂数的水位线 `_ledger_total` 挂在实例上
# 实例每 run 新建，但台账是进程级累计 —— 水位线锚在 run 起飞点，否则历史消耗
# 会算进本次（见 _new_token_budget）。缺此字段的旧调用方行为完全不变。

def _new_token_budget() -> TokenBudget:
    """构造会话级 TokenBudget；阈值可用环境变量覆盖（非法值忽略并提示）。

    token 水位线同样锚在当前全局台账累计上 —— 预算与熔断器共用
    ``_LLM_TOKEN_LEDGER``，同一个作用域坑必须一起修，否则新会话开局即被判超预算。
    """
    budget = TokenBudget()
    budget._ledger_total = ledger_total()  # type: ignore[attr-defined]
    for attr, env_name in (("soft_limit", "HARNESS_TOKEN_SOFT_LIMIT"),
                           ("hard_limit", "HARNESS_TOKEN_HARD_LIMIT")):
        raw = os.environ.get(env_name)
        if not raw:
            continue
        try:
            setattr(budget, attr, int(raw))
        except (TypeError, ValueError):
            print(f"[Supervisor] ⚠️ 忽略非法 {env_name}={raw!r}", file=sys.stderr)
    return budget


def _feed_budget(state: SupervisorState) -> BudgetLevel:
    """把本轮新增的 LLM token 记进会话预算，返回当前预算级别。

    与 `_feed_breaker_tokens` 同一台账来源，但**两本账各记各的 delta**（水位线分别
    挂在 breaker 与 budget 实例上），互不吞数。state 里没有预算 → 返回 OK。
    """
    budget = state.get("token_budget") if isinstance(state, dict) else None
    if not isinstance(budget, TokenBudget):
        return BudgetLevel.OK
    cur = ledger_total()
    delta = cur - getattr(budget, "_ledger_total", 0)
    if delta > 0:
        budget._ledger_total = cur  # type: ignore[attr-defined]
        return budget.add(delta)
    return budget.check()


def _budget_allows_continue(state: SupervisorState, level: BudgetLevel) -> bool:
    """预算是否允许继续下一轮（ASK 未确认 / STOP / kill switch → False）。"""
    budget = state.get("token_budget") if isinstance(state, dict) else None
    if not isinstance(budget, TokenBudget):
        return True
    return budget.can_continue(level)


def _handle_budget_level(state: SupervisorState, level: BudgetLevel) -> list[dict]:
    """按预算级别记日志 / 发告警 / 触发急停，返回预算事件列表（OK → 空）。"""
    budget = state.get("token_budget") if isinstance(state, dict) else None
    if not isinstance(budget, TokenBudget) or level == BudgetLevel.OK:
        return []
    pct = round(budget.used / budget.soft_limit * 100, 1) if budget.soft_limit else 0.0
    if level == BudgetLevel.WARN:
        msg = (f"⚠️ Token 预算提醒：已用 {budget.used}/{budget.soft_limit} "
               f"({pct}%)，接近软限")
    elif level == BudgetLevel.ASK:
        msg = (f"🛑 Token 预算已超软限：{budget.used}/{budget.soft_limit} ({pct}%)，"
               f"需用户确认后才能继续 —— 本轮提前收尾")
    else:  # BudgetLevel.STOP
        reason = f"token 跑飞：{budget.used} ≥ {budget.stop_at:g}× 软限 {budget.soft_limit}"
        budget.kill(reason)
        msg = f"🔥 Token 预算急停（kill switch）：{reason}"
    print(f"[Supervisor] {msg}", file=sys.stderr)
    if _progress_queue:
        _progress_queue.put({"type": "progress", "content": msg + "\n"})
    return [{"level": level.value, "used": budget.used,
             "soft_limit": budget.soft_limit, "message": msg}]


# ─── P1-3: 主回路无进展熔断（with_no_progress_guard 接线）───
# 主回路 = supervisor 的多轮 worker 派发循环（dispatch → collect → replan → dispatch）。
# 每轮都按 (worker, task) 重新调用 `run_worker`；若主管反复派**同一个 worker + 同一个
# 子任务**，就是在空转烧 token。这里用既有的 `with_no_progress_guard` 把「连续 K 次
# 同 tool+同参」变成真正的熔断：
#   * 每个 worker（= tool）一个独立 guard 实例 → 多 worker 交错派发时互不掩盖计数；
#   * guard 状态跨轮次保留（模块级、每次 run 重置）；
#   * 熔断那一次**不真正执行** worker（check 在派发前跑，非装饰在 run_worker 上）；
#   * key_fn 可注入（默认 = worker 名 + 任务指纹），K 可配（默认 3）。

NO_PROGRESS_MAX_REPEATS = 3

_no_progress_enabled: bool = False
_no_progress_max_repeats: int = NO_PROGRESS_MAX_REPEATS
_no_progress_key_fn = None  # type: ignore[var-annotated]  # (worker_name, task) -> hashable
_no_progress_guards: dict = {}


def _worker_call_key(wname: str, task: Any) -> tuple:
    """默认 tool 粒度指纹：worker 名（tool）+ 任务参数指纹。"""
    return (str(wname), _default_call_key((str(task),), {}))


def set_no_progress_detection(enabled: bool = True,
                              max_repeats: int = NO_PROGRESS_MAX_REPEATS,
                              key_fn=None) -> None:
    """启用/配置主回路无进展熔断（每次 `run_multi_agent` 前调用）。

    Args:
        enabled: 是否启用；False 时 `check_no_progress` 一律 no-op（默认关闭，向后兼容）。
        max_repeats: 连续同 (worker, task) 达到该次数即熔断（>=1）。
        key_fn: 可注入的 (worker_name, task) -> hashable 指纹；默认 worker 名 + 任务指纹。
    """
    global _no_progress_enabled, _no_progress_max_repeats, _no_progress_key_fn, _no_progress_guards
    if max_repeats < 1:
        raise ValueError("max_repeats 必须 >= 1")
    _no_progress_enabled = bool(enabled)
    _no_progress_max_repeats = int(max_repeats)
    _no_progress_key_fn = key_fn
    _no_progress_guards = {}


def _guard_for(wname: str):
    """取（或建）某 worker 专属的 no-progress guard（per-tool 独立计数）。"""
    g = _no_progress_guards.get(wname)
    if g is None:
        base = _no_progress_key_fn or _worker_call_key

        def _kf(task, _w=wname, _base=base):
            return _base(_w, task)

        @with_no_progress_guard(max_repeats=_no_progress_max_repeats, key_fn=_kf)
        def _guard(task):
            return task

        g = _guard
        _no_progress_guards[wname] = g
    return g


def check_no_progress(wname: str, task: Any) -> None:
    """派发前检查：连续 K 次同 (worker, task) → 抛 :class:`NoProgressError`。

    检测未启用时一律 no-op。**在调用线程（主派发线程）串行执行**，避免并发竞争。
    """
    if not _no_progress_enabled:
        return
    _guard_for(wname)(task)


def _record_no_progress_trip(wname: str, task: Any, exc: NoProgressError,
                             results: dict, events: list) -> None:
    """记录一次熔断：占位 worker 结果 + 事件，供 router 收尾 / 完成契约标记未完成。"""
    results[wname] = WorkerResult(
        worker_name=wname, success=False,
        output=f"⛔ 无进展熔断：{wname} 连续 {exc.repeats} 次重复同一子任务，已跳过执行",
        data=None, trace_steps=[], errors=[f"no_progress:{exc.repeats}"], elapsed_s=0,
    )
    events.append({"worker": wname, "task": str(task)[:120], "repeats": exc.repeats})


def set_trace_collector(collector: TraceCollector):
    """Set the trace collector for the current execution."""
    global _trace_collector
    _trace_collector = collector


def set_progress_queue(q):
    """Set a queue for real-time progress events (SSE streaming)."""
    global _progress_queue
    _progress_queue = q


def clear_progress_queue():
    """Clear the progress queue after execution."""
    global _progress_queue
    _progress_queue = None


# ─── Dispatch: run workers in parallel ───

def _partition_workers(workers: list, capabilities: dict) -> tuple:
    """Split workers into (safe, side_effect). Side-effect workers conflict with
    each other (file system / desktop / ComfyUI queue) — Cognition P2 fix:
    at most one side-effect worker may run in the parallel batch; the rest
    must run serially. Safe (read-only) workers may parallel with one risky.
    """
    safe, risky = [], []
    for w in workers:
        if capabilities.get(w, {}).get("side_effect"):
            risky.append(w)
        else:
            safe.append(w)
    return safe, risky


def supervisor_dispatch(state: SupervisorState) -> dict:
    """Fan-out tasks to workers, running them in parallel."""
    workers_assigned = state.get("workers_assigned", [])
    worker_tasks = state.get("worker_tasks", {})

    # Check cancellation before dispatching（ENG-21：只查本 session 的信号）
    if is_cancelled(state.get("task_id", "")):
        print("[Supervisor] ⛔ 用户取消了任务")
        if _progress_queue:
            _progress_queue.put({"type": "progress", "content": "⛔ 任务已取消\n"})
        return {"all_done": True, "final_output": "⛔ 任务已被用户取消"}

    if not workers_assigned:
        return {"all_done": True}

    print(f"\n[Supervisor] 调度 {len(workers_assigned)} 个 Worker: {workers_assigned}")
    if _progress_queue:
        _progress_queue.put({
            "type": "progress",
            "content": f"🔀 分配 {len(workers_assigned)} 个 Worker: {', '.join(workers_assigned)}\n"
        })

    # Cognition P2 fix: side-effect workers (analyze/execute share file system,
    # desktop, ComfyUI queue) must not run in parallel with each other.
    from ..agents.supervisor import WORKER_CAPABILITIES
    _safe, _risky = _partition_workers(workers_assigned, WORKER_CAPABILITIES)
    if len(_risky) > 1:
        parallel_workers = _safe + [_risky[0]]
        serial_workers = _risky[1:]
        print(f"[Supervisor] 副作用 Worker 串行化: 并行 {parallel_workers}，串行 {serial_workers}")
        if _progress_queue:
            _progress_queue.put({
                "type": "progress",
                "content": f"🛡️ {len(serial_workers)} 个副作用 Worker 转串行（防并行冲突）\n"
            })
    else:
        parallel_workers = workers_assigned
        serial_workers = []

    results: dict[str, WorkerResult] = {}
    errors: dict[str, list[str]] = {}
    no_progress_tripped = False
    no_progress_events: list[dict] = []

    def _run_with_trace(wname: str, task: str):
        """Run worker with optional trace span."""
        if _progress_queue:
            _progress_queue.put({
                "type": "progress",
                "content": f"▶️ {wname} 开始工作: {task[:60]}...\n"
            })
        task_id = state.get("task_id", "")
        # SEC-2：ThreadPoolExecutor 不传播 contextvars —— 不显式 copy 的话，worker 线程里
        # RAG 可见作用域是空串（=全见），rag_query 工具就能被诱导读到别人的 collection。
        ctx = contextvars.copy_context()
        if _trace_collector:
            with _trace_collector.span(f"worker:{wname}", "worker", task=task[:80]):
                result = ctx.run(run_worker, wname, task, task_id=task_id)
                _trace_collector.record_metadata("output_preview", str(result.get("output", ""))[:100])
                return result
        return ctx.run(run_worker, wname, task, task_id=task_id)

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(parallel_workers)) as pool:
        futures = {}
        for wname in parallel_workers:
            task = worker_tasks.get(wname, state["request"])
            # P1-3: 派发前无进展检查 —— 连续同 tool+同参则熔断，跳过本次执行
            try:
                check_no_progress(wname, task)
            except NoProgressError as e:
                no_progress_tripped = True
                _record_no_progress_trip(wname, task, e, results, no_progress_events)
                print(f"  🔥 {wname}: 无进展熔断（连续 {e.repeats} 次重复），跳过执行")
                continue
            print(f"  → {wname}: {task[:60]}...")
            futures[pool.submit(_run_with_trace, wname, task)] = wname

        for future in concurrent.futures.as_completed(futures):
            # Check cancellation
            if is_cancelled(state.get("task_id", "")):
                print("[Supervisor] ⛔ 取消检测到，停止等待 Worker")
                for f in futures:
                    f.cancel()
                break
            wname = futures[future]
            try:
                result = future.result(timeout=120)
                results[wname] = result
                status = "✅" if result.get("success") else "❌"
                print(f"  {status} {wname} ({result.get('elapsed_s', 0):.1f}s): "
                      f"{str(result.get('output', ''))[:80]}")
                if _progress_queue:
                    snippet = str(result.get("output", ""))[:60]
                    _progress_queue.put({
                        "type": "progress",
                        "content": f"{status} {wname} 完成 ({result.get('elapsed_s', 0):.1f}s): {snippet}\n"
                    })
            except Exception as e:
                results[wname] = WorkerResult(
                    worker_name=wname, success=False, output="",
                    data=None, trace_steps=[], errors=[str(e)], elapsed_s=0,
                )
                errors[wname] = [str(e)]
                print(f"  ❌ {wname}: {e}")

    # Serial tail: remaining side-effect workers run one by one (Cognition P2)
    for wname in serial_workers:
        if is_cancelled(state.get("task_id", "")):
            print("[Supervisor] ⛔ 取消检测到，跳过串行 Worker")
            break
        task = worker_tasks.get(wname, state["request"])
        # P1-3: 串行分支同样做派发前无进展检查
        try:
            check_no_progress(wname, task)
        except NoProgressError as e:
            no_progress_tripped = True
            _record_no_progress_trip(wname, task, e, results, no_progress_events)
            print(f"  🔥 {wname} (串行): 无进展熔断（连续 {e.repeats} 次重复），跳过执行")
            continue
        print(f"  → {wname} (串行): {task[:60]}...")
        try:
            result = _run_with_trace(wname, task)
            results[wname] = result
            status = "✅" if result.get("success") else "❌"
            print(f"  {status} {wname} (串行) ({result.get('elapsed_s', 0):.1f}s): "
                  f"{str(result.get('output', ''))[:80]}")
        except Exception as e:
            results[wname] = WorkerResult(
                worker_name=wname, success=False, output="",
                data=None, trace_steps=[], errors=[str(e)], elapsed_s=0,
            )
            errors[wname] = [str(e)]
            print(f"  ❌ {wname} (串行): {e}")

    # Feed circuit breaker with this round's tokens + per-worker outputs.
    cb = state.get("circuit_breaker")  # type: ignore[union-attr]
    if cb is not None:
        _feed_breaker_tokens(cb)
        for wname in workers_assigned:
            out = results.get(wname, {}).get("output", "")
            _feed_breaker_output(cb, wname, out)
        if _breaker_tripped(cb):
            print(f"[Supervisor] 🔥 熔断触发: {'; '.join(cb.check()['reasons'])}",
                  file=sys.stderr)
            if _progress_queue:
                _progress_queue.put({
                    "type": "progress",
                    "content": "🔥 任务熔断（token 超预算/超时/无进展），提前收尾\n",
                })

    # Token 预算三层软限制 —— 与硬熔断同一检查点（每轮收尾时结算本轮 token）。
    budget_level = _feed_budget(state)
    budget_events = _handle_budget_level(state, budget_level)
    budget_halts = not _budget_allows_continue(state, budget_level)

    # Collect trace steps
    all_traces = list(state.get("trace_steps", []))
    all_traces.append({
        "step": "supervisor_dispatch",
        "workers": [
            {"name": w, "success": results[w].get("success", False)}
            for w in workers_assigned
        ],
    })
    if no_progress_events:
        all_traces.append({
            "step": "supervisor_no_progress_fuse",
            "events": no_progress_events,
        })
    if budget_events:
        all_traces.append({
            "step": "supervisor_budget",
            "level": budget_level.value,
            "events": budget_events,
        })

    tripped = no_progress_tripped or (cb is not None and cb.check()["tripped"])
    return {
        "worker_results": results,
        "worker_errors": errors,
        "trace_steps": all_traces,
        "no_progress_tripped": no_progress_tripped or bool(state.get("no_progress_tripped", False)),
        "no_progress_events": list(state.get("no_progress_events", []) or []) + no_progress_events,
        "budget_level": budget_level.value,
        "budget_events": list(state.get("budget_events", []) or []) + budget_events,
        "all_done": True if (tripped or budget_halts) else state.get("all_done", False),
    }


# ─── Router after collect ───

def supervisor_route(state: SupervisorState) -> Literal["replan", "finalize"]:
    """Decide whether to do another round or finalize."""
    cb = state.get("circuit_breaker")  # type: ignore[union-attr]
    if _breaker_tripped(cb):
        print("[Supervisor] 🔥 熔断已触发，停止 Replan 提前收尾", file=sys.stderr)
        return "finalize"
    # Token 预算三层软限制：ASK（未确认）/ STOP / kill switch → 立即收尾
    budget_level = _feed_budget(state)
    if not _budget_allows_continue(state, budget_level):
        print(f"[Supervisor] 💰 Token 预算级别 {budget_level.value}，停止 Replan 提前收尾",
              file=sys.stderr)
        return "finalize"
    # P1-3: 主回路无进展熔断 → 立即收尾，不再 replan（否则只会重复空转）
    if state.get("no_progress_tripped"):
        print("[Supervisor] 🔥 无进展熔断（连续同 tool+同参），停止 Replan 提前收尾",
              file=sys.stderr)
        return "finalize"
    # WP16: infeasible task → finalize immediately, straight to the abstention
    # path (never replan, never fabricate a research report).
    if state.get("feasibility") == "infeasible":
        print("[Supervisor] ⚠️ 判定任务不可行，声明无法完成（不走调研报告）", file=sys.stderr)
        return "finalize"
    if state.get("all_done"):
        return "finalize"
    if state.get("round", 0) >= SUPERVISOR_MAX_ROUNDS:
        return "finalize"
    return "replan"


# ─── Finalizer ───

def _build_abstention_output(state: SupervisorState) -> dict:
    """WP16 abstention: declare the task unachievable instead of fabricating a report.

    Triggered when ``supervisor_collect`` set ``feasibility="infeasible"``. Lists
    what was attempted and why it was not enough. Deliberately does NOT use the
    1000-2000 word research-report prompt — that prompt would hard-code a
    "looks complete" report on top of evidence we do not have.
    """
    reason = (state.get("feasibility_reason") or "").strip() or "现有手段无法获得足够信息"
    worker_results = state.get("worker_results", {}) or {}
    worker_tasks = state.get("worker_tasks", {}) or {}
    worker_errors = state.get("worker_errors", {}) or {}

    order = list(state.get("workers_assigned", []) or [])
    for w in worker_results:
        if w not in order:
            order.append(w)

    lines = []
    for w in order:
        task = str(worker_tasks.get(w, "") or "").strip()
        r = worker_results.get(w, {})
        r = r if isinstance(r, dict) else {}
        ok = bool(r.get("success"))
        errs = list(r.get("errors") or []) + list(worker_errors.get(w, []) or [])
        detail = f"（{'成功' if ok else '失败'}）"
        if errs:
            detail += f" 失败原因: {'; '.join(str(e)[:120] for e in errs[:2])}"
        lines.append(f"- {w}: {task or '（无子任务描述）'} {detail}")
    attempted = "\n".join(lines) or "- （无可用手段）"

    final = (
        "⚠️ 无法完成该任务。\n\n"
        f"理由：{reason}\n\n"
        "已尝试的手段：\n"
        f"{attempted}\n\n"
        "为避免在证据不足时生成一份看似完整的报告，本次不提供调研报告。"
        "建议：补充关键前提（数据源 / 权限 / 更明确的目标），或人工介入。"
    )
    return {
        "final_output": final,
        "feasibility": "infeasible",
        "trace_steps": [{
            "step": "finalize_abstention",
            "feasibility": "infeasible",
            "attempted_workers": order,
        }],
    }


# ─── P0-2: 反编造 facts 校验 ───
# 报告中的数字必须能对回 state 里的 tool 产出/中间事实。对不上的数字标「未核实」；
# 保守判据（round 触顶 / 无数字 / 无证据 / 解析失败 → 一律跳过），绝不误判。

_CITE_RE = re.compile(r"\[[^\]]*\]")      # [来源 12] / [15] / [url]
_URL_RE = re.compile(r"https?://\S+")
# v1 旧正则：只认裸数字，保留给 verify_report_facts（既有通过路径不动）。
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

# ─── ENG-34: 中文单位归一（供 v2 核验 / claim_verifier adapter 共用） ───
# 单位组把「万亿」放最前（正则交替按顺序匹配，先长后短），
# 否则 1.2万亿 会被先吃成 1.2万，漏了「亿」。
_NUM_UNIT_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(万亿|万|亿|千|w|k|m)?", re.IGNORECASE)
_UNIT_MULTIPLIER = {
    "千": 1e3, "k": 1e3,
    "万": 1e4, "w": 1e4,
    "亿": 1e8,
    "万亿": 1e12,
}


def _normalize_number_tokens(text: str) -> set:
    """报告与 provenance snippet 共用的同一口径数字提取。

    返回 float 集合；容差 round(..., 2) 对齐 6489.61万↔64896100 这类浮点漂移。
    不做全量中文数词（「一千二百」→1200），留后续接口。
    """
    if not text:
        return set()
    cleaned = _URL_RE.sub(" ", text)       # 剔除 URL
    cleaned = _CITE_RE.sub(" ", cleaned)   # 剔除 [N]/[来源 N]
    # 年份是时间限定不是数据：「2024 年营收 5 亿」不应因来源是 2023 年报就报「2024 未找到依据」
    cleaned = _YEAR_RE.sub(" ", cleaned)
    out: set = set()
    for m in _NUM_UNIT_RE.finditer(cleaned):
        raw = m.group(1).replace(",", "")
        unit = (m.group(2) or "").lower()
        try:
            val = float(raw) * _UNIT_MULTIPLIER.get(unit, 1.0)
        except ValueError:
            continue
        if val == int(val) and 0 <= val <= 9:
            continue  # 单位数不显著，对齐 _extract_checkable_numbers 的保守判据
        out.add(round(val, 2))
    return out


def _normalize_sources_numbers(sources: list) -> list:
    """喂 RuleBasedJudge 前的 notation 对齐 adapter —— 只追加对照串、不改原句。

    claim_verifier._numbers()（只抽裸数字）遇到报告写「987 亿」、来源写
    「98700000000」会误判数字锚点不支撑。本 adapter 在来源正文末尾追加
    「裸数字 + 中文单位简写」对照行，使两边裸数字串同口径；词面覆盖分只增不减，
    不会把无源判成有源（覆盖率阈值仍由 RuleBasedJudge 把关）。
    """
    out = []
    for src in (sources or []):
        text = (src.get("text") or src.get("content")
                or src.get("body") or src.get("snippet") or "")
        extras = []
        for n in sorted(_normalize_number_tokens(text)):
            extras.append(_fmt_num(n))
            if n >= 1e12:
                extras.append(f"{n / 1e12:g} 万亿")
            elif n >= 1e8:
                extras.append(f"{n / 1e8:g} 亿")
            elif n >= 1e4:
                extras.append(f"{n / 1e4:g} 万")
        merged = f"{text}\n[numbers] {' '.join(extras)}" if extras else text
        out.append({**src, "text": merged})
    return out


def _extract_checkable_numbers(text: str) -> list[float]:
    """提取「可核对」的显著数字（多位整数 / 小数），用于反编造校验。

    保守剔除会误伤的内容：URL 与引用标记 [来源 N]/[N] 里的数字不是事实断言；
    0-9 的单位数在正文里太常见，同样跳过。
    """
    if not text:
        return []
    cleaned = _URL_RE.sub(" ", text)
    cleaned = _CITE_RE.sub(" ", cleaned)
    nums: list[float] = []
    for m in _NUM_RE.finditer(cleaned):
        raw = m.group(0).replace(",", "")
        try:
            val = float(raw)
        except ValueError:
            continue
        if val == int(val) and 0 <= val <= 9:
            continue  # 单位数不显著，跳过
        nums.append(val)
    return nums


def _collect_report_evidence(state) -> str:
    """汇总 state 里的 tool 产出/中间事实，作为事实核对基准。"""
    parts: list[str] = []
    for r in (state.get("worker_results") or {}).values():
        if not isinstance(r, dict):
            continue
        parts.append(str(r.get("output", "")))
        data = r.get("data")
        if data is not None:
            try:
                parts.append(json.dumps(data, ensure_ascii=False, default=str))
            except Exception:
                parts.append(str(data))
        for e in (r.get("errors") or []):
            parts.append(str(e))
    for errs in (state.get("worker_errors") or {}).values():
        for e in (errs or []):
            parts.append(str(e))
    return "\n".join(p for p in parts if p)


def _fmt_num(v: float) -> str:
    return str(int(v)) if v == int(v) else str(v)


# ─── ENG-29: provenance（引用溯源：工具名 + URL + 抓取时间 + 摘要） ───
# 数据形状（workers.py 的 worker executor / run_worker）：
#   worker_results[wn]["data"] = [{"step": {"tool": ...},
#                                  "result": {"success": True, "data": [...] 或 JSON str},
#                                  "fetched_at": "2026-10-22T14:30:05+0800"}, ...]
# search/fetch → data 是 list[str]（"标题: 摘要 [url]"）；web_scrape → JSON str。
_YEAR_RE = re.compile(r"(?:19|20)\d{2}\s*年")   # 「2024 年」→ 时间限定，不作数据核验
_PROV_LINE = re.compile(
    # title 用 .+? 而不是 [^:]+? —— 真实网页标题大量含冒号（'销量榜: 2025 年度盘点'），
    # 原写法会直接失配，整条来源被丢弃，表现为「搜索明明有结果却零 provenance」。
    r"^(?P<title>.+?):\s*(?P<snippet>.*?)\s*\[(?P<url>https?://[^\]]+)\]\s*$"
)
_PROV_JSON_URL_RE = re.compile(r'"url"\s*:\s*"(?P<url>[^"]+)"')
_PROV_JSON_TITLE_RE = re.compile(r'"title"\s*:\s*"(?P<title>[^"]*)"')
_PROV_JSON_BODY_RE = re.compile(r'"body"\s*:\s*"(?P<body>.*)')


def _collect_provenance(state) -> list:
    """从 worker_results 的 step 列表解析带 URL 的一手来源记录。

    只认 success 的步骤；解析不出 URL 的（纯模型自述 output）不进 provenance ——
    这正是 v2 与 v1 的分界：自述不算证据，带 URL 的来源行才算。
    """
    out: list = []
    results = state.get("worker_results") if isinstance(state, dict) else None
    if not isinstance(results, dict):
        results = {}
    for wn, r in results.items():
        if not isinstance(r, dict):
            continue
        for step in (r.get("data") or []):
            if not isinstance(step, dict):
                continue
            tool = (step.get("step") or {}).get("tool", "")
            res = step.get("result") or {}
            if not res.get("success"):
                continue
            fetched_at = step.get("fetched_at", "")
            raw = res.get("data")
            # 分支1：search —— list[str]，每行 "标题: 摘要 [url]"
            if isinstance(raw, list):
                for line in raw:
                    m = _PROV_LINE.match(str(line).strip())
                    if not m:
                        continue
                    out.append({
                        "url": m.group("url"), "tool": tool,
                        "fetched_at": fetched_at, "engine": "",
                        "title": m.group("title").strip(),
                        "snippet": m.group("snippet").strip(),
                        "source_worker": wn, "status": "unknown",
                    })
            # 分支2：web_scrape —— JSON str {"title","body","url"}
            elif isinstance(raw, str):
                text = raw
                obj = None
                try:
                    obj = json.loads(text)
                except (ValueError, TypeError):
                    obj = None         # 可能被 truncate_output 截断成半截 JSON
                if isinstance(obj, dict) and obj.get("url"):
                    out.append({
                        "url": str(obj["url"]), "tool": tool,
                        "fetched_at": fetched_at, "engine": "",
                        "title": str(obj.get("title", "")),
                        "snippet": str(obj.get("body", ""))[:2000],
                        "source_worker": wn, "status": "unknown",
                    })
                    continue
                # 截断容错：用字段正则捞 url/title/body，不让长正文被截断就整条丢
                mu = _PROV_JSON_URL_RE.search(text)
                if not mu:
                    continue
                mt = _PROV_JSON_TITLE_RE.search(text)
                mb = _PROV_JSON_BODY_RE.search(text)
                out.append({
                    "url": mu.group("url"), "tool": tool,
                    "fetched_at": fetched_at, "engine": "",
                    "title": mt.group("title") if mt else "",
                    "snippet": (mb.group("body") if mb else "")[:2000],
                    "source_worker": wn, "status": "unknown",
                })
    return out


def verify_report_facts(report: str, state) -> dict:
    """反编造校验：报告的显著数字须能在 state 证据里找到依据。

    返回 ``{"report", "unverified", "checked", "skipped", "reason"}``。
    保守原则：round 触顶 / 报告无数字 / 证据为空 / 解析异常 → 跳过（不误判）。
    """
    result: dict = {"report": report, "unverified": [], "checked": False,
                    "skipped": False, "reason": ""}
    try:
        if int(state.get("round", 0) or 0) >= SUPERVISOR_MAX_ROUNDS:
            result["skipped"] = True
            result["reason"] = "round 触顶，跳过事实校验"
            return result
        report_nums = _extract_checkable_numbers(report)
        if not report_nums:
            result["skipped"] = True
            result["reason"] = "报告无可核对数字"
            return result
        evidence_nums = set(_extract_checkable_numbers(_collect_report_evidence(state)))
        if not evidence_nums:
            result["skipped"] = True
            result["reason"] = "无证据可核对"
            return result
        seen: set[float] = set()
        unverified: list[float] = []
        for n in report_nums:
            if n in evidence_nums or n in seen:
                continue
            seen.add(n)
            unverified.append(n)
        result["checked"] = True
        result["unverified"] = unverified
        return result
    except Exception as exc:  # 解析失败绝不误判
        result["skipped"] = True
        result["reason"] = f"校验异常，已跳过: {exc}"
        return result


# ─── ENG-33: skip 四种情况 → 用户可读文案（skipped 不再静默通过） ───
_SKIP_REASON_CN = {
    "round 触顶，跳过事实校验": "本轮达到最大轮次 {max_rounds}，事实校验未执行",
    "报告无可核对数字": "报告正文无可核对数字，未做数字核验",
    "无证据可核对": "未检索到可用来源正文（provenance 为空），无法比对",
}


def _skip_reason_cn(reason: str) -> str:
    """四种 skip reason → 用户可读文案；未知 reason（异常分支）兜底。"""
    tmpl = _SKIP_REASON_CN.get(reason)
    if tmpl:
        return tmpl.format(max_rounds=SUPERVISOR_MAX_ROUNDS)
    return f"校验流程异常，已跳过：{reason}"


def _skip_note(reason: str) -> str:
    """统一的「未核验」标注块（skipped 时报告末尾必带原因）。"""
    return (
        "\n\n---\n"
        f"⚠️ 未核验：未经数字核验（原因：{_skip_reason_cn(reason)}）。"
        "以下内容未经过事实校验，请谨慎采信。\n"
    )


def _apply_fact_check(report: str, state) -> tuple[str, dict]:
    """跑 facts 校验；对不上的数字在报告末尾标「未核实」；skipped 也显式标「未核验」。"""
    info = verify_report_facts(report, state)
    if info["checked"] and info["unverified"]:
        nums = "、".join(_fmt_num(n) for n in info["unverified"][:12])
        report = report + (
            "\n\n---\n"
            "⚠️ 未核实数据：以下数字未能在 Worker 产出/中间事实中找到依据，"
            f"请谨慎采信 —— {nums}\n"
        )
    elif info["skipped"]:
        report = report + _skip_note(info.get("reason", ""))
    return report, info


# ─── ENG-32: v2 —— 证据基准换成带 URL 的一手来源 snippet ───
# 目标场景（如金融研报类尽调，仅作可证伪的演示场景）：报告数字不再拿 worker
# 自述 output 当证据，必须在 >=1 条 provenance snippet 里核对得上，源头错误当场可抓。

def verify_report_facts_v2(report: str, state) -> dict:
    """v2：报告数字必须在 provenance 一手来源 snippet 里核对得上。

    返回 {"checked", "skipped", "reason", "not_found", "provenance_count", "backing"}；
    保守判据同 v1（round 触顶/无数字/provenance 为空/异常 → skip 不误判）。
    """
    result: dict = {"checked": False, "skipped": True, "reason": "",
                    "not_found": [], "provenance_count": 0, "backing": {}}
    try:
        if int(state.get("round", 0) or 0) >= SUPERVISOR_MAX_ROUNDS:
            result["reason"] = "round 触顶，跳过事实校验"
            return result
        # 顺序对齐 v1：先查报告有无数字，再看来源
        report_nums = sorted(_normalize_number_tokens(report))
        if not report_nums:
            result["reason"] = "报告无可核对数字"
            return result
        provenance = _collect_provenance(state)
        result["provenance_count"] = len(provenance)
        if not provenance:
            result["reason"] = "无证据可核对"
            return result
        evidence_nums: set = set()
        backing: dict = {}
        for p in provenance:
            # 来源标题也是来源正文的一部分（「2023 年报」这类数字不该误报）
            src_text = f"{p.get('title', '')} {p.get('snippet', '')}"
            for n in _normalize_number_tokens(src_text):
                evidence_nums.add(n)
                backing.setdefault(n, p.get("url", ""))
        not_found = [{"num": n, "url": backing.get(n, "")}
                     for n in report_nums if n not in evidence_nums]
        result.update(checked=True, skipped=False, reason="",
                      not_found=not_found, backing=backing)
        return result
    except Exception as exc:  # 解析失败绝不误判
        result["reason"] = f"校验异常，已跳过: {exc}"
        return result


def _apply_fact_check_v2(report: str, state) -> tuple[str, dict]:
    """v2 渲染：❌ 未找到依据 / ⚠️ 未核验（原因）两态标注；全对则原文不动。"""
    info = verify_report_facts_v2(report, state)
    if info["checked"] and info["not_found"]:
        items = "、".join(
            f"{_fmt_num(x['num'])}" + (f"（来源：{x['url']}）" if x.get("url") else "")
            for x in info["not_found"][:12]
        )
        report = report + (
            "\n\n---\n"
            "❌ 未找到依据：以下数字未能在任何一手来源正文里核对上，"
            f"请谨慎采信 —— {items}\n"
        )
    elif info["skipped"]:
        report = report + _skip_note(info.get("reason", ""))
    return report, info


# ─── P1-6: 显式完成契约 ───
# 痛点：agent 倾向「声称完成」，半成品被当完成交付。收尾产物必须带一个**显式完成契约**
# ——feature 列表 + 每项 `passes` 布尔；`supervisor_finalize` 输出必带「未完成项」显式列表
# （无未完成则明确声明「无」）。保守原则：**未经验证不得置 passes=true**。

def _worker_results_dict(state: SupervisorState) -> dict:
    """安全取出 worker_results：外部脏数据（str/list/None）一律当空 dict。

    supervisor 的输入可能来自反序列化/上游节点，不能假设类型正确——
    非 dict 时若直接迭代或 .get() 会在收尾链路抛异常，把"脏输入"升级成"整体失败"。
    """
    results = state.get("worker_results") if isinstance(state, dict) else None
    return results if isinstance(results, dict) else {}


def _feature_verified(state: SupervisorState, worker: str) -> bool:
    """保守判据：仅当有明确证据证明该 worker 交付了实质产物时才认为 feature 完成。

    未验证 / 失败 / 空输出 / 带错误 / 无进展熔断 / 部分步骤失败 → False。
    整体任务被判 infeasible 时全部 False（见 build_completion_contract）。
    """
    r = _worker_results_dict(state).get(worker)
    if not isinstance(r, dict):
        return False
    if not r.get("success"):
        return False
    out = str(r.get("output") or "").strip()
    if len(out) < 5:
        return False
    if r.get("errors"):
        return False
    if "无进展熔断" in out or "部分步骤失败" in out:
        return False
    return True


def build_completion_contract(state: SupervisorState) -> dict:
    """构建显式完成契约：``{features, incomplete, complete}``。

    features: ``[{"feature": str, "worker": str, "passes": bool}]``
      * 若 state 提供 ``features``（调用方声明的验收项），按 worker 关联核对；
      * 否则从 ``workers_assigned`` / ``worker_results`` 派生（每个 worker 一项）。
    每项 passes 由 :func:`_feature_verified` 保守判定；infeasible 时强制全 false。
    """
    force_incomplete = state.get("feasibility") == "infeasible" or bool(
        state.get("no_progress_tripped"))
    features_in = state.get("features")
    feats: list[dict] = []
    if features_in:
        for f in features_in:
            if isinstance(f, dict):
                name = str(f.get("feature") or f.get("name") or "").strip()
                worker = f.get("worker")
            else:
                name = str(f).strip()
                worker = None
            worker = str(worker or name)
            passes = (not force_incomplete) and _feature_verified(state, worker)
            feats.append({"feature": name or worker, "worker": worker, "passes": bool(passes)})
    else:
        order = list(state.get("workers_assigned") or [])
        for w in _worker_results_dict(state):
            if w not in order:
                order.append(w)
        tasks = state.get("worker_tasks") or {}
        for w in order:
            passes = (not force_incomplete) and _feature_verified(state, w)
            feats.append({"feature": str(tasks.get(w) or w), "worker": w, "passes": bool(passes)})
    incomplete = [f for f in feats if not f["passes"]]
    return {
        "features": feats,
        "incomplete": incomplete,
        "complete": bool(feats) and not incomplete,
    }


def render_completion_contract(contract: dict) -> str:
    """把完成契约渲染成收尾产物里的显式小节（必带「未完成项」列表）。"""
    lines = ["", "---", "## ✅ 完成契约"]
    feats = contract.get("features") or []
    if not feats:
        lines.append("- 未声明可验收的 feature（无完成契约）")
        lines.append("")
        lines.append("**未完成项：** 无 feature 可验收（未声明完成契约）")
        return "\n".join(lines)
    for f in feats:
        mark = "✅ 通过" if f.get("passes") else "❌ 未完成"
        lines.append(f"- [{mark}] {f.get('feature')}")
    lines.append("")
    incomplete = contract.get("incomplete") or []
    if incomplete:
        lines.append("**未完成项：**")
        for f in incomplete:
            lines.append(f"- ❌ {f.get('feature')}")
    else:
        lines.append("未完成项：无（全部 feature 均已验证通过）")
    return "\n".join(lines)


def _contract_trace(contract: dict) -> dict:
    return {
        "step": "finalize_completion_contract",
        "complete": contract.get("complete", False),
        "incomplete_count": len(contract.get("incomplete") or []),
        "incomplete": [f.get("feature") for f in (contract.get("incomplete") or [])][:20],
    }


def _render_attribution_section(report) -> str:
    """LWP17: 渲染「失败归因」小节（仅在存在决定性根因时由调用方调用）。"""
    decisive = report.decisive
    if decisive is None:
        return ""
    agent, action = decisive
    lines = [
        "", "---", "", "## 🔍 失败归因", "",
        f"- **决定性步骤**: `{agent}` :: `{action}`",
        f"- **置信度**: {report.confidence:.2f}",
    ]
    if report.explanation:
        lines.append(f"- **症状链**: {report.explanation}")
    return "\n".join(lines)


def supervisor_finalize(state: SupervisorState) -> dict:
    """Synthesize all worker results into a final response."""
    print("\n[Supervisor] 生成最终回复...")

    # P1-6: 收尾必带显式完成契约（feature + passes + 未完成项列表）
    contract = build_completion_contract(state)
    contract_text = render_completion_contract(contract)
    contract_trace = _contract_trace(contract)

    worker_results = _worker_results_dict(state)

    # ─── LWP17: 失败归因（仅当完成契约未完成；纯确定性、零 LLM 调用）───
    attribution_trace: dict | None = None
    attribution_section = ""
    if not contract.get("complete") and state.get("feasibility") != "infeasible":
        try:
            from ..failure_attribution import attribute_from_run_result
            _attr = attribute_from_run_result(state)
            attribution_trace = {"step": "failure_attribution", **_attr.to_dict()}
            if _attr.decisive is not None:
                attribution_section = _render_attribution_section(_attr)
        except Exception as _e:  # 保守原则：归因失败绝不中断收尾
            print(f"[Supervisor] 失败归因跳过（异常）: {_e}", file=sys.stderr)
            attribution_trace = None
            attribution_section = ""

    # ─── WP16: infeasible → declare inability, do NOT call the report LLM ───
    if state.get("feasibility") == "infeasible":
        print("[Supervisor] 任务不可行，输出「无法完成」声明", file=sys.stderr)
        out = _build_abstention_output(state)
        out["final_output"] = (out.get("final_output", "") or "") + "\n" + contract_text
        out["completion_contract"] = contract
        out.setdefault("trace_steps", []).append(contract_trace)
        return out

    # ─── dsh P0: 空决策短路 — 无结果不调 LLM 生成报告 ───
    if not worker_results:
        print("[Supervisor] 无 Worker 结果，跳过报告生成", file=sys.stderr)
        return {
            "final_output": (state.get("final_output", "⚠️ 无可用结果，未生成报告。")
                             + "\n" + contract_text),
            "completion_contract": contract,
            "trace_steps": [{"step": "finalize_shortcircuit", "no_results": True}, contract_trace],
        }

    combined = "\n\n".join(
        "### {}\n{}".format(w, r.get('output', '无结果')[:2000])
        for w, r in worker_results.items()
    )

    # Use LLM to craft final response
    from ..agent_log import log_event
    from ..agents.supervisor import _call_llm_full

    # Log finalize start
    log_event(state.get("session_id", "unknown"), "finalize", {
        "request": state.get("request", "")[:100],
        "worker_count": len(worker_results),
    })
    system = (
        "你是专业的调研报告撰写助手。根据所有 Worker 的搜索结果，撰写一份完整、规范的调研报告。\n\n"
        "报告结构（严格遵守）:\n"
        "---\n"
        "## 📋 执行摘要\n"
        "用 2-3 句话概括核心发现和结论。面向忙碌的读者，看完这段就知道要不要继续读。\n\n"
        "## 🔍 主要发现\n"
        "分条目列出关键发现，每条包含:\n"
        "  - **发现内容**: 具体描述，引用的数据必须精确（数字、百分比、日期）\n"
        "  - **置信度**: 在每条的末尾标注 [高置信度] / [中置信度] / [低置信度]\n"
        "    - 高置信度: 多个可靠来源交叉验证的数据\n"
        "    - 中置信度: 单一来源但可信的信息\n"
        "    - 低置信度: 推测、未经验证的说法\n"
        "  - **来源**: 在引用处标注 [来源 N]\n\n"
        "## 📊 分析\n"
        "基于发现的深入分析，含对比、趋势、因果关系。使用表格对比数据。\n\n"
        "## 💡 结论与建议\n"
        "总结性结论，以及基于调研的可操作建议。\n\n"
        "---\n"
        "格式规则:\n"
        "1. 所有数字必须精确（不用「大约」「可能」模糊表述，除非低置信度）\\n"
        "2. 每个发现必须有来源引用 [来源 N]\n"
        "3. 置信度标注紧跟发现内容（高/中/低置信度）\n"
        "4. 表格用于数据对比，列表用于发现枚举\n"
        "5. 末尾列出「📎 参考来源」小节，格式:\n"
        "   [来源 1] 标题 - URL (访问日期: YYYY-MM-DD)\n"
        "6. 总字数 1000-2000 字"
    )
    final, fin_reason = _call_llm_full(
        [{"role": "user", "content": f"用户请求: {state['request']}\n\nWorker 结果:\n{combined}"}],
        system_prompt=system,
        max_tokens=4096,
    )
    # ─── dsh P0 sticky: max_tokens 截断(length) ≠ 失败 — 保留已生成内容，避免浪费性重试 ───
    if fin_reason == "length" and final and len(final.strip()) >= 20:
        print("[Supervisor] max_tokens 触顶，保留截断回答", file=sys.stderr)
    elif not final or len(final.strip()) < 20:
        print("[Supervisor] LLM 返回空结果，尝试降级回复...", file=sys.stderr)
        final, _ = _call_llm_full(
            [{"role": "user", "content": f"用户请求: {state['request']}\n\n请根据以下信息直接回答:\n{combined[:3000]}"}],
            system_prompt="简洁、直接地回答用户的问题。用中文。",
            max_tokens=1024,
        )
    # Second fallback: just return worker results directly
    if not final or len(final.strip()) < 20:
        print("[Supervisor] LLM 再次返回空，直接输出 Worker 结果", file=sys.stderr)
        final = combined[:2000]
    if not final:
        final = "[无法生成回复] 搜索到了一些信息但未能整理成回复，请重试。"
        print("[Supervisor] 最终降级: 搜索链路异常", file=sys.stderr)

    # Build summary
    total_elapsed = sum(
        r.get("elapsed_s", 0)
        for r in worker_results.values()
    )

    # ─── P0-2: 反编造 facts 校验（不改内容结构，仅标注未核实数字；不调报告 LLM）───
    # ENG-32: 有 provenance（带 URL 的一手来源行）时走 v2 —— 证据基准从 worker
    # 自述 output 换成来源 snippet；无 provenance 时回落 v1，保持既有路径不破。
    if _collect_provenance(state):
        final, fact_info = _apply_fact_check_v2(final, state)
    else:
        final, fact_info = _apply_fact_check(final, state)

    # P1-6: 收尾附显式完成契约（feature + passes + 未完成项列表）
    final = final + "\n" + contract_text

    # LWP17: 仅当存在决定性根因时，追加「失败归因」小节
    if attribution_section:
        final = final + "\n" + attribution_section

    trace_steps: list[dict] = [{
        "step": "finalize",
        "total_elapsed": round(total_elapsed, 1),
        "rounds": state.get("round", 1),
    }]
    if fact_info.get("checked"):
        if "unverified" in fact_info:          # v1
            unverified = [_fmt_num(n) for n in fact_info["unverified"][:12]]
        else:                                  # v2：not_found 是 [{num,url}]
            unverified = [_fmt_num(x["num"]) for x in fact_info.get("not_found", [])[:12]]
        trace_steps.append({
            "step": "finalize_fact_check",
            "checked": True,
            "unverified_count": len(unverified),
            "unverified": unverified,
        })
    elif fact_info.get("skipped"):
        # ENG-33: skipped 不再静默 —— trace 里也留原因，报告正文有「未核验」标注
        trace_steps.append({
            "step": "finalize_fact_check",
            "checked": False,
            "skipped": True,
            "reason": fact_info.get("reason", ""),
        })
    trace_steps.append(contract_trace)
    if attribution_trace:
        trace_steps.append(attribution_trace)

    return {
        "final_output": final,
        "completion_contract": contract,
        "trace_steps": trace_steps,
        # ENG-29 收口：结构化核验结果与一手来源记录随 finalize 一起返回。
        # 此前只把「未核验条数」塞进 trace_steps，原始 provenance 与三态被丢弃，
        # 导致调用方（API/报告生成）拿不到证据链，HTML 溯源汇总块永远是空的。
        "factcheck": fact_info,
        "provenance": _collect_provenance(state),
    }


# ─── Build multi-agent graph ───

def build_multi_agent() -> StateGraph:
    """Build the Supervisor-Worker multi-agent graph.

    Returns a compiled LangGraph graph.
    """
    graph = StateGraph(SupervisorState)

    # Add nodes
    graph.add_node("analyze", supervisor_analyze)
    graph.add_node("dispatch", supervisor_dispatch)
    graph.add_node("collect", supervisor_collect)
    graph.add_node("replan", supervisor_replan)
    graph.add_node("finalize", supervisor_finalize)

    # Set entry point
    graph.set_entry_point("analyze")

    # Edges
    graph.add_edge("analyze", "dispatch")
    graph.add_edge("dispatch", "collect")
    graph.add_conditional_edges("collect", supervisor_route, {
        "replan": "replan",
        "finalize": "finalize",
    })
    graph.add_edge("replan", "dispatch")
    graph.add_edge("finalize", END)

    return graph.compile()


# ─── Run entry point ───

def run_multi_agent(
    request: str,
    goal: str = "",
    trace_id: str = "",
    enable_tracing: bool = False,
    task_id: str = "",
) -> dict[str, Any]:
    """Run the multi-agent pipeline from a single request.

    Args:
        request: User's natural language request
        goal: Optional explicit goal description
        trace_id: Optional trace ID for logging
        enable_tracing: If True, collect detailed trace spans
        task_id: 取消作用域 ID（API 传 session_id）。空串 → 回落全局兜底事件（CLI）

    Returns:
        Dict with final_output, trace_steps, errors, elapsed, trace_tree (if enabled)
    """
    t0 = time.time()
    trace_id = trace_id or str(uuid.uuid4())

    # P1-3: 启用主回路无进展熔断（每次 run 重置 guard 状态，避免跨任务串计数）
    set_no_progress_detection(True, NO_PROGRESS_MAX_REPEATS)

    print(f"\n{'='*60}")
    print(f"[Multi-Agent] 任务: {request[:100]}...")
    print(f"{'='*60}")

    # Setup tracing
    collector = None
    if enable_tracing:
        collector = TraceCollector(trace_id)
        set_trace_collector(collector)

    graph = build_multi_agent()

    initial_state: SupervisorState = {
        "request": request,
        "goal": goal or request,
        "task_type": "",
        "round": 0,
        "workers_assigned": [],
        "worker_tasks": {},
        "worker_results": {},
        "worker_errors": {},
        "all_done": False,
        "no_progress_tripped": False,
        "no_progress_events": [],
        "feasibility": "",
        "feasibility_reason": "",
        "final_output": "",
        "errors": [],
        "trace_id": trace_id,
        "task_id": task_id,
        "trace_steps": [],
        "circuit_breaker": _new_circuit_breaker(),
        # 会话级 token 预算（WARN/ASK/STOP 三层软限制）；水位线锚在本 run 起飞点
        "token_budget": _new_token_budget(),
        "budget_level": "ok",
        "budget_events": [],
    }

    if collector:
        with collector.span("multi_agent_root", "supervisor", request=request[:100]):
            final = graph.invoke(initial_state, config={"recursion_limit": 100})
            # Check circuit breaker
            cb: CircuitBreaker = initial_state.get("circuit_breaker")  # type: ignore
            if cb and cb.check()["tripped"]:
                collector.mark_circuit_breaker("; ".join(cb.check()["reasons"]))
    else:
        final = graph.invoke(initial_state, config={"recursion_limit": 100})

    elapsed = time.time() - t0
    print(f"\n[Multi-Agent] 完成 ({elapsed:.1f}s, {final.get('round', 1)} 轮)")

    result: dict[str, Any] = {
        "final_output": final.get("final_output", ""),
        "trace_steps": final.get("trace_steps", []),
        "errors": final.get("errors", []),
        "worker_results": final.get("worker_results", {}),
        "rounds": final.get("round", 1),
        "elapsed_s": round(elapsed, 2),
        "trace_id": trace_id,
        "no_progress_tripped": final.get("no_progress_tripped", False),
        "completion_contract": final.get("completion_contract", {}),
        # 证据链（ENG-29 收口）：报告生成与 UI 需要原始 provenance + 三态核验结果
        "factcheck": final.get("factcheck", {}),
        "provenance": final.get("provenance", []),
        # 预算摘要：成本/级别/cache 命中率在一等公民字段里可见
        "budget": initial_state["token_budget"].summary(),
        "budget_level": final.get("budget_level", "ok"),
        "budget_events": final.get("budget_events", []),
    }

    if collector:
        trace_tree = collector.build()
        result["trace_tree"] = trace_tree
        print(f"\n{trace_tree.summary()}")

    return result
