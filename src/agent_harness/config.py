"""Configuration — LLM endpoints, thresholds, paths."""

import os
from pathlib import Path

# ─── LLM API endpoints ───
LLAMA_API = os.environ.get("HARNESS_LLAMA_API", "http://127.0.0.1:8081/v1/chat/completions")
OLLAMA_API = os.environ.get("HARNESS_OLLAMA_API", "http://172.18.9.126:11434/api/generate")
DEEPSEEK_API = os.environ.get("HARNESS_DEEPSEEK_API", "http://127.0.0.1:9000/v1/chat/completions")
CLOUD_API_DIRECT = os.environ.get("HARNESS_CLOUD_API", "http://127.0.0.1:9099/v1/chat/completions")
CLOUD_API_KEY = os.environ.get("HARNESS_CLOUD_KEY", "sk-local")

# ─── Model names ───
MODEL_LLAMA = os.environ.get("HARNESS_MODEL_LLAMA", "qwen2.5-coder:14b")
MODEL_DEEPSEEK = os.environ.get("HARNESS_MODEL_DEEPSEEK", "deepseek-v4-pro")

# ─── Paths ───
HARNESS_DIR = Path(__file__).resolve().parent
MEMORY_DIR = Path(os.environ.get("HARNESS_MEMORY_DIR", HARNESS_DIR.parent.parent / "memory"))
SKILLS_DIR = Path(os.environ.get("HARNESS_SKILLS_DIR", HARNESS_DIR.parent.parent / "skills"))

# ─── Orchestration limits ───
MAX_RETRIES = int(os.environ.get("HARNESS_MAX_RETRIES", "2"))
MAX_ITERATIONS = int(os.environ.get("HARNESS_MAX_ITERATIONS", "10"))
MAX_TOKENS_PER_TASK = int(os.environ.get("HARNESS_MAX_TOKENS", "100000"))
MAX_WALL_TIME = int(os.environ.get("HARNESS_MAX_TIME", "600"))
MAX_NO_PROGRESS = int(os.environ.get("HARNESS_MAX_NO_PROGRESS", "5"))

# ─── Multi-agent settings ───
MAX_WORKER_CONCURRENCY = int(os.environ.get("HARNESS_MAX_WORKERS", "3"))
SUPERVISOR_MAX_ROUNDS = int(os.environ.get("HARNESS_SUPERVISOR_ROUNDS", "3"))
