"""Worker Agents — specialized agents for search, analyze, and execute tasks.

Each worker runs as a LangGraph subgraph with its own tool set.
Workers are designed to be called in parallel by the supervisor.
"""

import json
import re
import sys
import time
from typing import Literal

from langgraph.graph import END, StateGraph

from ..agents.supervisor import WORKER_CAPABILITIES
from ..pipeline.cancel import is_cancelled
from ..pipeline.llm import ledger_bump
from ..pipeline.state import WorkerResult, WorkerState
from ..tools.registry import TOOL_REGISTRY, call_tool, validate_result

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
    return bool(len(t) < 50 and not any(kw in t for kw in ['搜索', '查找', '分析', '代码', 'python', '文件', '执行', '运行']))


# ─── Fallback result (LLM unreachable) ───

def _fallback_worker_result(worker_type: str, query: str) -> str:
    """Return a template response without LLM when backend is unreachable."""
    fallbacks = {
        "search": (
            f"[搜索] 由于 LLM 后端不可用，搜索结果不可用。\n"
            f"请配置 HARNESS_DEEPSEEK_API 或 HARNESS_LLAMA_API。\n"
            f"原始查询: {query[:200]}"
        ),
        "analyze": (
            f"[分析] LLM 后端不可用，无法生成分析。\n"
            f"原始任务: {query[:200]}"
        ),
        "execute": (
            f"[执行] LLM 后端不可用，无法执行。\n"
            f"原始任务: {query[:200]}"
        ),
    }
    return fallbacks.get(worker_type,
                         f"[{worker_type}] LLM 后端不可用，无法完成任务。原始任务: {query[:200]}")


# ─── LLM call for workers ───

# 端点/模型必须走 get_llm_config()（运行时配置），不能用 config 模块的 import 快照——
# 快照是进程启动那一刻的 .env，用户在设置页改的 api_url/model 看不到，结果是同一进程里
# 一部分调用打真模型、一部分打 mock/旧端口（实测：一次调研里出现 mock + 127.0.0.1:18888
# + qwen3.5:9b 三种后端，报告混着真实与占位内容）。
# 简单任务只降预算、不切端点：切端点等于维护"两套配置来源"，正是配置分裂的成因。


