"""Agent Cron Scheduler — run agent tasks on a schedule.

Built-in scheduler for the 灵枢 agent platform. Runs tasks in a background
thread using a simple polling loop. Tasks are stored as JSON files.

Usage:
    scheduler = AgentScheduler()
    scheduler.start()
    scheduler.add_task("daily_report", "0 9 * * *", "生成昨日行业简报")
    scheduler.stop()

Task file format:
    ~/.agent-harness/scheduler/tasks/*.json
"""

import contextlib
import json
import os
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

SCHEDULER_DIR = Path(os.environ.get(
    "HARNESS_SCHEDULER_DIR",
    str(Path.home() / ".agent-harness" / "scheduler"),
))
TASKS_DIR = SCHEDULER_DIR / "tasks"
_POLL_INTERVAL = 30  # seconds between checks


def _ensure():
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    (SCHEDULER_DIR / "logs").mkdir(parents=True, exist_ok=True)


def _task_path(task_id: str) -> Path:
    safe = task_id.replace("/", "_").replace("\\", "_")
    return TASKS_DIR / (f"{safe}.json")


def _parse_cron(expr: str) -> dict | None:
    """Parse a simple cron expression. Returns dict with next_match info or None.
    
    Supports:
      - "0 9 * * *"     (min hour day month weekday)
      - "*/15 * * * *"   (every 15 minutes)
      - "every 30m"      (human-friendly)
      - "every 2h"       (human-friendly)
    """
    expr = expr.strip()

    # Human-friendly
    m = re.match(r'^every\s+(\d+)\s*(m|min|h|hour)s?$', expr.lower())
    if m:
        val = int(m.group(1))
        unit = m.group(2)
        interval = val * 60 if unit in ('m', 'min') else val * 3600
        return {"type": "interval", "interval": interval}

    # Standard cron: "min hour day month weekday"
    parts = expr.split()
    if len(parts) == 5:
        return {"type": "cron", "raw": expr, "min": parts[0], "hour": parts[1],
                "day": parts[2], "month": parts[3], "weekday": parts[4]}

    return None


def _cron_matches(cron: dict, now: time.struct_time) -> bool:
    """Check if a cron expression matches the given time."""
    if cron["type"] == "interval":
        return True  # interval tasks are handled differently

    fields = {
        "min": now.tm_min,
        "hour": now.tm_hour,
        "day": now.tm_mday,
        "month": now.tm_mon,
        "weekday": now.tm_wday,
    }
    for key, value in fields.items():
        pattern = cron.get(key, "*")
        if pattern == "*":
            continue
        if pattern.startswith("*/"):
            step = int(pattern[2:])
            if step == 0 or value % step != 0:
                return False
        else:
            try:
                if int(pattern) != value:
                    return False
            except ValueError:
                return False
    return True


# ─── Task CRUD ───

