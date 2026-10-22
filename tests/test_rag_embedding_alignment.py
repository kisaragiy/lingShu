"""Tests for ENG-11 / ENG-13: RAG vector↔text alignment.

Run:
    pytest tests/test_rag_embedding_alignment.py -v

Covers:
  A. Normal online indexing → embs rows == chunks rows.
  B. Ollama down during index → chunks saved, embs not saved; recovery rebuilds
     (no doubling / no positional misalignment).
  C. Pre-corrupted store (embs rows != chunks rows) → rebuild on next index.
  D. Query in misaligned state → BM25 fallback (method=="bm25", no exception).
  E. _embed never caches zero vectors (recovery re-embeds previously-failed text).
  F. _rebuild_all_embeddings direct call.
  G. collection_info reports embedding_aligned / embedding_rows.
"""

from __future__ import annotations

import importlib
import os
import sys

import numpy as np
import pytest


# ─── Fixtures ───

@pytest.fixture()
def rag(tmp_path, monkeypatch):
    """Import a fresh rag_store module pointed at tmp_path.

    Default-mocks _embed to return real (ones) vectors — i.e. Ollama online.
    Tests that need offline behavior re-mock via _set_embed_phase().
    """
    monkeypatch.setenv("HARNESS_RAG_DIR", str(tmp_path))
    monkeypatch.setenv("HARNESS_EMBED_DIM", "8")

    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)

    for mod_name in list(sys.modules):
        if mod_name.startswith("rag_store"):
            del sys.modules[mod_name]

    import agent_harness.core.tools.rag_store as rs
    importlib.reload(rs)
    rs._EMBED_CACHE.clear()

    # Preserve the real _embed for cache-poisoning tests
    rs._real_embed = rs._embed

    dim = rs.EMBED_DIM

    def _online_embed(texts):
        if not texts:
            return np.zeros((0, dim), dtype=np.float32), []
        return np.ones((len(texts), dim), dtype=np.float32), []

    rs._embed = _online_embed
    rs._EMBED_CACHE.clear()
    return rs


def _set_embed_phase(rag, phase: int = 0):
    """Set rag._embed behavior: phase=0 online(ones), phase=1 offline(zeros)."""
    dim = rag.EMBED_DIM
    rag._EMBED_CACHE.clear()
    if phase == 1:
        def offline_embed(texts):
            if not texts:
                return np.zeros((0, dim), dtype=np.float32), []
            return np.zeros((len(texts), dim), dtype=np.float32), list(texts)
        rag._embed = offline_embed
    else:
        def online_embed(texts):
            if not texts:
                return np.zeros((0, dim), dtype=np.float32), []
            return np.ones((len(texts), dim), dtype=np.float32), []
        rag._embed = online_embed


# ─── Tests ───

class TestOnlineIndexAlignment:
    """Scenario A: normal online indexing keeps embs/chunks aligned."""

    def test_single_index_aligned(self, rag):
        rag.index("金融研报 尽调 现金流 分析", source="a.txt", collection="c1")
        embs = rag._load_embeddings("c1")
        chunks = rag._load_chunks("c1")
        assert embs is not None
        assert embs.shape[0] == len(chunks)
        assert len(chunks) >= 1

    def test_multi_index_aligned(self, rag):
        rag.index("第一份文档 内容 关于 现金流", source="a.txt", collection="c2")
        rag.index("第二份文档 关于 负债 比率", source="b.txt", collection="c2")
        rag.index("第三份文档 关于 营收 增长", source="c.txt", collection="c2")
        embs = rag._load_embeddings("c2")
        chunks = rag._load_chunks("c2")
        assert embs.shape[0] == len(chunks)
        assert len(chunks) >= 3

    def test_return_dict_online(self, rag):
        r = rag.index("测试内容", source="x.txt", collection="c3")
        assert r["embedding_status"] == "online"
        assert r["fallback"] == "none"
        assert r["rebuilt"] is False


