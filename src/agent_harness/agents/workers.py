"""Worker Agents — specialized agents for search, analyze, and execute tasks.

Each worker runs as a LangGraph subgraph with its own tool set.
Workers are designed to be called in parallel by the supervisor.
"""

import json
import re
import time
from typing import Literal, Any
from langgraph.graph import StateGraph, END

from ..config import LLAMA_API, MODEL_LLAMA
from ..pipeline.state import WorkerState, WorkerResult
from ..tools.registry import TOOL_REGISTRY, call_tool, validate_result
from ..agents.supervisor import WORKER_CAPABILITIES


# ─── Token optimization utilities ───

def truncate_output(output: str, max_chars: int = 500, max_lines: int = 20) -> str:
    """Truncate tool output to save tokens. Keeps head + tail."""
    if not output or len(output) <= max_chars:
        return output or ""
    lines = output.split("\n")
    if len(lines) > max_lines:
        half = max_lines // 2
        skipped = len(lines) - max_lines
        return "\n".join(lines[:half]) + f"\n...（省略 {skipped} 行）...\n" + "\n".join(lines[-half:])
    return output[:max_chars] + f"\n...（超出 {len(output)-max_chars} 字符）"


def _is_simple_task(task: str) -> bool:
    """Detect if a task is simple enough for a cheap local model."""
    t = task.lower().strip()
    # Short queries
    if len(t) < 20:
        return True
    # Patterns that don't need strong reasoning
    SIMPLE_PATTERNS = [
        r'^(ls|cd|pwd|echo|cat|head|tail|pip|npm|git)\s',
        r'^(计算|算一下|求值)\s*\d',
        r'^(翻译|转换成|格式化)\s',
        r'^(你好|hi|hello|ping)$',
        r'^\d+\s*[+\-*/]\s*\d+',
        r'(现在几点|今天日期|星期几|天气|温度)',
    ]
    for pat in SIMPLE_PATTERNS:
        if re.search(pat, t):
            return True
    # Pure chat without tool requirements
    if len(t) < 50 and not any(kw in t for kw in
        ['搜索', '查找', '分析', '代码', 'python', '文件', '执行', '运行']):
        return True
    return False


# ─── LLM call for workers ───

# Local model endpoint for simple tasks (token optimization strategy #4)
LOCAL_LLM_API = "http://127.0.0.1:8081/v1/chat/completions"
LOCAL_LLM_MODEL = "qwen3:14b"


