# agent-harness 统一命令入口
.PHONY: install install-prod test test-integration test-cov lint lint-fix format format-check typecheck security run serve docs clean docker-up docker-down docker-build ci help

install:  ## 安装开发依赖（含 pytest/ruff/mypy —— 注意 dev 在 optional-dependencies，`uv sync` 装不出它们）
	uv sync --extra dev

install-prod:  ## 安装生产依赖
	uv sync --no-dev

test:  ## 运行测试（默认不跑需外部服务的集成用例）
	uv run pytest

test-integration:  ## 运行集成测试（需本地服务：cs_demo :8788 / llama.cpp :8080）
	uv run pytest -m integration

test-cov:  ## 运行测试 + 覆盖率
	uv run pytest --cov=src/agent_harness --cov-report=term --cov-report=xml

lint:  ## 代码质量检查
	uv run ruff check src/

lint-fix:  ## 自动修复可修复的问题
	uv run ruff check --fix --unsafe-fixes src/

format:  ## 格式化代码
	uv run ruff format src/

format-check:  ## 检查格式（CI 用）
	uv run ruff format --check src/

typecheck:  ## 类型检查（渐进式）
	uv run mypy src/agent_harness/ --ignore-missing-imports --follow-imports=skip 2>/dev/null || echo "⚠️ mypy 未安装或配置"

security:  ## 安全审计
	uv run pip-audit --strict --progress-spinner=off 2>&1 || echo "⚠️ 有已知漏洞依赖"

run:  ## 启动开发服务器（与 README 的入口一致；api_fastapi 只是兼容壳）
	uv run python -m agent_harness.main

serve: run  ## 别名

docs:  ## 启动文档服务器
	uv run mkdocs serve 2>/dev/null || echo "⚠️ mkdocs 未安装 (pip install mkdocs-material)"

clean:  ## 清理缓存
	rm -rf dist build *.egg-info
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true

docker-up:  ## 启动 Docker 开发环境
	docker compose up -d

docker-down:  ## 停止 Docker 环境
	docker compose down

docker-build:  ## 构建生产镜像
	docker build -t agent-harness .

ci: test lint format-check security  ## CI 全量检查（本地模拟）

help:  ## 显示帮助
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-16s\033[0m %s\n", $$1, $$2}'

# 用法示例:
#   make install   # 装依赖
#   make test      # 跑测试
#   make lint      # 代码检查
#   make run       # 启动服务
#   make ci        # CI 全量检查
