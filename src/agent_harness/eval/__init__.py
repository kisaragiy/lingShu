"""Evaluation module — dataset, scorer, runner."""

from .dataset import EVAL_DATASET, CI_TASKS
from .scorer import score_task, EvalScore
from .runner import run_eval, save_report, EvalReport

__all__ = [
    "EVAL_DATASET", "CI_TASKS",
    "score_task", "EvalScore",
    "run_eval", "save_report", "EvalReport",
]
