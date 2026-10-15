"""Supervisor Agent — analyzes tasks and delegates to workers."""
import sys
import json

from ..config import (
    LLAMA_API,
    MODEL_LLAMA,
    SUPERVISOR_MAX_ROUNDS,
)
from ..pipeline.cancel import is_cancelled
from ..pipeline.llm import ledger_bump
from ..pipeline.state import SupervisorState

# ─── Worker capability definitions ───

WORKER_CAPABILITIES = {
    "search": {
        "description": "网页搜索、信息抓取、RAG语义检索、实时数据查询、知识问答",
        "tools": ["search", "fetch", "web_scrape", "web_browse", "rag_query", "datetime", "think"],
        "side_effect": False,
    },
    "analyze": {
        "description": "数据分析、报告生成、内容总结、代码执行、文本处理",
        "tools": ["think", "code_execute", "summarize", "file_read", "file_write"],
        "side_effect": True,
    },
    "execute": {
        "description": "桌面自动化、浏览器操作、ComfyUI图像生成、文件管理、应用启动",
        "tools": ["desktop_gui", "browser_automation", "app_launch", "comfyui_text2img",
                  "comfyui_img2img", "file_write", "chat_send"],
        "side_effect": True,
    },
}


# ─── Context builders (arch review 2026-09-07, Cognition P1) ───

def _build_replan_context(worker_results: dict, worker_errors: dict,
                          max_chars: int = 300) -> str:
    """Build replan context that includes failure reasons, not just output summaries.

    Cognition P1 fix: a replan that cannot see *why* a worker failed tends to
    re-dispatch the same doomed task. Include errors and partial-failure marks.
    """
    lines = []
    for w, r in (worker_results or {}).items():
        if not isinstance(r, dict):
            continue
        ok = bool(r.get("success", False))
        out = str(r.get("output", ""))[:max_chars]
        lines.append(f"[{w}] {'✅成功' if ok else '❌失败'}: {out}")
        errs = list(r.get("errors") or []) + list((worker_errors or {}).get(w, []) or [])
        if errs:
            lines.append(f"  └ 失败原因: {'; '.join(str(e)[:200] for e in errs[:3])}")
        if ok and "部分步骤失败" in out:
            lines.append(f"  └ ⚠️ {w} 汇报部分步骤失败——新一轮请只补缺失部分，勿重复已完成工作")
    # Errors-only entries (dispatch-level failures not present in results)
    for w, errs in (worker_errors or {}).items():
        if w in (worker_results or {}):
            continue
        if errs:
            lines.append(f"[{w}] ❌失败(调度级): {'; '.join(str(e)[:200] for e in errs[:3])}")
    return "\n".join(lines) or "(无上一轮结果)"


def _last_raw_evidence(data) -> str:
    """Extract the last non-empty tool result text from worker data records."""
    if isinstance(data, list):
        for item in reversed(data):
            if not isinstance(item, dict):
                continue
            res = item.get("result") or {}
            if isinstance(res, dict):
                for key in ("data", "output", "error"):
                    val = res.get(key)
                    if isinstance(val, str) and val.strip():
                        return val
                nested = res.get("data")
                if isinstance(nested, dict):
                    for key in ("data", "output", "result", "content"):
                        val = nested.get(key)
                        if isinstance(val, str) and val.strip():
                            return val
    return ""


def _build_judge_context(worker_results: dict) -> str:
    """Build judge context; append raw tool evidence for failed/partial workers.

    Cognition P1 fix: judge decisions should not rest only on the worker's own
    LLM-compressed summary (self-report). When a worker failed or reports partial
    failure, attach a slice of its raw tool output so the judge can cross-check.
    """
    parts = []
    for w, r in (worker_results or {}).items():
        if not isinstance(r, dict):
            continue
        out = str(r.get("output", "无结果"))[:500]
        parts.append(f"### {w}\n{out}")
        flagged = (not r.get("success", True)) or ("部分步骤失败" in out)
        if flagged:
            raw = _last_raw_evidence(r.get("data"))
            if raw:
                parts.append(f"[{w} 原始证据]\n{raw[:300]}")
    return "\n\n".join(parts)


# ─── LLM call helper ───