def _call_llm(messages: list[dict], system_prompt: str = "",
              max_tokens: int = 4096, task_hint: str = "") -> str:
    """
    Call LLM with optional simple-task routing to save tokens.

    Strategy #4: If task_hint indicates a simple query, route to
    the local Ollama model (free) instead of DeepSeek (paid).
    """
    import requests as req_lib

    # Check if this is a simple task that can use local model
    use_local = bool(task_hint and _is_simple_task(task_hint))

    api_url = LOCAL_LLM_API if use_local else LLAMA_API
    model = LOCAL_LLM_MODEL if use_local else MODEL_LLAMA

    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "max_tokens": max_tokens if not use_local else 512,
        "temperature": 0.3,
        "stream": False,
        "thinking": {"type": "disabled"},
    }
    try:
        resp = req_lib.post(api_url, json=payload, timeout=300 if not use_local else 30)
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
        f"你是 {worker_name} Worker。使用可用工具完成子任务。\n\n"
        f"可用工具:\n{tools_text}\n\n"
        "规则:\n"
        "1. 数学计算 → 用 code_execute（别心算）\n"
        "2. 搜索信息 → 输出多个搜索 query，从不同角度搜索同一主题，"
        "不要只搜一次。例如搜品牌、搜品类、搜用户评价等\n"
        "3. 总结文本 → 用 summarize\n"
        "4. 纯知识问答/翻译/列举 → 用 think\n"
        "5. 能调用工具就别空想\n\n"
        "输出一个 JSON 数组，每个元素: {name, tool, args}\n"
        "搜索示例:\n"
        "[{\"name\":\"搜索华为\",\"tool\":\"search\",\"args\":{\"query\":\"华为手环10 价格 功能 2026\"}},"
        "{\"name\":\"搜索小米\",\"tool\":\"search\",\"args\":{\"query\":\"小米手环9 Pro 价格 评测 2026\"}},"
        "{\"name\":\"搜索荣耀\",\"tool\":\"search\",\"args\":{\"query\":\"荣耀手环9 价格 功能 评价\"}}]\n"
        "数学示例: [{\"name\":\"计算\",\"tool\":\"code_execute\",\"args\":{\"code\":\"print(2**10)\"}}]\n"
        "只输出 JSON 数组，不要其他文字。"
    )

    result = _call_llm(
        [{"role": "user", "content": task}],
        system_prompt=system,
        task_hint=task,
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


# ─── Auto-routing helpers ───

_MATH_PATTERN = re.compile(
    r'(计算[式:：]?|^算[一下]?|求值|等于多少|加减乘除|'
    r'\d+\s*[+\-*/]\s*\d+)',
    re.IGNORECASE,
)


def _is_math_task(task: str) -> bool:
    """Detect if a task involves math calculation."""
    return bool(_MATH_PATTERN.search(task))


def _extract_for_code(task: str) -> str:
    """Extract a Python expression from a math task, stripping surrounding text."""
    # Remove common Chinese prefixes
    for prefix in ['计算', '算一下', '求值', '等于多少', '的结果', '告诉我', '用Python', '用 python']:
        task = task.replace(prefix, '')
    task = task.strip().strip('，。,:：；;')
    # Find the math expression: from a digit or paren, ending at digit or paren
    m = re.search(r'[\d(][\d+\-*/().%\s]*[\d)]', task)
    if m:
        expr = m.group().strip()
        # Remove trailing Chinese chars
        expr = re.sub(r'[^\d+\-*/().%\s]+$', '', expr).strip()
        # Fix unmatched parentheses
        if expr.count('(') > expr.count(')'):
            expr += ')' * (expr.count('(') - expr.count(')'))
        return f'print({expr})'
    # Fallback: wrap whole task as string
    return f'print("{task}")'


def _is_search_task(task: str) -> bool:
    """Detect if a task involves searching."""
    search_keywords = ['搜索', '查', '查找', '找', '搜', '查询', 'search', 'find', 'look up']
    return any(kw in task.lower() for kw in search_keywords)


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
    worker_name = state.get("worker_name", "")

    # Auto-route: force tool usage based on task type
    task = state.get("task", "")
    if tool_name == "think" and _is_math_task(task) and "code_execute" in state.get("available_tools", []):
        tool_name = "code_execute"
        code = _extract_for_code(task)
        args = {"code": code}
        print(f"  [Auto-route] {worker_name}: think→code_execute ({code})")

    if tool_name == "think" and _is_search_task(task) and "search" in state.get("available_tools", []):
        tool_name = "search"
        args = {"query": task}
        print(f"  [Auto-route] {worker_name}: think→search ({task[:40]})")

    t0 = time.time()
    try:
        result = call_tool(tool_name, **args)
    except Exception as e:
        result = {"success": False, "error": str(e), "data": None}

    # If auto-routed tool failed, fall back to think
    if not result.get("success") and tool_name != "think":
        print(f"  [Auto-route] {worker_name}: {tool_name} failed, fallback to think")
        tool_name = "think"
        args = {"prompt": task}
        try:
            result = call_tool(tool_name, **args)
        except Exception as e2:
            result = {"success": False, "error": str(e2), "data": None}

    elapsed = time.time() - t0
    validation = validate_result(tool_name, result)

    # Push tool progress event
    try:
        from ..graph_multi import _progress_queue
        if _progress_queue:
            status_icon = "✅" if result.get("success") else "❌"
            _progress_queue.put({
                "type": "progress",
                "content": f"  {status_icon} [{state.get('worker_name','?')}] {tool_name} ({elapsed:.1f}s)\n"
            })
    except Exception:
        pass

    new_results = list(state.get("results", []))
    # Truncate tool output to save tokens (strategy #3)
    truncated_result = dict(result)
    if isinstance(truncated_result.get("data"), str):
        truncated_result["data"] = truncate_output(truncated_result["data"])
    elif isinstance(truncated_result.get("output"), str):
        truncated_result["output"] = truncate_output(truncated_result["output"])
    new_results.append({
        "step": step,
        "result": truncated_result,
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
    # Fallback: if LLM returns empty, try direct answer
    if not output or len(output) < 10:
        direct = _call_llm(
            [{"role": "user", "content": state["task"]}],
            system_prompt=f"你是 {state['worker_name']} Worker。直接回答这个问题，给出具体内容。用中文。",
            max_tokens=1024,
        )
        output = direct or f"[{state['worker_name']}] 任务完成: {state['task'][:200]}"

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

def run_worker(worker_name: str, task: str, max_retries: int = 3) -> WorkerResult:
    """Run a worker agent synchronously and return its result.

    Auto-retries on failure with exponential backoff (2s, 4s, 8s).

    Args:
        worker_name: One of "search", "analyze", "execute"
        task: The specific sub-task description
        max_retries: Max retry attempts (0 = no retry)

    Returns:
        WorkerResult dict with output, traces, errors
    """
    t0 = time.time()
    tools = WORKER_CAPABILITIES.get(worker_name, {}).get("tools", [])

    last_error = ""
    for attempt in range(1 + max_retries):
        if attempt > 0:
            wait = 2 ** attempt  # 2s, 4s, 8s
            print(f"  [Retry] {worker_name} 第 {attempt}/{max_retries} 次重试 (等待 {wait}s)...")
            time.sleep(wait)

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

            result = WorkerResult(
                worker_name=worker_name,
                success=True,
                output=final.get("final_output", ""),
                data=final.get("results", []),
                trace_steps=final.get("trace_steps", []),
                errors=final.get("errors", []),
                elapsed_s=round(time.time() - t0, 2),
            )

            # Check if result is meaningful — empty output counts as failure
            output = result.get("output", "").strip()
            if output and len(output) >= 5:
                return result

            last_error = f"输出为空 (attempt {attempt + 1})"
            print(f"  [Retry] {worker_name}: {last_error}")
            if attempt < max_retries:
                continue  # retry with backoff
            # After all retries exhausted, return as failed
            result["success"] = False
            result["errors"] = result.get("errors", []) + [last_error]
            return result

        except Exception as e:
            last_error = str(e)
            print(f"  [Retry] {worker_name} 异常: {e}")
            if attempt >= max_retries:
                return WorkerResult(
                    worker_name=worker_name,
                    success=False,
                    output="",
                    data=None,
                    trace_steps=[],
                    errors=[str(e)],
                    elapsed_s=round(time.time() - t0, 2),
                )

    # Fallback (shouldn't reach here)
    return WorkerResult(
        worker_name=worker_name,
        success=False,
        output="",
        data=None,
        trace_steps=[],
        errors=[last_error],
        elapsed_s=round(time.time() - t0, 2),
    )
