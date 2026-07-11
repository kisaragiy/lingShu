"""
并行步骤执行器

在 plan_validator_node 中检测可并行步骤（如多个搜索），
在 executor_node 中用 ThreadPoolExecutor 同时执行。

用法:
    phases = detect_parallel_steps(plan)
    for phase_type, steps in phases:
        if phase_type == "parallel":
            results = execute_parallel(steps)
        else:
            results = [execute_single(step)]
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from .registry import call_tool, validate_result

# 可以并行的工具类别
PARALLEL_TOOLS = {"search", "fetch", "web_scrape", "agent_browser", "stock_realtime",
                  "stock_search", "stock_history"}


def detect_parallel_steps(plan: list[dict]) -> list[tuple[str, list]]:
    """将 plan 分成串行阶段，每个阶段内可并行的步骤合并

    Returns:
        [(phase_type, steps)] 其中 phase_type 为 "parallel" 或 "single"
        steps 是步骤列表（parallel 时多个，single 时单个）
    """
    phases = []
    current_parallel: list[dict] = []

    for step in plan:
        tool = step.get("tool", "")
        if tool in PARALLEL_TOOLS:
            current_parallel.append(step)
        else:
            if current_parallel:
                phases.append(("parallel" if len(current_parallel) > 1 else "single", current_parallel))
                current_parallel = []
            phases.append(("single", [step]))

    if current_parallel:
        phases.append(("parallel" if len(current_parallel) > 1 else "single", current_parallel))

    return phases


def execute_parallel(steps: list[dict], max_workers: int = 4) -> list[dict]:
    """并行执行多个互不依赖的工具步骤"""
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {}
        for step in steps:
            tool = step.get("tool", "think")
            args = step.get("args", {})
            future = pool.submit(call_tool, tool, **args)
            future_map[future] = step

        for future in as_completed(future_map):
            step = future_map[future]
            try:
                result = future.result()
                validation = validate_result(step["tool"], result)
                results.append({
                    "step": step,
                    "result": result,
                    "validation": validation,
                })
            except Exception as e:
                results.append({
                    "step": step,
                    "result": {"success": False, "error": str(e), "data": None},
                    "validation": {"passed": False, "reason": str(e), "severity": "error"},
                })

    return results


def execute_single(step: dict) -> list[dict]:
    """同步执行单个步骤"""
    tool = step.get("tool", "think")
    args = step.get("args", {})
    result = call_tool(tool, **args)
    validation = validate_result(tool, result)
    return [{
        "step": step,
        "result": result,
        "validation": validation,
    }]
