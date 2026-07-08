"""Supervisor Agent — analyzes tasks and delegates to workers."""

import json
import time
from typing import Any

from ..config import (
    LLAMA_API, DEEPSEEK_API, CLOUD_API_KEY,
    MODEL_LLAMA, MODEL_DEEPSEEK,
    SUPERVISOR_MAX_ROUNDS,
)
from ..pipeline.state import SupervisorState, WorkerResult
from ..pipeline.cancel import is_cancelled

# ─── Worker capability definitions ───

WORKER_CAPABILITIES = {
    "search": {
        "description": "网页搜索、信息抓取、RAG语义检索、实时数据查询、知识问答",
        "tools": ["search", "fetch", "web_scrape", "web_browse", "rag_query", "datetime", "think"],
    },
    "analyze": {
        "description": "数据分析、报告生成、内容总结、代码执行、文本处理",
        "tools": ["think", "code_execute", "summarize", "file_read", "file_write"],
    },
    "execute": {
        "description": "桌面自动化、浏览器操作、ComfyUI图像生成、文件管理、应用启动",
        "tools": ["desktop_gui", "browser_automation", "app_launch", "comfyui_text2img",
                   "comfyui_img2img", "file_write", "chat_send"],
    },
}


# ─── LLM call helper ───

def _call_llm(messages: list[dict], system_prompt: str = "",
              max_tokens: int = 4096) -> str:
    """Call LLM for supervisor reasoning."""
    import requests as req_lib

    payload = {
        "model": MODEL_LLAMA,
        "messages": (
            [{"role": "system", "content": system_prompt}]
            + messages
        ),
        "max_tokens": max_tokens,
        "temperature": 0.3,
        "stream": False,
        "thinking": {"type": "disabled"},
    }
    try:
        resp = req_lib.post(LLAMA_API, json=payload, timeout=300)
        if resp.status_code == 200:
            data = resp.json()
            msg = data["choices"][0]["message"]
            content = msg.get("content", "") or msg.get("reasoning_content", "")[-500:] or ""
            return content
        return ""
    except Exception:
        return ""


# ─── Supervisor nodes ───

def supervisor_analyze(state: SupervisorState) -> dict:
    """Analyze request and determine which workers to assign."""
    request = state["request"]

    workers_desc = "\n".join(
        f"- {name}: {info['description']}"
        for name, info in WORKER_CAPABILITIES.items()
    )

    # Check if knowledge base is available
    kb_hint = ""
    try:
        from ..tools.rag_store import list_collections
        kb_cols = list_collections()
        if kb_cols:
            kb_names = ", ".join(kb_cols)
            kb_hint = (
                "\n\n📚 知识库可用! 已有 collections: %s\n"
                "如果用户询问与已上传文档相关的问题，搜索 worker 使用 rag_query 工具检索知识库。\n"
                "知识库问答应走 search worker。" % kb_names
            )
    except Exception:
        pass

    system = (
        "你是一个任务调度主管。分析用户请求，决定需要哪些 Worker 来处理。\n\n"
        f"可用的 Worker:\n{workers_desc}\n"
        f"{kb_hint}"
        "规则:\n"
        "1. 需要搜索信息 → 分配 search worker，给它具体的多角度搜索指令\n"
        "2. 需要分析/计算/总结/翻译/列举 → 分配 analyze worker\n"
        "3. 需要操作桌面/浏览器/生成图像/发消息 → 分配 execute worker\n"
        "4. 纯知识问答（翻译、列举、常识等）→ 只分配 analyze，不要分配 search\n"
        "5. 简单任务只分配 1 个 worker，复杂任务可以分配多个\n"
        "6. 每个 worker 分配一个清晰的具体子任务\n\n"
        '输出 JSON: {"task_type": "search"|"analyze"|"execute"|"mixed", "workers": [{"name": "...", "task": "..."}]}'
    )

    result = _call_llm(
        [{"role": "user", "content": request}],
        system_prompt=system,
    )

    try:
        parsed = json.loads(result.strip().strip("`").replace("json", ""))
    except json.JSONDecodeError:
        parsed = {
            "task_type": "mixed",
            "workers": [
                {"name": "search", "task": request},
                {"name": "analyze", "task": "分析并回复"},
            ],
        }

    workers_assigned = []
    worker_tasks = {}
    for w in parsed.get("workers", []):
        name = w["name"]
        if name in WORKER_CAPABILITIES:
            workers_assigned.append(name)
            worker_tasks[name] = w["task"]

    if not workers_assigned:
        workers_assigned = ["search"]
        worker_tasks["search"] = request

    return {
        "task_type": parsed.get("task_type", "mixed"),
        "workers_assigned": workers_assigned,
        "worker_tasks": worker_tasks,
        "worker_results": {},
        "worker_errors": {},
        "round": state.get("round", 0) + 1,
        "all_done": False,
        "trace_steps": [{"step": "supervisor_analyze", "assigned": workers_assigned}],
    }


