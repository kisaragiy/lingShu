"""Worker Agents — specialized agents for search, analyze, and execute tasks.

Each worker runs as a LangGraph subgraph with its own tool set.
Workers are designed to be called in parallel by the supervisor.
"""

import json
import time
from typing import Literal, Any
from langgraph.graph import StateGraph, END

from ..config import LLAMA_API, MODEL_LLAMA
from ..pipeline.state import WorkerState, WorkerResult
from ..tools.registry import TOOL_REGISTRY, call_tool, validate_result
from ..agents.supervisor import WORKER_CAPABILITIES


# ─── LLM call for workers ───

def _call_llm(messages: list[dict], system_prompt: str = "",
              max_tokens: int = 4096) -> str:
    import requests as req_lib
    payload = {
        "model": MODEL_LLAMA,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "stream": False,
        "thinking": {"type": "disabled"},
    }
    try:
        resp = req_lib.post(LLAMA_API, json=payload, timeout=300)
        if resp.status_code == 200:
            msg = resp.json()["choices"][0]["message"]
            content = msg.get("content", "") or msg.get("reasoning_content", "")[-500:] or ""
            return content
        return ""
    except Exception:
        return ""


def _extract_json_array(text: str) -> list[dict]:
    """Extract a JSON array from LLM output."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:]) if len(lines) > 1 else text
        if text.endswith("```"):
            text = text[:-3]
    text = text.strip().strip("`").replace("json\n", "").strip()
    try:
        result = json.loads(text)
        return result if isinstance(result, list) else [result]
    except (json.JSONDecodeError, ValueError):
        return []


# ─── Worker planner node ───

def _worker_planner(state: WorkerState) -> dict:
    """Plan how to accomplish the assigned sub-task using available tools."""
    worker_name = state["worker_name"]
    task = state["task"]
    tools = state.get("available_tools", [])

    # Build tool list for this worker
    tool_lines = []
    for tool_name in tools:
        if tool_name in TOOL_REGISTRY:
            entry = TOOL_REGISTRY[tool_name]
            desc = entry["schema"].get("description", "")
            brief = desc.split("。")[0].split(".")[0][:60]
            params = list(entry["schema"].get("properties", {}).keys())
            param_str = f"({', '.join(params[:4])})" if params else "()"
            tool_lines.append(f"  - {tool_name}{param_str}: {brief}")

    tools_text = "\n".join(tool_lines) if tool_lines else "  (无可用工具)"

    system = (
        f"你是 {worker_name} Worker。只做一件事：完成分配给你的子任务。\n\n"
        f"可用工具:\n{tools_text}\n\n"
        "输出一个 JSON 数组，每个元素: {name, tool, args}\n"
        "输出示例: [{\"name\":\"搜索\",\"tool\":\"search\",\"args\":{\"query\":\"...\"}}]\n"
        "只输出 JSON 数组，不要其他文字。"
    )

    result = _call_llm(
        [{"role": "user", "content": task}],
        system_prompt=system,
    )
    plan = _extract_json_array(result)
    if not plan:
        plan = [{"name": "处理", "tool": "think", "args": {"prompt": task}}]

    return {
        "plan": plan,
        "current_step": 0,
        "results": [],
        "errors": [],
        "retry_count": 0,
    }


# ─── Worker executor node ───

def _worker_executor(state: WorkerState) -> dict:
    """Execute one step of the worker's plan."""
    step_idx = state.get("current_step", 0)
    plan = state.get("plan", [])

    if step_idx >= len(plan):
        return {}

    step = plan[step_idx]
    tool_name = step.get("tool", "think")
    args = step.get("args", {})

    t0 = time.time()
    try:
        result = call_tool(tool_name, **args)
    except Exception as e:
        result = {"success": False, "error": str(e), "data": None}

    elapsed = time.time() - t0
    validation = validate_result(tool_name, result)

    new_results = list(state.get("results", []))
    new_results.append({
        "step": step,
        "result": result,
        "validation": validation,
        "elapsed": round(elapsed, 2),
    })

    trace = list(state.get("trace_steps", []))
    trace.append({
        "step": step.get("name", tool_name),
        "tool": tool_name,
        "elapsed": round(elapsed, 2),
        "passed": validation["passed"],
    })

    return {"results": new_results, "trace_steps": trace}


