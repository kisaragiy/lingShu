# Stage 1: Build wheel
FROM python:3.11-slim AS builder

WORKDIR /build
COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir build && \
    python -m build --wheel && \
    ls -la dist/

# Stage 2: Runtime — minimal image
FROM python:3.11-slim

WORKDIR /app

# Install runtime deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Copy and install the built wheel from builder
COPY --from=builder /build/dist/*.whl .
RUN pip install --no-cache-dir *.whl && rm -f *.whl

# 静态资源已由 pyproject 的 package-data 打进 wheel：
#   agent_harness/apps/research/static/（主前端，main.py 的 RESEARCH_STATIC_DIR）
#   agent_harness/static/admin/（管理后台）
# 这里曾手工 COPY 兜底，但只补了 admin、漏了主前端整个目录 → 容器里前端 404。
COPY .env.example /app/.env.example

EXPOSE 8788

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:8788/health || exit 1

CMD ["agent-harness", "serve"]