def _record_llm(model: str, usage: dict | None = None, success: bool = True) -> None:
    """把 LLM 调用记进指标（延迟导入，观测层故障绝不影响主链路）。

    ⚠️ 这里是 Agent 主链路的 LLM 入口 —— 它**直接 post LLAMA_API，绕过 `pipeline/llm.py`**，
    所以 llm.py 里的接线覆盖不到它。实测：日志里 8+ 次「LLM 调用异常」，
    而 /metrics 里 status="error" 一行都没有（监控对主链路 LLM 完全失明）。
    """
    try:
        from ..pipeline.llm import _record_llm_metrics
        _record_llm_metrics(model, usage, success)
    except Exception:
        pass


def _emit_llm_envelope(messages: list[dict] | None, system_prompt: str,
                       temperature: float, usage: dict | None,
                       data: dict | None = None) -> None:
    """落主链路 LLM 调用的可复现信封（P1-1）。观测层故障绝不影响主链路。"""
    try:
        from ..reproducibility import reproducibility_envelope
        from ..agent_log import log_llm_envelope
        resp = data or {}
        env = reproducibility_envelope(
            model=MODEL_LLAMA,
            model_version=resp.get("model") or resp.get("model_version"),
            provider=resp.get("provider"),
            temperature=temperature,
            system_fingerprint=resp.get("system_fingerprint"),
            messages=messages,
            system_prompt=system_prompt,
        )
        log_llm_envelope(None, env, usage)
    except Exception:
        pass


