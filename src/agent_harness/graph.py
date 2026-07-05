"""Single-Agent Graph — 5-stage LangGraph pipeline.

The original harness, adapted for the new package structure.
For backward compatibility and simple tasks.
"""

import json
import time
from typing import Literal, Any
from langgraph.graph import StateGraph, END

from .config import LLAMA_API, MODEL_LLAMA, MAX_RETRIES
from .pipeline.state import HarnessState
from .pipeline.circuit_breaker import CircuitBreaker
from .tools.registry import TOOL_REGISTRY, call_tool, validate_result


# ─── LLM Call ───

def _call_llm(messages: list[dict], system_prompt: str = "",
              max_tokens: int = 2048, temperature: float = 0.3) -> str:
    import requests as req_lib
    payload = {
        "model": MODEL_LLAMA,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    try:
        resp = req_lib.post(LLAMA_API, json=payload, timeout=120)
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"]
        return ""
    except Exception:
        return ""


def _extract_json_array(text: str) -> list[dict]:
    text = text.strip().strip("`").replace("json\n", "").strip()
    try:
        result = json.loads(text)
        return result if isinstance(result, list) else [result]
    except (json.JSONDecodeError, ValueError):
        return []


# ─── Nodes ───

def planner_node(state: HarnessState) -> dict:
    """Plan task using LLM with available tools."""
    tool_lines = []
    for name, entry in sorted(TOOL_REGISTRY.items()):
        desc = entry["schema"].get("description", "")
        brief = desc.split("。")[0].split(".")[0][:60]
        params = list(entry["schema"].get("properties", {}).keys())
        param_str = f"({', '.join(params[:5])})" if params else "()"
        tool_lines.append(f"  - {name}{param_str}: {brief}")

    tools_text = "\n".join(tool_lines[:25])

    system = (
        "You are a task planner. Break the user's request into a step-by-step plan.\n"
        f"Available tools:\n{tools_text}\n\n"
        "Output ONLY a JSON array: [{\"name\":\"...\",\"tool\":\"...\",\"args\":{...}}]"
    )

    result = _call_llm(
        [{"role": "user", "content": state["request"]}],
        system_prompt=system,
    )
    plan = _extract_json_array(result)
    if not plan:
        plan = [{"name": "直接回复", "tool": "think", "args": {"prompt": state["request"]}}]

    return {
        "plan": plan,
        "current_step": 0,
        "results": [],
        "errors": [],
        "retry_count": 0,
        "trace_steps": [{"step": "planner", "plan": [s.get("name", "") for s in plan]}],
    }


def executor_node(state: HarnessState) -> dict:
    """Execute current plan step."""
    if state["current_step"] >= len(state["plan"]):
        return {}

    # Circuit breaker check
    cb: CircuitBreaker = state.get("_circuit_breaker")  # type: ignore
    if cb and cb.check()["tripped"]:
        return {"errors": state.get("errors", []) + ["[熔断] 已触发"]}

    step = state["plan"][state["current_step"]]
    tool_name = step.get("tool", "think")
    args = {k: v for k, v in step.get("args", {}).items() if k != "name"}

    t0 = time.time()
    try:
        result = call_tool(tool_name, **args)
    except Exception as e:
        result = {"success": False, "error": str(e), "data": None}

    elapsed = time.time() - t0
    validation = validate_result(tool_name, result)

    new_results = list(state["results"])
    new_results.append({"step": step, "result": result, "validation": validation})
    new_trace = list(state.get("trace_steps", []))
    new_trace.append({"step": step["name"], "tool": tool_name,
                      "elapsed": round(elapsed, 2), "passed": validation["passed"]})

    return {"results": new_results, "trace_steps": new_trace}


def router_node(state: HarnessState) -> Literal["advance", "corrector", "finalizer"]:
    if state["current_step"] >= len(state["plan"]):
        return "finalizer"
    if state.get("retry_count", 0) > MAX_RETRIES:
        return "advance"  # skip after max retries
    if state["results"]:
        last = state["results"][-1]
        if last["validation"]["passed"]:
            return "advance"
    return "corrector"


def advance_node(state: HarnessState) -> dict:
    return {"current_step": state["current_step"] + 1, "retry_count": 0}


def corrector_node(state: HarnessState) -> dict:
    if state.get("retry_count", 0) > MAX_RETRIES:
        return {"current_step": state["current_step"] + 1, "retry_count": 0}
    return {"retry_count": state.get("retry_count", 0) + 1}


def finalizer_node(state: HarnessState) -> dict:
    """Generate final reply."""
    evidence = "\n".join(
        str(r["result"].get("data", ""))[:300]
        for r in state["results"] if r["result"].get("success")
    )
    system = "根据以下执行结果给用户一个完整的回复。用中文。"
    reply = _call_llm(
        [{"role": "user", "content": f"请求: {state['request']}\n结果: {evidence}"}],
        system_prompt=system,
        max_tokens=2048,
    )
    return {"final_output": reply or "任务完成"}


# ─── Build ───

def build() -> StateGraph:
    graph = StateGraph(HarnessState)
    graph.add_node("planner", planner_node)
    graph.add_node("executor", executor_node)
    graph.add_node("advance", advance_node)
    graph.add_node("corrector", corrector_node)
    graph.add_node("finalizer", finalizer_node)

    graph.set_entry_point("planner")
    graph.add_edge("planner", "executor")
    graph.add_conditional_edges("executor", router_node, {
        "advance": "advance",
        "corrector": "corrector",
        "finalizer": "finalizer",
    })
    graph.add_edge("advance", "executor")
    graph.add_edge("corrector", "executor")
    graph.add_edge("finalizer", END)

    return graph.compile()


def run(request: str) -> str:
    """Simple single-agent run."""
    graph = build()
    state: HarnessState = {
        "request": request,
        "plan": [],
        "current_step": 0,
        "results": [],
        "errors": [],
        "retry_count": 0,
        "final_output": "",
        "conversation_history": [],
        "goal": request,
        "stop_conditions": [],
        "loop_state_path": "",
        "iteration_count": 0,
        "stop_conditions_met": False,
        "should_finalize": False,
        "enable_review": False,
        "review_passed": False,
        "review_feedback": "",
        "validator_fixes": [],
        "trace_id": "",
        "trace_steps": [],
    }
    final = graph.invoke(state)
    return final.get("final_output", "")
