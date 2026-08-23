"""Integration tests — real RAG index + query + search chain."""
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest


@pytest.fixture
def temp_rag_dir():
    with tempfile.TemporaryDirectory() as td:
        old = os.environ.get("HARNESS_DATA_DIR")
        os.environ["HARNESS_DATA_DIR"] = td
        yield Path(td)
        if old:
            os.environ["HARNESS_DATA_DIR"] = old


class TestRAGIntegration:
    """Real RAG index → query integration."""

    def test_index_and_query(self, temp_rag_dir):
        from agent_harness.core.tools.rag_store import index, query
        text = "Agent Harness is a multi-agent orchestration framework."
        r = index(text, source="test", collection="integration_test")
        assert r.get("chunks_count", 0) > 0
        results = query("multi-agent", collection="integration_test", top_k=3)
        assert len(results) > 0

    def test_empty_collection_returns_empty(self, temp_rag_dir):
        from agent_harness.core.tools.rag_store import query
        results = query("anything", collection="nonexistent", top_k=3)
        assert len(results) == 0

    def test_bm25_fallback_without_embeddings(self, temp_rag_dir):
        from agent_harness.core.tools.rag_store import index, query
        index("FastAPI is a Python web framework", source="doc", collection="bm25_test")
        index("RAG uses vector search with BM25 fallback", source="doc", collection="bm25_test")
        results = query("FastAPI web", collection="bm25_test", top_k=3)
        assert len(results) > 0
        assert any("FastAPI" in (r.get("text") or "") for r in results)
