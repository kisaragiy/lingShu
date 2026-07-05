"""Execution Tracing — OpenTelemetry-style lightweight tracing for Agent pipelines.

Produces trace.json after each task with:
  - Per-node timing (latency, tokens, status)
  - Full span tree (supervisor → workers → tools)
  - Tool call statistics (success rate, cost distribution)
  - Human-readable summary for debugging

Usage:
    from pipeline.tracing import TraceCollector

    collector = TraceCollector()
    with collector.span("supervisor_analyze"):
        # ... do work ...
        collector.record_tokens(1500)
    collector.to_dict()  # → trace tree as dict
    collector.to_json("trace.json")  # → save to file
"""

import json
import time
import os
from dataclasses import dataclass, field
from typing import Any, Optional
from contextlib import contextmanager


@dataclass
class TraceSpan:
    """A single execution span in the trace tree."""
    name: str
    span_type: str = "node"            # "supervisor" | "worker" | "tool" | "llm"
    start_time: float = 0.0
    end_time: float = 0.0
    duration_ms: float = 0.0
    tokens_used: int = 0
    status: str = "ok"                 # "ok" | "error" | "skipped"
    error_message: str = ""
    metadata: dict = field(default_factory=dict)
    children: list["TraceSpan"] = field(default_factory=list)

    @property
    def duration_str(self) -> str:
        if self.duration_ms < 1000:
            return f"{self.duration_ms:.0f}ms"
        return f"{self.duration_ms / 1000:.1f}s"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.span_type,
            "duration_ms": round(self.duration_ms, 1),
            "tokens": self.tokens_used,
            "status": self.status,
            "error": self.error_message or None,
            "metadata": self.metadata,
            "children": [c.to_dict() for c in self.children],
        }

    def total_tokens(self) -> int:
        return self.tokens_used + sum(c.total_tokens() for c in self.children)

    def total_duration_ms(self) -> float:
        return self.duration_ms + sum(c.total_duration_ms() for c in self.children)


@dataclass
class TraceTree:
    """Complete trace tree for one task execution."""
    trace_id: str
    root: TraceSpan
    start_time: float = 0.0
    end_time: float = 0.0
    circuit_breaker_tripped: bool = False
    circuit_breaker_reason: str = ""

    @property
    def total_tokens(self) -> int:
        return self.root.total_tokens()

    @property
    def total_duration_s(self) -> float:
        return self.root.total_duration_ms() / 1000

    @property
    def tool_stats(self) -> dict:
        """Aggregate tool call statistics."""
        tools: dict[str, dict] = {}

        def _collect(span: TraceSpan):
            if span.span_type == "tool":
                if span.name not in tools:
                    tools[span.name] = {"calls": 0, "success": 0, "fail": 0, "total_ms": 0}
                stats = tools[span.name]
                stats["calls"] += 1
                stats["total_ms"] += span.duration_ms
                if span.status == "ok":
                    stats["success"] += 1
                else:
                    stats["fail"] += 1
            for child in span.children:
                _collect(child)

        _collect(self.root)
        # Add rates
        for name, s in tools.items():
            s["success_rate"] = round(s["success"] / s["calls"] * 100, 1) if s["calls"] > 0 else 0
            s["avg_ms"] = round(s["total_ms"] / s["calls"], 1) if s["calls"] > 0 else 0
        return tools

    def summary(self) -> str:
        """Human-readable summary string."""
        lines = [
            f"Trace: {self.trace_id[:8]}",
            f"Duration: {self.total_duration_s:.1f}s",
            f"Tokens: {self.total_tokens:,}",
        ]
        if self.circuit_breaker_tripped:
            lines.append(f"⚠ Circuit breaker: {self.circuit_breaker_reason}")
        lines.append(f"\nTool calls ({len(self.tool_stats)} tools):")
        for name, stats in sorted(self.tool_stats.items(), key=lambda x: -x[1]["calls"]):
            s = stats
            lines.append(
                f"  {name}: {s['calls']} calls, "
                f"{s['success_rate']:.0f}% success, "
                f"avg {s['avg_ms']:.0f}ms"
            )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "total_duration_s": round(self.total_duration_s, 2),
            "total_tokens": self.total_tokens,
            "circuit_breaker": {
                "tripped": self.circuit_breaker_tripped,
                "reason": self.circuit_breaker_reason,
            },
            "tool_stats": self.tool_stats,
            "span_tree": self.root.to_dict(),
        }

    def to_json(self, path: str = ""):
        """Save trace to JSON file."""
        if not path:
            path = f"trace_{self.trace_id[:8]}.json"
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        return path


class TraceCollector:
    """Collects trace spans during agent execution."""

    def __init__(self, trace_id: str = ""):
        import uuid
        self.trace_id = trace_id or str(uuid.uuid4())
        self._stack: list[TraceSpan] = []
        self._root: Optional[TraceSpan] = None
        self._start_time = time.time()
        self._cb_tripped = False
        self._cb_reason = ""

    @contextmanager
    def span(self, name: str, span_type: str = "node", **metadata):
        """Context manager for recording a span.

        Usage:
            with collector.span("search_worker", "worker", task="search AI news"):
                # ... do work ...
                collector.record_tokens(500)
        """
        span = TraceSpan(
            name=name,
            span_type=span_type,
            start_time=time.time(),
            metadata=metadata,
        )

        if self._stack:
            self._stack[-1].children.append(span)
        else:
            self._root = span

        self._stack.append(span)
        try:
            yield span
            span.status = "ok"
        except Exception as e:
            span.status = "error"
            span.error_message = str(e)
            raise
        finally:
            span.end_time = time.time()
            span.duration_ms = (span.end_time - span.start_time) * 1000
            self._stack.pop()

    def record_tokens(self, n: int):
        """Record token usage for the current span."""
        if self._stack:
            self._stack[-1].tokens_used += n

    def record_metadata(self, key: str, value: Any):
        """Add metadata to the current span."""
        if self._stack:
            self._stack[-1].metadata[key] = value

    def record_tool_call(self, tool_name: str, duration_ms: float,
                         success: bool, error: str = ""):
        """Record a tool call as a child span of the current span."""
        tool_span = TraceSpan(
            name=tool_name,
            span_type="tool",
            duration_ms=duration_ms,
            status="ok" if success else "error",
            error_message=error,
        )
        if self._stack:
            self._stack[-1].children.append(tool_span)

    def mark_circuit_breaker(self, reason: str):
        """Mark that circuit breaker tripped."""
        self._cb_tripped = True
        self._cb_reason = reason

    def build(self) -> TraceTree:
        """Build the final trace tree."""
        root = self._root or TraceSpan(name="empty", span_type="root")
        return TraceTree(
            trace_id=self.trace_id,
            root=root,
            start_time=self._start_time,
            end_time=time.time(),
            circuit_breaker_tripped=self._cb_tripped,
            circuit_breaker_reason=self._cb_reason,
        )

    def to_dict(self) -> dict:
        return self.build().to_dict()

    def to_json(self, path: str = "") -> str:
        return self.build().to_json(path)

    def summary(self) -> str:
        return self.build().summary()