def _call_llm_full(messages: list[dict], system_prompt: str = "",
                   max_tokens: int = 4096, timeout: int = 300) -> tuple[str, str]:
    """Call LLM, return (content, finish_reason).

    dsh P0: 捕获 finish_reason 以识别 max_tokens 截断(length)，
    供 sticky 逻辑区分「截断但实质内容」vs「真正空返回」。
    """
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
        resp = req_lib.post(LLAMA_API, json=payload, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            choice = data["choices"][0]
            msg = choice["message"]
            finish_reason = str(choice.get("finish_reason", ""))
            content = msg.get("content", "") or msg.get("reasoning_content", "")[-500:] or ""
            usage = data.get("usage", {}) or {}
            ledger_bump(usage.get("total_tokens", 0))
            _record_llm(MODEL_LLAMA, usage)
            _emit_llm_envelope(messages, system_prompt, 0.3, usage, data=data)
            return content, finish_reason
        _record_llm(MODEL_LLAMA, None, success=False)     # 非 200：后端在但报错
        _emit_llm_envelope(messages, system_prompt, 0.3, None)
        return "", ""
    except Exception:
        _record_llm(MODEL_LLAMA, None, success=False)     # 不可达/超时
        _emit_llm_envelope(messages, system_prompt, 0.3, None)
        return "", "error"


def _call_llm(messages: list[dict], system_prompt: str = "",
              max_tokens: int = 4096, timeout: int = 300) -> str:
    """Call LLM for supervisor reasoning (returns content only)."""
    content, _ = _call_llm_full(
        messages, system_prompt=system_prompt,
        max_tokens=max_tokens, timeout=timeout,
    )
    return content


# ─── Fallback plan (LLM unreachable) ───

def _fallback_plan(user_request: str) -> dict:
    """Create a basic task plan without LLM when backend is unreachable."""
    request_lower = user_request.lower()

    # Keyword matching to classify intent
    search_keywords = ["搜索", "查", "查找", "找", "搜", "查询", "search", "find",
                       "最新", "新闻", "价格", "对比", "哪个好", "评测", "评价"]
    analyze_keywords = ["分析", "总结", "翻译", "总结", "计算", "代码", "python",
                        "写", "生成", "创建", "解释", "分析预测"]
    execute_keywords = ["打开", "启动", "截图", "点击", "桌面", "浏览器",
                        "图片", "图像", "绘画", "画图", "生成图片"]

    workers = []
    worker_tasks = {}

    for kw in search_keywords:
        if kw in request_lower:
            workers.append("search")
            worker_tasks["search"] = f"搜索相关信息: {user_request}"
            break

    for kw in analyze_keywords:
        if kw in request_lower:
            workers.append("analyze")
            worker_tasks["analyze"] = f"分析处理: {user_request}"
            break

    for kw in execute_keywords:
        if kw in request_lower:
            workers.append("execute")
            worker_tasks["execute"] = f"执行操作: {user_request}"
            break

    if not workers:
        workers = ["search", "analyze"]
        worker_tasks = {
            "search": f"搜索相关信息: {user_request}",
            "analyze": f"分析并回复: {user_request}",
        }

    return {
        "task_type": "mixed" if len(workers) > 1 else workers[0],
        "workers_assigned": workers,
        "worker_tasks": worker_tasks,
        "worker_results": {},
        "worker_errors": {},
        "round": 1,
        "all_done": False,
        "trace_steps": [{"step": "supervisor_analyze_fallback", "assigned": workers}],
    }


# ─── Supervisor nodes ───

def supervisor_analyze(state: SupervisorState) -> dict:
    """Analyze request and determine which workers to assign."""
    request = state["request"]

    # ─── dsh P0: 空决策短路 — 无有效输入不烧模型调用 ───
    if not request or not request.strip():
        print("[Supervisor] 空请求，跳过 LLM 调用", file=sys.stderr)
        return {
            "task_type": "mixed",
            "workers_assigned": [],
            "worker_tasks": {},
            "worker_results": {},
            "worker_errors": {},
            "round": state.get("round", 0) + 1,
            "all_done": True,
            "final_output": "⚠️ 空请求，未执行任何操作。",
            "trace_steps": [{"step": "supervisor_analyze_shortcircuit", "empty_request": True}],
        }

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
                f"\n\n📚 知识库可用! 已有 collections: {kb_names}\n"
                "如果用户询问与已上传文档相关的问题，搜索 worker 使用 rag_query 工具检索知识库。\n"
                "知识库问答应走 search worker。"
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

    if not result or not result.strip():
        print("[Supervisor] LLM 返回空，使用 fallback 规划", file=sys.stderr)
        fallback = _fallback_plan(request)
        return {
            **fallback,
            "round": state.get("round", 0) + 1,
        }

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

    # ─── dsh P0: 空决策短路 — 无 worker 不再调 verify LLM ───
    if not workers_assigned:
        return {
            "all_done": True,
            "final_output": state.get("final_output", "⚠️ 空请求，无可用 Worker"),
        }

    # Check if all workers completed successfully
    all_complete = all(
        w in worker_results and worker_results[w].get("success")
        for w in workers_assigned
    )

    # If all workers succeeded, ask LLM if task is done
    infeasible = False
    feasible = False
    reason = ""
    if all_complete:
        combined = _build_judge_context(worker_results)

        system = (
            "你是一个任务验收员。根据 Worker 们返回的结果，判断原始任务是否已完成。\n"
            "三种判定（必须保守，宁可判「还缺信息」也不要误判可解任务为不可解）：\n"
            "1. 信息充分、目标已达成 → {\"done\": true, \"reason\": \"简述\"}\n"
            "2. 还缺信息，且下一轮有合理手段可补 → {\"done\": false, \"reason\": \"缺什么\"}\n"
            "3. 任务不可行 / 信息根本不足（数据不存在、来源不可达、需求自相矛盾等，"
            "再搜再算也拿不到）→ {\"done\": false, \"infeasible\": true, "
            "\"reason\": \"为什么不可行 + 到底缺什么\"}\n"
            "只有当所有合理手段都已用尽仍无法获得关键信息时，才选第 3 态；"
            "只要还剩可行手段，就选第 2 态。\n"
            '输出 JSON: {"done": true/false, "infeasible": false, "reason": "说明"}'
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
            # WP16: conservative abstention — only abstain on an explicit infeasible flag
            infeasible = bool(check.get("infeasible", False))
            reason = str(check.get("reason", "") or "")
        except json.JSONDecodeError:
            done = True
            infeasible = False

        # A round-cap force-finish is NOT evidence of infeasibility — never
        # let "ran out of rounds" masquerade as an abstention.
        if state.get("round", 0) >= SUPERVISOR_MAX_ROUNDS:
            done = True
            infeasible = False
        feasible = bool(done) and not infeasible
    else:
        done = state.get("round", 0) >= SUPERVISOR_MAX_ROUNDS

    feasibility = "infeasible" if infeasible else ("feasible" if feasible else "")
    return {
        "all_done": done or all_complete,
        "feasibility": feasibility,
        "feasibility_reason": reason if infeasible else "",
        "trace_steps": [{"step": "supervisor_collect", "all_done": done,
                         "feasibility": feasibility}],
    }


def supervisor_replan(state: SupervisorState) -> dict:
    """If task is not done, replan for next round."""
    request = state["request"]
    worker_results = state.get("worker_results", {})
    worker_errors = state.get("worker_errors", {})
    current_round = state.get("round", 0)

    # Build context from previous results, including failure reasons (Cognition P1)
    context = _build_replan_context(worker_results, worker_errors)

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