class TestOfflineRecoveryNoDoubling:
    """Scenario B: offline → recovery must rebuild, not double."""

    def test_offline_then_recover_no_doubling(self, rag):
        _set_embed_phase(rag, 0)

        # 1. Online index
        rag.index("在线内容 第一批", source="a.txt", collection="d1")
        embs1 = rag._load_embeddings("d1")
        chunks1 = rag._load_chunks("d1")
        assert embs1.shape[0] == len(chunks1)

        # 2. Offline index: chunks saved, embs NOT saved
        _set_embed_phase(rag, 1)
        r_off = rag.index("掉线期间 第二批", source="b.txt", collection="d1")
        assert r_off["embedding_status"] == "offline"
        assert r_off["fallback"] == "keyword (BM25)"
        chunks_after_off = rag._load_chunks("d1")
        embs_after_off = rag._load_embeddings("d1")
        assert embs_after_off.shape[0] == len(chunks1)
        assert len(chunks_after_off) > len(chunks1)

        # 3. Recovery: next online index triggers rebuild
        _set_embed_phase(rag, 0)
        r_rec = rag.index("恢复后 第三批", source="c.txt", collection="d1")
        assert r_rec["embedding_status"] == "online"
        assert r_rec["rebuilt"] is True

        embs_final = rag._load_embeddings("d1")
        chunks_final = rag._load_chunks("d1")
        assert embs_final.shape[0] == len(chunks_final), (
            f"doubling bug: embs={embs_final.shape[0]} chunks={len(chunks_final)}"
        )

    def test_query_after_recovery_returns_vector(self, rag):
        _set_embed_phase(rag, 0)
        rag.index("现金流 负债 比率 分析", source="a.txt", collection="d2")
        _set_embed_phase(rag, 1)
        rag.index("掉线内容 关键词", source="b.txt", collection="d2")
        _set_embed_phase(rag, 0)
        r = rag.index("恢复后内容", source="c.txt", collection="d2")
        assert r["rebuilt"] is True

        results = rag.query("现金流", collection="d2", top_k=3)
        assert len(results) > 0
        assert results[0]["method"] == "vector"


class TestCorruptedStoreRebuild:
    """Scenario C: pre-corrupted file triggers rebuild."""

    def test_manual_corruption_triggers_rebuild(self, rag):
        _set_embed_phase(rag, 0)
        rag.index("正常内容 第一批", source="a.txt", collection="e1")
        chunks = rag._load_chunks("e1")
        n = len(chunks)

        bad_embs = np.ones((n + 5, rag.EMBED_DIM), dtype=np.float32)
        rag._save_embeddings("e1", bad_embs)

        r = rag.index("新内容 触发重建", source="b.txt", collection="e1")
        assert r["rebuilt"] is True

        embs = rag._load_embeddings("e1")
        chunks_after = rag._load_chunks("e1")
        assert embs.shape[0] == len(chunks_after)

    def test_fewer_embs_than_chunks_triggers_rebuild(self, rag):
        _set_embed_phase(rag, 0)
        rag.index("内容 A", source="a.txt", collection="e2")
        rag.index("内容 B", source="b.txt", collection="e2")
        chunks = rag._load_chunks("e2")

        good_embs = rag._load_embeddings("e2")
        truncated = good_embs[: len(chunks) - 1]
        rag._save_embeddings("e2", truncated)

        r = rag.index("内容 C", source="c.txt", collection="e2")
        assert r["rebuilt"] is True
        embs = rag._load_embeddings("e2")
        chunks_after = rag._load_chunks("e2")
        assert embs.shape[0] == len(chunks_after)


class TestQueryMisalignedFallback:
    """Scenario D: misaligned store → query returns BM25, not vector."""

    def test_misaligned_query_falls_back_to_bm25(self, rag):
        _set_embed_phase(rag, 0)
        rag.index("苹果 香蕉 橙子 水果", source="a.txt", collection="f1")
        chunks = rag._load_chunks("f1")

        bad_embs = np.ones((len(chunks) + 2, rag.EMBED_DIM), dtype=np.float32)
        rag._save_embeddings("f1", bad_embs)

        results = rag.query("水果", collection="f1", top_k=3)
        assert len(results) > 0
        for res in results:
            assert res["method"] == "bm25"

    def test_aligned_query_uses_vector(self, rag):
        _set_embed_phase(rag, 0)
        rag.index("苹果 香蕉 橙子 水果 沙拉", source="a.txt", collection="f2")
        results = rag.query("水果", collection="f2", top_k=3)
        assert len(results) > 0
        assert results[0]["method"] == "vector"


