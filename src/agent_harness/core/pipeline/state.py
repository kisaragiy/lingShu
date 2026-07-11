"""Pipeline state definitions — single-agent and multi-agent."""

from typing import Any, TypedDict


class HarnessState(TypedDict, total=False):
    """Single-agent LangGraph pipeline state."""
    request: str
    plan: list[dict]
    current_step: int
    results: list[dict]
    errors: list[str]
    retry_count: int
    final_output: str
    conversation_history: list[dict]
    goal: str
    stop_conditions: list[str]
    loop_state_path: str
    iteration_count: int
    stop_conditions_met: bool
    should_finalize: bool
    enable_review: bool
    review_passed: bool
    review_feedback: str
    validator_fixes: list[str]
    trace_id: str
    trace_steps: list[dict]


class SupervisorState(TypedDict, total=False):
    """Multi-agent supervisor state."""
    request: str
    goal: str
    task_type: str                     # "search" | "analyze" | "execute" | "mixed"
    round: int                         # current supervisor round
    workers_assigned: list[str]        # ["search", "analyze", "execute"]
    worker_tasks: dict[str, str]       # {worker_name: task_description}
    worker_results: dict[str, dict]    # {worker_name: WorkerResult}
    worker_errors: dict[str, list[str]]
    all_done: bool                     # supervisor decides if task is complete
    final_output: str
    errors: list[str]
    trace_id: str
    trace_steps: list[dict]
    circuit_breaker: Any               # CircuitBreaker instance


class WorkerState(TypedDict, total=False):
    """Individual worker subgraph state."""
    worker_name: str                   # "search" | "analyze" | "execute"
    task: str                          # task description for this worker
    available_tools: list[str]         # tools this worker can use
    plan: list[dict]                   # mini plan: [{name, tool, args}]
    current_step: int
    results: list[dict]                # [{step, result, validation}]
    errors: list[str]
    retry_count: int
    final_output: str                  # worker's final answer to supervisor
    trace_steps: list[dict]


class WorkerResult(TypedDict):
    """What a worker returns to the supervisor."""
    worker_name: str
    success: bool
    output: str
    data: Any
    trace_steps: list[dict]
    errors: list[str]
    elapsed_s: float
