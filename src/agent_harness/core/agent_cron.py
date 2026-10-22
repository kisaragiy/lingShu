"""Agent Cron Scheduler — run agent tasks on a schedule.

Built-in scheduler for the 灵枢 agent platform. Runs tasks in a background
thread using a simple polling loop. Tasks are stored as JSON files.

Scheduling model:
    Every task persists ``next_run`` (epoch seconds). The polling loop runs a
    task only when ``now >= next_run``, then recomputes ``next_run`` from
    *now* (not from the missed slot) — so a service that was down for three
    days catches up exactly once instead of replaying every missed slot.

Field semantics:
    ``*``, ``a-b`` range, ``a,b,c`` list and ``*/n`` step are supported;
    ``?``/``L``/``W`` are not. weekday uses POSIX cron numbering —
    Sunday=0 (7 is accepted as an alias), Monday=1 ... Saturday=6 — so
    ``0 9 * * 1-5`` is Monday through Friday at 09:00, matching standard cron.
    (``time.struct_time.tm_wday`` is Monday=0, hence the +1 shift below.
    A bare ``7`` works; ``7`` inside a range does not.)

Usage:
    scheduler = AgentScheduler()
    scheduler.start()
    scheduler.add_task("daily_report", "0 9 * * 1-5", "生成昨日行业简报")
    scheduler.stop()

Task file format:
    ~/.agent-harness/scheduler/tasks/*.json
"""

import json
import logging
import os
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEDULER_DIR = Path(os.environ.get(
    "HARNESS_SCHEDULER_DIR",
    str(Path.home() / ".agent-harness" / "scheduler"),
))
TASKS_DIR = SCHEDULER_DIR / "tasks"
_POLL_INTERVAL = 30  # seconds between checks
_MAX_SCAN_MINUTES = 525600  # scan at most one year ahead for the next slot
# Fields a caller may write through update_task(). Anything else is dropped —
# the old whitelist covered only schedule/prompt/enabled, so last_run /
# next_run / run_count passed explicitly were silently discarded.
_WRITABLE = ("schedule", "prompt", "enabled", "last_run", "next_run", "run_count")


def _ensure():
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    (SCHEDULER_DIR / "logs").mkdir(parents=True, exist_ok=True)


def _task_path(task_id: str) -> Path:
    safe = task_id.replace("/", "_").replace("\\", "_")
    return TASKS_DIR / (f"{safe}.json")


