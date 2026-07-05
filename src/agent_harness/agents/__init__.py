"""Agent implementations — Supervisor + Workers + ComicAgent."""

from .supervisor import (
    supervisor_analyze,
    supervisor_collect,
    supervisor_replan,
    WORKER_CAPABILITIES,
)
from .workers import build_worker, run_worker
from .comic_agent import produce_comic, ComicResult, ComicScript, Scene

__all__ = [
    "supervisor_analyze", "supervisor_collect", "supervisor_replan",
    "WORKER_CAPABILITIES",
    "build_worker", "run_worker",
    "produce_comic", "ComicResult", "ComicScript", "Scene",
]