def _call_llm(messages: list[dict], system_prompt: str = "",
              max_tokens: int = 4096, task_hint: str = "") -> str:
    """Call the configured LLM. Simple tasks get a smaller budget, not another endpoint."""
    import requests as req_lib

    from ..config import get_llm_config
    from ..pipeline.llm import (
        THINKING_MIN_BUDGET, _extract_openai_content, _thinking_mode,
    )

    use_local = bool(task_hint and _is_simple_task(task_hint))
    cfg = get_llm_config()
    api_url, model = cfg.get("api_url") or "", cfg.get("model") or ""
    if not api_url or not model:
        print("[Worker] LLM 未配置（api_url / model 为空），不发起调用", file=sys.stderr)
        return ""

    mode = _thinking_mode()
    base_budget = 512 if (use_local and mode != "on") else max_tokens
    budget = max(base_budget, THINKING_MIN_BUDGET) if mode == "on" else base_budget

    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}] + messages,
        "max_tokens": budget,
        "temperature": 0.3,
        "stream": False,
    }
    if mode != "on":
        payload["thinking"] = {"type": "disabled"}
    try:
        resp = req_lib.post(api_url, json=payload, timeout=300 if not use_local else 60)
        if resp.status_code != 200:
            print(f"[Worker] LLM 返回 HTTP {resp.status_code}，本步产出为空", file=sys.stderr)
            return ""
        data = resp.json()
        content, reasoning, finish, _cacheable = _extract_openai_content(data, budget)
        ledger_bump(data.get("usage", {}).get("total_tokens", 0))
        if not content:
            # 思考轨迹是过程不是结论：拿它冒充答案会让报告里出现"看起来像答案的推理"
            print(f"[Worker] 答案为空（finish={finish}"
                  f"{'，有思考轨迹' if reasoning else ''}），本步产出为空",
                  file=sys.stderr)
        return content
    except Exception as e:
        print(f"[Worker] LLM 调用异常: {e}", file=sys.stderr)
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
        "2. 搜索信息 → 从不同角度生成多个搜索 query，\n"
        "   - 搜品类: '产品名 价格 参数 2026'\n"
        "   - 搜评测: '产品名 评测 评价'\n"
        "   - 搜对比: '产品A vs 产品B 对比'\n"
        "   - 搜行业: '行业名 市场规模 趋势 2026'\n"
        "3. 总结文本 → 用 summarize\n"
        "4. 纯知识问答/翻译/列举 → 用 think\n"
        "5. 能调用工具就别空想\n\n"
        "输出一个 JSON 数组，每个元素: {name, tool, args}\n"
        "示例: [{\"name\":\"搜价格\",\"tool\":\"search\",\"args\":{\"query\":\"华为手环10 价格 配置 2026\"}},\n"
        "       {\"name\":\"搜评测\",\"tool\":\"search\",\"args\":{\"query\":\"华为手环10 评测 体验\"}},\n"
        "       {\"name\":\"搜对比\",\"tool\":\"search\",\"args\":{\"query\":\"华为手环10 小米手环9 对比\"}}]\n"
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
    # ─── dsh P0: phase-abort — 取消后立即终止，不执行任何工具 ───
    if is_cancelled(state.get("task_id", "")):
        print(f"  [Abort] {state.get('worker_name','?')} 已取消，跳过工具执行", file=sys.stderr)
        return {
            "errors": list(state.get("errors", [])) + ["任务已取消"],
            "aborted": True,
            "results": state.get("results", []),
        }

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
    # 结果有效性也进指标：call_tool 的 success 只代表「函数没抛异常」，
    # 搜索降级返回、空结果这类「调用成功但没用」必须单独可见。
    try:
        from ..metrics import record_tool_result
        record_tool_result(tool_name, validation.get("severity", "ok"))
    except Exception:
        pass

    # Push tool progress event
    try:
        from ..graph_multi import get_progress_queue
        _pq = get_progress_queue(state.get("task_id", ""))
        if _pq:
            status_icon = "✅" if result.get("success") else "❌"
            _pq.put({
                "type": "progress",
                "content": f"  {status_icon} [{state.get('worker_name','?')}] {tool_name} ({elapsed:.1f}s)\n"
            })
    except Exception:
        pass

    new_results = list(state.get("results", []))
    # Truncate tool output to save tokens (strategy #3)
    truncated_result = dict(result)
    # 只读抓取类工具的正文需要更长摘要（500 字会把年报类正文拦腰截断，
    # 导致 provenance snippet 里查不到报告数字）；其余工具维持原 500 字上限省 token。
    _cap = 2000 if tool_name in {"web_scrape", "fetch", "web_browse"} else 500
    if isinstance(truncated_result.get("data"), str):
        truncated_result["data"] = truncate_output(truncated_result["data"], max_chars=_cap)
    elif isinstance(truncated_result.get("output"), str):
        truncated_result["output"] = truncate_output(truncated_result["output"])
    new_results.append({
        "step": step,
        "result": truncated_result,
        "validation": validation,
        "elapsed": round(elapsed, 2),
        # ENG-29: provenance 需要「抓取时间」——此前只有 elapsed，无绝对时间戳
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
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

def _failed_step_summary(results: list) -> str:
    """Summarize validation-failed steps. Empty string when none failed.

    Arch review 2026-09-07: worker success must reflect plan completion —
    silently skipped failed steps previously let a worker report success
    while key work was missing.
    """
    if not results:
        return ""
    failed = [r for r in results if not r.get("validation", {}).get("passed", False)]
    if not failed:
        return ""
    names = []
    for r in failed[:3]:
        step = r.get("step")
        if isinstance(step, dict):
            names.append(str(step.get("name", step.get("tool", "?"))))
        else:
            names.append(str(step))
    label = ", ".join(names)
    if len(failed) > 3:
        label += " 等"
    return f"⚠️ 部分步骤失败 ({len(failed)}/{len(results)}): {label}"


def _worker_synthesize(state: WorkerState) -> dict:
    """Synthesize results into a concise response for the supervisor."""
    _t0 = time.time()
    # ─── dsh P0: phase-abort — 取消后不再调 LLM 合成 ───
    if is_cancelled(state.get("task_id", "")):
        return {
            "final_output": f"⛔ {state.get('worker_name', '?')} 任务已取消",
            "aborted": True,
        }
    results = state.get("results", [])
    successful = [r for r in results if r["validation"]["passed"]]

    evidence = "\n".join(
        str(r["result"].get("data", ""))[:300]
        for r in successful
    )
    failed_note = _failed_step_summary(results)

    user_msg = f"子任务: {state['task']}\n执行结果: {evidence}"
    if failed_note:
        user_msg += (
            f"\n\n{failed_note}\n"
            "你的汇报开头必须说明哪些步骤失败/被跳过，不得假装全部完成。"
        )

    system = (
        f"你是 {state['worker_name']} Worker。你的子任务已完成。\n"
        "根据执行结果，给主管一个简洁的回复（2-5句话），包含关键发现和数据。\n"
        "用中文回复。"
    )

    output = _call_llm(
        [{"role": "user", "content": user_msg}],
        system_prompt=system,
    )
    # Fallback: if LLM returns empty, try direct answer
    if not output or len(output) < 10:
        direct = _call_llm(
            [{"role": "user", "content": state["task"]}],
            system_prompt=f"你是 {state['worker_name']} Worker。直接回答这个问题，给出具体内容。用中文。",
            max_tokens=1024,
        )
        if direct and len(direct) >= 10:
            output = direct
        else:
            # LLM 全挂时**不要丢掉已经拿到的工具结果**：工具成功 ≠ 非要 LLM 才能交付。
            # 实测：search 已返回 5 条结果，却因 LLM 不可用输出"搜索结果不可用"（数据白丢、
            # 可用性无谓降低）。有 evidence 就直接交出去，让上层自己决定要不要润色。
            output = evidence.strip()[:2000] or _fallback_worker_result(
                state["worker_name"], state["task"])

    # Agent（Worker）调用指标 —— 每次 worker 跑完记一次。
    # 时长是 synthesize 段（LLM 总结/降级）的耗时；工具耗时另有
    # harness_tool_call_duration_seconds 单独记，两者不是同一层。
    try:
        from ..metrics import record_agent_call
        record_agent_call(state.get("worker_name", "unknown"), time.time() - _t0)
    except Exception:
        pass

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

def run_worker(worker_name: str, task: str, max_retries: int = 3, task_id: str = "") -> WorkerResult:
    """Run a worker agent synchronously and return its result.

    Auto-retries on failure with exponential backoff (2s, 4s, 8s).

    Args:
        worker_name: One of "search", "analyze", "execute"
        task: The specific sub-task description
        max_retries: Max retry attempts (0 = no retry)
        task_id: 取消作用域 ID（session_id）—— 只查这个任务的取消信号（ENG-21）

    Returns:
        WorkerResult dict with output, traces, errors
    """
    t0 = time.time()
    tools = WORKER_CAPABILITIES.get(worker_name, {}).get("tools", [])

    last_error = ""
    for attempt in range(1 + max_retries):
        # ─── dsh P0: phase-abort — 取消后不构建/运行 worker，直接返回取消结果 ───
        if is_cancelled(task_id):
            print(f"  [Abort] {worker_name} 已取消", file=sys.stderr)
            return WorkerResult(
                worker_name=worker_name,
                success=False,
                output="⛔ 任务已被取消",
                data=None,
                trace_steps=[],
                errors=["cancelled"],
                elapsed_s=round(time.time() - t0, 2),
            )

        if attempt > 0:
            wait = 2 ** attempt  # 2s, 4s, 8s
            print(f"  [Retry] {worker_name} 第 {attempt}/{max_retries} 次重试 (等待 {wait}s)...")
            time.sleep(wait)

        try:
            worker = build_worker(worker_name)
            initial_state: WorkerState = {
                "worker_name": worker_name,
                "task": task,
                "task_id": task_id,
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
            result["output"] = _fallback_worker_result(worker_name, task)
            result["errors"] = result.get("errors", []) + [last_error]
            return result

        except Exception as e:
            last_error = str(e)
            print(f"  [Retry] {worker_name} 异常: {e}")
            if attempt >= max_retries:
                return WorkerResult(
                    worker_name=worker_name,
                    success=False,
                    output=_fallback_worker_result(worker_name, task),
                    data=None,
                    trace_steps=[],
                    errors=[str(e)],
                    elapsed_s=round(time.time() - t0, 2),
                )

    # Fallback (shouldn't reach here)
    return WorkerResult(
        worker_name=worker_name,
        success=False,
        output=_fallback_worker_result(worker_name, task),
        data=None,
        trace_steps=[],
        errors=[last_error],
        elapsed_s=round(time.time() - t0, 2),
    )