class TestEmbedCacheNoZeroPoisoning:
    """Scenario E: _embed never caches zero vectors."""

    def test_zero_vectors_not_cached(self, rag):
        texts = ["唯一文本 测试缓存"]

        import requests
        orig_get = requests.get
        orig_post = requests.post

        def fail_get(*a, **kw):
            raise ConnectionError("offline")

        def fail_post(*a, **kw):
            raise ConnectionError("offline")

        requests.get = fail_get
        requests.post = fail_post
        try:
            vecs, failed = rag._real_embed(texts)
        finally:
            requests.get = orig_get
            requests.post = orig_post

        assert np.all(vecs == 0)
        assert len(failed) == 1

        key = __import__("hashlib").md5(texts[0].encode("utf-8")).hexdigest()
        assert key not in rag._EMBED_CACHE, "zero vector poisoned cache!"

    def test_recovery_reembeds_previously_failed(self, rag):
        dim = rag.EMBED_DIM
        texts = ["恢复后的文本 测试"]
        import requests
        orig_get = requests.get
        orig_post = requests.post

        def fail_get(*a, **kw):
            raise ConnectionError("offline")

        requests.get = fail_get
        try:
            vecs1, failed1 = rag._real_embed(texts)
        finally:
            requests.get = orig_get
        assert len(failed1) == 1

        class FakeResp:
            status_code = 200

            def json(self_inner):
                return {"embeddings": [[1.0] * dim]}

        def ok_get(*a, **kw):
            return FakeResp()

        def ok_post(*a, **kw):
            return FakeResp()

        requests.get = ok_get
        requests.post = ok_post
        try:
            vecs2, failed2 = rag._real_embed(texts)
        finally:
            requests.get = orig_get
            requests.post = orig_post

        assert np.any(vecs2 != 0), "recovery did not re-embed failed text"
        assert len(failed2) == 0


class TestRebuildDirect:
    """Scenario F: _rebuild_all_embeddings direct call."""

    def test_rebuild_returns_proper_shape(self, rag):
        _set_embed_phase(rag, 0)
        rag.index("内容 甲 乙 丙", source="a.txt", collection="g1")
        rag.index("内容 丁 戊 己", source="b.txt", collection="g1")

        with rag._lock:
            res = rag._rebuild_all_embeddings("g1")
        chunks = rag._load_chunks("g1")
        assert res["rebuilt"] is True
        assert res["embeddings"].shape[0] == len(chunks)
        assert res["total"] == len(chunks)
        assert res["failed"] == 0

    def test_rebuild_empty_collection(self, rag):
        with rag._lock:
            res = rag._rebuild_all_embeddings("nonexistent")
        assert res["rebuilt"] is True
        assert res["embeddings"].shape[0] == 0

    def test_rebuild_offline_skips_save(self, rag):
        _set_embed_phase(rag, 0)
        rag.index("好内容", source="a.txt", collection="g3")
        good_embs = rag._load_embeddings("g3")

        _set_embed_phase(rag, 1)
        with rag._lock:
            res = rag._rebuild_all_embeddings("g3")
        assert res["rebuilt"] is False
        still_good = rag._load_embeddings("g3")
        assert still_good.shape[0] == good_embs.shape[0]


class TestCollectionInfo:
    """Scenario G: collection_info alignment field."""

    def test_aligned_collection_info(self, rag):
        _set_embed_phase(rag, 0)
        rag.index("对齐内容", source="a.txt", collection="h1")
        info = rag.collection_info("h1")
        assert info["embedding_aligned"] is True
        assert info["embedding_rows"] == info["chunks"]

    def test_misaligned_collection_info(self, rag):
        _set_embed_phase(rag, 0)
        rag.index("内容", source="a.txt", collection="h2")
        chunks = rag._load_chunks("h2")
        rag._save_embeddings(
            "h2", np.ones((len(chunks) + 1, rag.EMBED_DIM), dtype=np.float32)
        )
        info = rag.collection_info("h2")
        assert info["embedding_aligned"] is False
