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
from typing import Literal, Any
from langgraph.graph import StateGraph, END

from .config import SUPERVISOR_MAX_ROUNDS
from .pipeline.state import SupervisorState, WorkerResult
from .pipeline.circuit_breaker import CircuitBreaker
from .pipeline.tracing import TraceCollector
from .agents import (
    supervisor_analyze, supervisor_collect, supervisor_replan,
    run_worker, WORKER_CAPABILITIES,
)

# Module-level trace collector — set before graph invocation
_trace_collector: TraceCollector | None = None


def set_trace_collector(collector: TraceCollector):
    """Set the trace collector for the current execution."""
    global _trace_collector
    _trace_collector = collector


# ─── Dispatch: run workers in parallel ───

def supervisor_dispatch(state: SupervisorState) -> dict:
    """Fan-out tasks to workers, running them in parallel."""
    workers_assigned = state.get("workers_assigned", [])
    worker_tasks = state.get("worker_tasks", {})

    if not workers_assigned:
        return {"all_done": True}

    print(f"\n[Supervisor] 调度 {len(workers_assigned)} 个 Worker: {workers_assigned}")

    # Run all workers in parallel
    results: dict[str, WorkerResult] = {}
    errors: dict[str, list[str]] = {}

    def _run_with_trace(wname: str, task: str):
        """Run worker with optional trace span."""
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
            wname = futures[future]
            try:
                result = future.result(timeout=120)
                results[wname] = result
                status = "✅" if result.get("success") else "❌"
                print(f"  {status} {wname} ({result.get('elapsed_s', 0):.1f}s): "
                      f"{str(result.get('output', ''))[:80]}")
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
        f"### {w}\n{r.get('output', '无结果')[:500]}"
        for w, r in worker_results.items()
    )

    # Use LLM to craft final response
    from .agents.supervisor import _call_llm
    system = (
        "你是最终回复生成器。根据所有 Worker 的结果，给用户一个完整、准确的回复。\n"
        "引用具体数据，用中文。"
    )
    final = _call_llm(
        [{"role": "user", "content": f"用户请求: {state['request']}\n\nWorker 结果:\n{combined}"}],
        system_prompt=system,
        max_tokens=2048,
    )
    if not final:
        final = combined[:1000]

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
            final = graph.invoke(initial_state)
            # Check circuit breaker
            cb: CircuitBreaker = initial_state.get("circuit_breaker")  # type: ignore
            if cb and cb.check()["tripped"]:
                collector.mark_circuit_breaker("; ".join(cb.check()["reasons"]))
    else:
        final = graph.invoke(initial_state)

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
