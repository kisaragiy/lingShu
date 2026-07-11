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

# Copy static assets (not included in wheel)
COPY src/agent_harness/static/ /usr/local/lib/python3.11/site-packages/agent_harness/static/
COPY .env.example /app/.env.example

EXPOSE 8788

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:8788/health || exit 1

CMD ["agent-harness", "serve"]