def _parse_cron(expr: str) -> dict | None:
    """Parse a simple cron expression.

    Supports:
      - "0 9 * * *"        (min hour day month weekday)
      - "0 9 * * 1-5"      (range — Monday..Friday)
      - "0 9 * * 1,3,5"    (list)
      - "*/15 * * * *"     (step)
      - "every 30m"        (human-friendly)
      - "every 2h"         (human-friendly)
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


def _parse_field(pattern: str, value: int, sunday_alias: bool = False) -> bool:
    """Match one cron field against one value.

    Handles ``*``, ``a-b`` range, ``a,b,c`` list (recursive) and ``*/n`` step.
    Malformed patterns return False instead of raising — a bad field must
    disable that field, not kill the whole tick loop.

    ``sunday_alias``: weekday only — a bare ``7`` also matches value 0
    (POSIX treats both 0 and 7 as Sunday).
    """
    pattern = pattern.strip()
    if pattern == "*":
        return True
    if "," in pattern:
        return any(_parse_field(p, value, sunday_alias) for p in pattern.split(","))
    if pattern.startswith("*/"):
        try:
            step = int(pattern[2:])
        except ValueError:
            return False
        return step > 0 and value % step == 0
    if "-" in pattern:
        lo, hi = pattern.split("-", 1)
        try:
            return int(lo) <= value <= int(hi)
        except ValueError:
            return False
    try:
        num = int(pattern)
    except ValueError:
        return False
    if num == value:
        return True
    return sunday_alias and num == 7 and value == 0


def _cron_matches(cron: dict, now: time.struct_time) -> bool:
    """True when a 5-field cron pattern matches the given local time."""
    fields = {
        "min": now.tm_min,
        "hour": now.tm_hour,
        "day": now.tm_mday,
        "month": now.tm_mon,
        # tm_wday is Monday=0; POSIX cron is Sunday=0 → shift by one.
        "weekday": (now.tm_wday + 1) % 7,
    }
    for key, value in fields.items():
        if not _parse_field(cron.get(key, "*"), value, sunday_alias=(key == "weekday")):
            return False
    return True


def _next_run_after(parsed: dict, after_ts: float,
                    max_scan: int = _MAX_SCAN_MINUTES) -> float | None:
    """First slot strictly after ``after_ts`` (epoch seconds), local time.

    Interval schedules just add their period. Cron schedules scan minute by
    minute from the next minute boundary, at most one year ahead.
    """
    if parsed["type"] == "interval":
        return int(after_ts) + parsed["interval"]
    t = (int(after_ts) // 60) * 60 + 60
    for _ in range(max_scan):
        if _cron_matches(parsed, time.localtime(t)):
            return t
        t += 60
    return None


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
        # Scheduled from now on — adding a task must not fire it immediately.
        "next_run": _next_run_after(parsed, now),
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
    kwargs = {k: v for k, v in kwargs.items() if k in _WRITABLE}
    if "schedule" in kwargs:
        parsed = _parse_cron(kwargs["schedule"])
        if parsed is None:
            raise ValueError("无效的调度表达式")
        kwargs.setdefault("next_run", _next_run_after(parsed, time.time()))
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
        try:
            tasks.append(json.loads(f.read_text("utf-8")))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("cron: 任务文件读取失败 %s: %s", f.name, e)
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

    def list_tasks(self) -> list[dict]:
        """List all scheduled tasks (instance method, mirrors module-level list_tasks)."""
        return list_tasks()

    def _loop(self):
        """Main scheduling loop.

        A failing tick is logged with traceback and the loop keeps running.
        Silently swallowing it (the old contextlib.suppress) meant a scheduler
        that stopped firing produced no evidence anywhere.
        """
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as e:
                logger.error("cron tick failed: %s", e, exc_info=True)
            self._stop_event.wait(_POLL_INTERVAL)

    def _tick(self):
        """Run every task whose next_run has arrived."""
        now = time.time()

        for task in list_tasks():
            if not task.get("enabled", True):
                continue
            parsed = _parse_cron(task.get("schedule", ""))
            if parsed is None:
                logger.warning("cron: 任务 %s 的调度表达式无效: %r",
                               task.get("id"), task.get("schedule"))
                continue

            next_run = task.get("next_run")
            if next_run is None:
                # Legacy task file (written before next_run was consumed):
                # schedule it from now on, do not fire retroactively.
                update_task(task["id"], next_run=_next_run_after(parsed, now))
                continue

            if now < next_run:
                continue

            if now - next_run > 2 * _POLL_INTERVAL:
                logger.warning(
                    "cron: 任务 %s 错过了 %s 的计划运行，补跑一次",
                    task.get("id"),
                    datetime.fromtimestamp(next_run).strftime("%Y-%m-%d %H:%M"),
                )
            self._run_task(task, parsed)

    def _run_task(self, task: dict, parsed: dict | None = None):
        """Execute a task: run the agent with the task's prompt."""
        task_id = task["id"]
        prompt = task.get("prompt", "")
        if not prompt:
            return

        # Persist the next slot *before* spawning the worker: recomputing from
        # now (not from the missed slot) is what makes catch-up run once
        # instead of replaying every slot missed while the service was down.
        now = int(time.time())
        parsed = parsed or _parse_cron(task.get("schedule", ""))
        next_ts = _next_run_after(parsed, now) if parsed else None
        update_task(task_id, last_run=now, next_run=next_ts,
                    run_count=task.get("run_count", 0) + 1)

        # Execute agent in a sub-thread
        t = threading.Thread(
            target=self._execute,
            args=(task_id, prompt),
            daemon=True,
        )
        t.start()

    def _execute(self, task_id: str, prompt: str):
        """Actually run the agent. Runs in its own thread."""
        log_dir = SCHEDULER_DIR / "logs"
        try:
            from .graph_multi import run_multi_agent
            result = run_multi_agent(prompt)
            output = result.get("final_output", "") or "[完成]"

            # Log result
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
            logger.error("cron: 任务 %s 执行失败: %s", task_id, e)
            try:
                log_dir.mkdir(exist_ok=True)
                ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
                (log_dir / ("error_{}_{}.json".format(task_id.replace("/", "_"), ts))).write_text(
                    json.dumps({"ts": ts, "task_id": task_id, "error": str(e),
                               "traceback": _tb.format_exc()[-500:]},
                              ensure_ascii=False, indent=2)
                )
            except OSError:
                logger.error("cron: 任务 %s 的错误日志写入失败", task_id, exc_info=True)


# Global singleton
_scheduler: AgentScheduler | None = None


def get_scheduler() -> AgentScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AgentScheduler()
    return _scheduler
