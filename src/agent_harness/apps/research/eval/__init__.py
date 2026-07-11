"""Evaluation module — dataset, scorer, runner."""

from .dataset import CI_TASKS, EVAL_DATASET
from .runner import EvalReport, run_eval, save_report
from .scorer import EvalScore, score_task

__all__ = [
    "EVAL_DATASET", "CI_TASKS",
    "score_task", "EvalScore",
    "run_eval", "save_report", "EvalReport",
]
