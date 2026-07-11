"""Agent implementations — Supervisor + Workers + ComicAgent."""

from .comic_agent import ComicResult, ComicScript, Scene, produce_comic
from .supervisor import (
    WORKER_CAPABILITIES,
    supervisor_analyze,
    supervisor_collect,
    supervisor_replan,
)
from .workers import build_worker, run_worker

__all__ = [
    "supervisor_analyze", "supervisor_collect", "supervisor_replan",
    "WORKER_CAPABILITIES",
    "build_worker", "run_worker",
    "produce_comic", "ComicResult", "ComicScript", "Scene",
]
