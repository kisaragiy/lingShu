"""Backward-compatible entry point — delegates to agent_harness.main.

Previously this file contained all routes. They've been split into:
  - agent_harness.main          → FastAPI app with shared middleware
  - agent_harness.apps.research.api  → Research (灵枢) routes
  - agent_harness.apps.cs_demo.api   → CS Demo routes

Importing from this module still works for compatibility.
"""
from agent_harness.main import app, main

__all__ = ["app", "main"]
