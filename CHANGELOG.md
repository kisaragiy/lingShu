# Changelog

All notable changes to Agent Harness will be documented in this file.

## [0.3.0] — 2026-07-05

### Added
- **ComicAgent** — end-to-end AIGC short video production pipeline
  - LLM-driven script generation with storyboard (scene count, narration, visual prompts)
  - Batch image generation via ComfyUI API with IPAdapter character consistency
  - Voiceover generation via edge-tts (Microsoft Xiaoxiao neural, Chinese)
  - ffmpeg zoom+concat video assembly with audio sync
  - Graceful degradation: falls back to defaults when LLM/ComfyUI unavailable
- CLI command: `agent-harness comic "描述" --scenes 6`
- Output structure: `comic_output/{timestamp}/output.mp4` + `script.json` + intermediates

### Changed
- Bumped version from 0.2.0 → 0.3.0

## [0.2.0] — 2026-07-05

### Added
- **Execution Tracing** (`pipeline/tracing.py`)
  - `TraceSpan` / `TraceTree` / `TraceCollector` — OpenTelemetry-style span tree
  - Per-node timing, token tracking, tool call statistics
  - Circuit breaker status recording
  - JSON export + human-readable summary
- **Evaluation Suite** (`eval/`)
  - 10-task evaluation dataset (8 CI-safe, 2 network-dependent)
  - 100-point scoring rubric: keywords(40) + tools(30) + latency(15) + cost(15)
  - Batch eval runner with pass/fail reporting
  - Pass threshold: ≥ 60/100
- CLI commands: `agent-harness run --trace` and `agent-harness eval`

### Changed
- `graph_multi.py`: integrated tracing via `enable_tracing=True` flag
- `run.py`: added `--trace`, `--trace-output`, and `eval` subcommands
- `pipeline/__init__.py`: exports tracing classes

## [0.1.0] — 2026-07-05

### Added
- **Multi-Agent Architecture** — Supervisor-Worker pattern
  - `supervisor.py`: task analysis → worker assignment → result collection → replanning
  - `workers.py`: 3 specialized workers (Search, Analyze, Execute) with parallel execution
  - `graph_multi.py`: LangGraph orchestration with subgraph workers
- **Single-Agent Pipeline** (backward compatible)
  - 5-stage pipeline: planner → executor → router → corrector → finalizer
  - Triple circuit breaker: token/time/no-progress
- **Tool Ecosystem** — 40 tools
  - `tools/desktop.py`: Windows GUI automation, WeChat/QQ messaging, browser control
  - `tools/web.py`: web search, fetch, scrape
  - `tools/comfyui.py`: AI image/video generation, LoRA management (10 tools)
  - `tools/misc.py`: code execution, file ops, RAG, stock market, GitHub issues
- **API Servers**
  - FastAPI (OpenAI-compatible): `agent-harness serve` on port 8788
  - MCP stdio server: `agent-harness mcp` for Hermes/Claude Desktop
- **Project Structure**
  - `pyproject.toml` with `pip install -e .` support
  - Environment-variable-driven configuration
  - `agents/`, `pipeline/`, `tools/`, `eval/` modular layout

---

## Versioning

This project follows [Semantic Versioning](https://semver.org/):

- **MAJOR** (0.x): pre-1.0, breaking changes possible
- **MINOR** (x.0): new features (new agent types, new tools, new capabilities)
- **PATCH** (x.x.0): bug fixes, performance improvements, docs

Current phase: **0.x** — active development, API may change.