def add_task(task_id: str, schedule: str, prompt: str,
             enabled: bool = True) -> dict:
    """Add a scheduled task.

    Args:
        task_id: Unique identifier
        schedule: Cron expression or human-friendly ("every 30m")
        prompt: Agent task prompt
        enabled: Whether the task starts enabled

    Returns:
        Task dict
    """
    _ensure()
    parsed = _parse_cron(schedule)
    if parsed is None:
        raise ValueError(f"无效的调度表达式: {schedule}")

    now = int(time.time())
    task = {
        "id": task_id,
        "schedule": schedule,
        "prompt": prompt,
        "enabled": enabled,
        "created_at": now,
        "last_run": None,
        "next_run": None,
        "run_count": 0,
    }
    path = _task_path(task_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(task, f, ensure_ascii=False, indent=2)
    return task


def update_task(task_id: str, **kwargs) -> dict | None:
    """Update a task's fields."""
    task = get_task(task_id)
    if task is None:
        return None
    for k, v in kwargs.items():
        if k in ("schedule", "prompt", "enabled"):
            task[k] = v
    if "schedule" in kwargs:
        parsed = _parse_cron(kwargs["schedule"])
        if parsed is None:
            raise ValueError("无效的调度表达式")
    path = _task_path(task_id)
    # Re-read and merge to avoid race conditions
    try:
        existing = json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        existing = task
    existing.update(kwargs)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    return existing


def delete_task(task_id: str) -> bool:
    """Delete a scheduled task."""
    path = _task_path(task_id)
    if path.exists():
        path.unlink()
        return True
    return False


def get_task(task_id: str) -> dict | None:
    """Get a task by ID."""
    path = _task_path(task_id)
    try:
        return json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def list_tasks() -> list[dict]:
    """List all scheduled tasks."""
    _ensure()
    tasks = []
    for f in sorted(TASKS_DIR.glob("*.json")):
        with contextlib.suppress(json.JSONDecodeError, OSError):
            tasks.append(json.loads(f.read_text("utf-8")))
    return tasks


# ─── Runner ───

class AgentScheduler:
    """Background thread that checks and runs scheduled tasks."""

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._running = False

    def start(self):
        """Start the scheduler loop in a background thread."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="agent-scheduler")
        self._thread.start()

    def stop(self):
        """Stop the scheduler loop gracefully."""
        self._stop_event.set()
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _loop(self):
        """Main scheduling loop."""
        while not self._stop_event.is_set():
            with contextlib.suppress(Exception):
                self._tick()
            self._stop_event.wait(_POLL_INTERVAL)

    def _tick(self):
        """Check all tasks and run any that are due."""
        tasks = list_tasks()
        now = time.time()
        now_local = time.localtime()

        for task in tasks:
            if not task.get("enabled", True):
                continue
            parsed = _parse_cron(task.get("schedule", ""))
            if parsed is None:
                continue

            should_run = False
            if parsed["type"] == "interval":
                interval = parsed["interval"]
                last_run = task.get("last_run")
                if last_run is None or (now - last_run) >= interval:
                    should_run = True
            elif parsed["type"] == "cron":
                if _cron_matches(parsed, now_local):
                    last_run = task.get("last_run")
                    last_local = time.localtime(last_run) if last_run else None
                    if last_local is None or (
                        last_local.tm_min != now_local.tm_min or
                        last_local.tm_hour != now_local.tm_hour
                    ):
                        should_run = True

            if should_run:
                self._run_task(task)

    def _run_task(self, task: dict):
        """Execute a task: run the agent with the task's prompt."""
        task_id = task["id"]
        prompt = task.get("prompt", "")
        if not prompt:
            return

        # Update last_run immediately to prevent re-trigger
        now = int(time.time())
        update_task(task_id, last_run=now, run_count=task.get("run_count", 0) + 1)

        # Execute agent in a sub-thread
        t = threading.Thread(
            target=self._execute,
            args=(task_id, prompt),
            daemon=True,
        )
        t.start()

    def _execute(self, task_id: str, prompt: str):
        """Actually run the agent. Runs in its own thread."""
        try:
            from .graph_multi import run_multi_agent
            result = run_multi_agent(prompt)
            output = result.get("final_output", "") or "[完成]"

            # Log result
            log_dir = SCHEDULER_DIR / "logs"
            log_dir.mkdir(exist_ok=True)
            ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            log_entry = {
                "ts": ts,
                "task_id": task_id,
                "output_len": len(output),
                "output_preview": output[:200],
            }
            log_file = log_dir / ("run_{}_{}.json".format(task_id.replace("/", "_"), ts))
            log_file.write_text(json.dumps(log_entry, ensure_ascii=False, indent=2))

        except Exception as e:
            import traceback as _tb
            log_dir = SCHEDULER_DIR / "logs"
            log_dir.mkdir(exist_ok=True)
            ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            (log_dir / ("error_{}_{}.json".format(task_id.replace("/", "_"), ts))).write_text(
                json.dumps({"ts": ts, "task_id": task_id, "error": str(e),
                           "traceback": _tb.format_exc()[-500:]},
                          ensure_ascii=False, indent=2)
            )


# Global singleton
_scheduler: AgentScheduler | None = None


def get_scheduler() -> AgentScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AgentScheduler()
    return _scheduler
