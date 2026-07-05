# Changelog

All notable changes to Agent Harness will be documented in this file.

## [0.3.0] — 2026-07-05
n## [0.4.0] — 2026-07-05

### Added
- **验证通过**：Multi-Agent 在 qwen2.5-coder:14b 上实测通过
- Eval 套件首次跑通：6/8 pass (75%)，均分 61/100，总耗时 71.8s
- ComicAgent 脚本生成验证：4 分镜，10.3s
- README 含验证结果和 demo

### Fixed
- Worker 图死循环：失败时不再回到 executor，直接 advance
- 全部 LLM reasoning 剥离适配
- 默认端点切换：8080(35B) → 8081 proxy(qwen2.5-coder:14b)
- recursion_limit=50 防无限循环

### Changed
- 默认模型 qwen2.5-coder:14b（50x 提速，0.7s/调用）
- LLM timeout 120s→300s，内容提取统一 reasoning fallback


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
