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
import time
import uuid
from typing import Any, Literal

from langgraph.graph import END, StateGraph

from .agents import (
    run_worker,
    supervisor_analyze,
    supervisor_collect,
    supervisor_replan,
)
from .config import SUPERVISOR_MAX_ROUNDS

# ─── Cancel event mechanism ───
from .pipeline.cancel import (
    is_cancelled,
)
from .pipeline.circuit_breaker import CircuitBreaker
from .pipeline.state import SupervisorState, WorkerResult
from .pipeline.tracing import TraceCollector

# Module-level trace collector — set before graph invocation
_trace_collector: TraceCollector | None = None
# Module-level progress queue — set before graph invocation for SSE streaming
_progress_queue = None


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

def supervisor_dispatch(state: SupervisorState) -> dict:
    """Fan-out tasks to workers, running them in parallel."""
    workers_assigned = state.get("workers_assigned", [])
    worker_tasks = state.get("worker_tasks", {})

    # Check cancellation before dispatching
    if is_cancelled():
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

    # Run all workers in parallel
    results: dict[str, WorkerResult] = {}
    errors: dict[str, list[str]] = {}

    def _run_with_trace(wname: str, task: str):
        """Run worker with optional trace span."""
        if _progress_queue:
            _progress_queue.put({
                "type": "progress",
                "content": f"▶️ {wname} 开始工作: {task[:60]}...\n"
            })
        if _trace_collector:
            with _trace_collector.span(f"worker:{wname}", "worker", task=task[:80]):
                result = run_worker(wname, task)
                _trace_collector.record_metadata("output_preview", str(result.get("output", ""))[:100])
                return result
        return run_worker(wname, task)

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(workers_assigned)) as pool:
        futures = {}
        for wname in workers_assigned:
            task = worker_tasks.get(wname, state["request"])
            print(f"  → {wname}: {task[:60]}...")
            futures[pool.submit(_run_with_trace, wname, task)] = wname

        for future in concurrent.futures.as_completed(futures):
            # Check cancellation
            if is_cancelled():
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

    # Collect trace steps
    all_traces = list(state.get("trace_steps", []))
    all_traces.append({
        "step": "supervisor_dispatch",
        "workers": [
            {"name": w, "success": results[w].get("success", False)}
            for w in workers_assigned
        ],
    })

    return {
        "worker_results": results,
        "worker_errors": errors,
        "trace_steps": all_traces,
    }


# ─── Router after collect ───

def supervisor_route(state: SupervisorState) -> Literal["replan", "finalize"]:
    """Decide whether to do another round or finalize."""
    if state.get("all_done"):
        return "finalize"
    if state.get("round", 0) >= SUPERVISOR_MAX_ROUNDS:
        return "finalize"
    return "replan"


# ─── Finalizer ───

def supervisor_finalize(state: SupervisorState) -> dict:
    """Synthesize all worker results into a final response."""
    print("\n[Supervisor] 生成最终回复...")

    worker_results = state.get("worker_results", {})
    combined = "\n\n".join(
        "### {}\n{}".format(w, r.get('output', '无结果')[:2000])
        for w, r in worker_results.items()
    )

    # Use LLM to craft final response
    from .agent_log import log_event
    from .agents.supervisor import _call_llm

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
    final = _call_llm(
        [{"role": "user", "content": f"用户请求: {state['request']}\n\nWorker 结果:\n{combined}"}],
        system_prompt=system,
        max_tokens=4096,
    )
    # Empty output handling: try once more with simpler prompt
    if not final or len(final.strip()) < 20:
        print("[Supervisor] LLM 返回空结果，尝试降级回复...")
        final = _call_llm(
            [{"role": "user", "content": f"用户请求: {state['request']}\n\n请根据以下信息直接回答:\n{combined[:3000]}"}],
            system_prompt="简洁、直接地回答用户的问题。用中文。",
            max_tokens=1024,
        )
    # Second fallback: just return worker results directly
    if not final or len(final.strip()) < 20:
        print("[Supervisor] LLM 再次返回空，直接输出 Worker 结果")
        final = combined[:2000]
    if not final:
        final = "[无法生成回复] 搜索到了一些信息但未能整理成回复，请重试。"
        print("[Supervisor] 最终降级: 搜索链路异常")

    # Build summary
    total_elapsed = sum(
        r.get("elapsed_s", 0)
        for r in worker_results.values()
    )

    return {
        "final_output": final,
        "trace_steps": [{
            "step": "finalize",
            "total_elapsed": round(total_elapsed, 1),
            "rounds": state.get("round", 1),
        }],
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
) -> dict[str, Any]:
    """Run the multi-agent pipeline from a single request.

    Args:
        request: User's natural language request
        goal: Optional explicit goal description
        trace_id: Optional trace ID for logging
        enable_tracing: If True, collect detailed trace spans

    Returns:
        Dict with final_output, trace_steps, errors, elapsed, trace_tree (if enabled)
    """
    t0 = time.time()
    trace_id = trace_id or str(uuid.uuid4())

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
        "final_output": "",
        "errors": [],
        "trace_id": trace_id,
        "trace_steps": [],
        "circuit_breaker": CircuitBreaker(),
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
    }

    if collector:
        trace_tree = collector.build()
        result["trace_tree"] = trace_tree
        print(f"\n{trace_tree.summary()}")

    return result