def supervisor_collect(state: SupervisorState) -> dict:
    """Collect results from all workers, check completeness."""
    if is_cancelled():
        return {"all_done": True, "final_output": "⛔ 任务已被取消"}

    worker_results = state.get("worker_results", {})
    workers_assigned = state.get("workers_assigned", [])

    # Check if all workers completed successfully
    all_complete = all(
        w in worker_results and worker_results[w].get("success")
        for w in workers_assigned
    )

    # If all workers succeeded, ask LLM if task is done
    if all_complete:
        combined = "\n\n".join(
            f"### {w}\n{r['output'][:500]}"
            for w, r in worker_results.items()
        )

        system = (
            "你是一个任务验收员。根据 Worker 们返回的结果，判断原始任务是否已完成。\n"
            "如果信息足够充分，返回 done=true；如果还需要更多信息，返回 done=false 和补充说明。\n"
            '输出 JSON: {"done": true/false, "reason": "说明"}'
        )
        response = _call_llm(
            [
                {"role": "user", "content": f"原始请求: {state['request']}\n\nWorker 结果:\n{combined}"}
            ],
            system_prompt=system,
        )
        try:
            check = json.loads(response.strip().strip("`").replace("json", ""))
            done = check.get("done", True)
        except json.JSONDecodeError:
            done = True

        # Also check round limit
        if state.get("round", 0) >= SUPERVISOR_MAX_ROUNDS:
            done = True
    else:
        done = state.get("round", 0) >= SUPERVISOR_MAX_ROUNDS

    return {
        "all_done": done or all_complete,
        "trace_steps": [{"step": "supervisor_collect", "all_done": done}],
    }


def supervisor_replan(state: SupervisorState) -> dict:
    """If task is not done, replan for next round."""
    request = state["request"]
    worker_results = state.get("worker_results", {})
    current_round = state.get("round", 0)

    # Build context from previous results
    context = "\n".join(
        f"[{w}] {r.get('output', '')[:300]}"
        for w, r in worker_results.items()
    )

    system = (
        "上一轮 Worker 的结果不够充分，请分析缺失了什么信息，"
        "给出新一轮需要 Worker 执行的具体任务。\n"
        "只输出 JSON: {\"workers\": [{\"name\": \"...\", \"task\": \"...\"}]}"
    )
    response = _call_llm(
        [
            {"role": "user", "content": f"原始请求: {request}\n上一轮结果: {context}"}
        ],
        system_prompt=system,
    )
    try:
        parsed = json.loads(response.strip().strip("`").replace("json", ""))
    except json.JSONDecodeError:
        return {"all_done": True}

    workers = []
    tasks = {}
    for w in parsed.get("workers", []):
        name = w["name"]
        if name in WORKER_CAPABILITIES:
            workers.append(name)
            tasks[name] = w["task"]

    return {
        "workers_assigned": workers,
        "worker_tasks": tasks,
        "worker_results": {},
        "round": current_round + 1,
        "all_done": False,
    }