# ─── Worker router ───

def _worker_router(state: WorkerState) -> Literal["advance", "planner", "synthesize"]:
    step_idx = state.get("current_step", 0)
    plan = state.get("plan", [])

    if step_idx >= len(plan):
        return "synthesize"

    results = state.get("results", [])
    if results:
        last = results[-1]
        if not last["validation"]["passed"]:
            # Skip failed step, keep moving forward — don't loop
            return "advance"

    return "advance"


# ─── Worker advance node ───

def _worker_advance(state: WorkerState) -> dict:
    return {"current_step": state.get("current_step", 0) + 1, "retry_count": 0}


# ─── Worker synthesizer node ───

def _worker_synthesize(state: WorkerState) -> dict:
    """Synthesize results into a concise response for the supervisor."""
    results = state.get("results", [])
    successful = [r for r in results if r["validation"]["passed"]]

    evidence = "\n".join(
        str(r["result"].get("data", ""))[:300]
        for r in successful
    )

    system = (
        f"你是 {state['worker_name']} Worker。你的子任务已完成。\n"
        "根据执行结果，给主管一个简洁的回复（2-5句话），包含关键发现和数据。\n"
        "用中文回复。"
    )

    output = _call_llm(
        [{"role": "user", "content": f"子任务: {state['task']}\n执行结果: {evidence}"}],
        system_prompt=system,
    )
    if not output:
        output = evidence[:500] or "任务完成"

    return {"final_output": output}


# ─── Build worker subgraph ───

def build_worker(worker_name: str) -> StateGraph:
    """Build a worker subgraph for a specific worker type.

    Returns a compiled LangGraph subgraph.
    """
    graph = StateGraph(WorkerState)

    graph.add_node("planner", _worker_planner)
    graph.add_node("executor", _worker_executor)
    graph.add_node("advance", _worker_advance)
    graph.add_node("synthesize", _worker_synthesize)

    graph.set_entry_point("planner")
    graph.add_edge("planner", "executor")
    graph.add_conditional_edges("executor", _worker_router, {
        "advance": "advance",
        "planner": "planner",
        "synthesize": "synthesize",
    })
    graph.add_edge("advance", "executor")
    graph.add_edge("planner", "executor")
    graph.add_edge("synthesize", END)

    return graph.compile()


# ─── Run a single worker ───

def run_worker(worker_name: str, task: str) -> WorkerResult:
    """Run a worker agent synchronously and return its result.

    Args:
        worker_name: One of "search", "analyze", "execute"
        task: The specific sub-task description

    Returns:
        WorkerResult dict with output, traces, errors
    """
    t0 = time.time()
    tools = WORKER_CAPABILITIES.get(worker_name, {}).get("tools", [])

    try:
        worker = build_worker(worker_name)
        initial_state: WorkerState = {
            "worker_name": worker_name,
            "task": task,
            "available_tools": tools,
            "plan": [],
            "current_step": 0,
            "results": [],
            "errors": [],
            "retry_count": 0,
            "final_output": "",
            "trace_steps": [],
        }
        final = worker.invoke(initial_state, config={"recursion_limit": 50})

        return WorkerResult(
            worker_name=worker_name,
            success=True,
            output=final.get("final_output", ""),
            data=final.get("results", []),
            trace_steps=final.get("trace_steps", []),
            errors=final.get("errors", []),
            elapsed_s=round(time.time() - t0, 2),
        )
    except Exception as e:
        return WorkerResult(
            worker_name=worker_name,
            success=False,
            output="",
            data=None,
            trace_steps=[],
            errors=[str(e)],
            elapsed_s=round(time.time() - t0, 2),
        )
