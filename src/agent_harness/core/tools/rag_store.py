"""RAG Vector Store — local embedding + similarity + BM25 keyword fallback.

Stability improvements over v0.16:
  - Batched embedding API calls (not one-by-one)
  - Retry with exponential backoff when Ollama is down
  - Threading.Lock for all file operations + atomic writes (tmp+replace)
  - BM25 keyword search as fallback (character overlap was too weak)
  - Graceful handling of corrupted .npy / .json files
  - Embedding cache: skips re-embedding chunks that already have vectors
  - Strict embeddings↔chunks alignment (ENG-11/ENG-13): no vstack on misaligned
    history, quadratic rebuild instead; query degrades to BM25 when rows differ

Storage: JSON chunks + NPY embeddings per collection.
Supports PDF, DOCX, TXT, Markdown.
"""

import contextlib
import contextvars
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# ─── 访问作用域（SEC-2：可见性在数据层兜底）───
# 为什么放在这一层而不是 API 层：API 的 `_user_collection()` 只保护「API 自己拼出来的名字」，
# 但 agent 的工具（rag_query / rag_index）允许模型直接传 collection 名 —— 模型被检索内容
# 诱导后可以传别人的物理名，绕过 API 那层映射。数据层过一道，任何调用方都绕不过去。
_scope: contextvars.ContextVar[str] = contextvars.ContextVar("rag_scope_prefix", default="")


def set_scope(prefix: str) -> contextvars.Token:
    """设置本执行上下文的可见 collection 前缀。空串 = 全见（admin / CLI / demo）。"""
    return _scope.set(str(prefix or ""))


def reset_scope(token: contextvars.Token) -> None:
    _scope.reset(token)


def current_scope() -> str:
    return _scope.get()


def _check_scope(name: str) -> str:
    """作用域外访问 → 明确拒绝（PermissionError），不静默返回空结果。

    静默返回空会让调用方以为「这个 collection 是空的」，而实际是「你没权限」——
    两者修法完全不同，必须区分。
    """
    s = _scope.get()
    if s and not name.startswith(s):
        raise PermissionError(f"collection 不可见（越权访问已拒绝）: {name!r}")
    return name

# ─── Config ───

HARNESS_DIR = Path(__file__).resolve().parent.parent
RAG_DIR = os.environ.get(
    "HARNESS_RAG_DIR",
    str(HARNESS_DIR.parent.parent / "rag_data"),
)
os.makedirs(RAG_DIR, exist_ok=True)

# Embedding API
# 默认指向本机 Ollama。WSL 等场景请通过 HARNESS_OLLAMA_API 显式指定，
# 不要在代码里硬编码私网 IP —— 打包分发后会把用户文档原文发往局域网内任意主机。
OLLAMA_API = os.environ.get(
    "HARNESS_OLLAMA_API",
    "http://127.0.0.1:11434/api/embed",
)
EMBED_MODEL = os.environ.get("HARNESS_EMBED_MODEL", "nomic-embed-text")
EMBED_DIM = int(os.environ.get("HARNESS_EMBED_DIM", "768"))

# Chunking
CHUNK_SIZE = int(os.environ.get("HARNESS_RAG_CHUNK_SIZE", "500"))
CHUNK_OVERLAP = int(os.environ.get("HARNESS_RAG_OVERLAP", "100"))

# Embedding retry
_EMBED_RETRIES = 2
_EMBED_BACKOFF = 2.0  # seconds

# File lock (reentrant, for thread safety)
_lock = threading.RLock()


# ─── Path helpers ───

def _safe_collection_name(collection: str) -> str:
    """collection 名即物理目录名 —— 拒绝路径穿越/保留名（否则 API 可写任意路径）。"""
    name = str(collection or "")
    if (
        not name
        or name in (".", "..")
        or any(ch in name for ch in ("/", "\\", ":", "\x00"))
    ):
        raise ValueError(f"非法 collection 名: {collection!r}")
    return name


def _coll_dir(collection: str, create: bool = False) -> str:
    """物理目录。默认不建目录：读路径（query/info/delete/list）绝不能制造空目录。

    create=True 只给写路径用（_save_chunks / _save_embeddings 内部已自行 makedirs）。
    所有读写最终都经过这里 —— 作用域校验也放在这里，一处覆盖全部入口（SEC-2）。
    """
    d = os.path.join(RAG_DIR, _check_scope(_safe_collection_name(collection)))
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def _chunks_path(collection: str) -> str:
    return os.path.join(_coll_dir(collection), "chunks.json")


def _embs_path(collection: str) -> str:
    return os.path.join(_coll_dir(collection), "embeddings.npy")


def _load_chunks(collection: str) -> list[dict]:
    """Load chunks JSON. Returns [] on any error."""
    p = _chunks_path(collection)
    with _lock:
        try:
            if os.path.isfile(p):
                with open(p, encoding="utf-8") as f:
                    return json.load(f)
        except (json.JSONDecodeError, OSError):
            # Corrupted file — will be rebuilt by next index operation
            pass
    return []


def _save_chunks(collection: str, chunks: list[dict]):
    """Atomic write: tmp → replace."""
    p = _chunks_path(collection)
    tmp = p + ".tmp." + hashlib.md5(str(time.time()).encode()).hexdigest()[:8]
    with _lock:
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(chunks, f, ensure_ascii=False, indent=2)
            os.replace(tmp, p)
        except Exception:
            if os.path.exists(tmp):
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
            raise


def _load_embeddings(collection: str) -> np.ndarray | None:
    """Load embeddings NPY. Returns None on corruption or missing file."""
    p = _embs_path(collection)
    with _lock:
        try:
            if os.path.isfile(p):
                arr = np.load(p)
                if arr.ndim == 2 and arr.shape[1] == EMBED_DIM:
                    return arr
        except Exception:
            pass
    return None


def _save_embeddings(collection: str, embs: np.ndarray) -> None:
    """Save embeddings to temp file then atomic replace.

    ``tmp`` must end with ``.npy``: numpy's ``save`` appends that suffix on its
    own when missing, which made ``os.replace(tmp, p)`` look for a file that
    never existed (embeddings were never persisted — pre-existing ENG-11 bug).
    """
    p = _embs_path(collection)
    tmp = p + ".tmp." + hashlib.md5(str(time.time()).encode()).hexdigest()[:8] + ".npy"
    with _lock:
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            np.save(tmp, embs)
            os.replace(tmp, p)
        except Exception:
            for candidate in (tmp, tmp[: -len(".npy")], tmp + ".npy"):
                if os.path.exists(candidate):
                    with contextlib.suppress(OSError):
                        os.unlink(candidate)
            raise


# ─── Embedding (batched + retry) ───

_EMBED_CACHE: dict[str, np.ndarray] = {}  # md5(text) -> real vector (never zero)


def _embed(texts: list[str]) -> tuple[np.ndarray, list[str]]:
    """Generate embeddings using Ollama nomic-embed-text (batched, with retry).

    Returns:
        (vectors, failed_texts)
        - vectors: ndarray shape (len(texts), EMBED_DIM), 1:1 aligned with texts
          **by position** (cached and freshly-embedded entries are re-interleaved
          in the caller's order). Failed entries are zeros.
        - failed_texts: texts whose embedding call failed (retry candidates).

    Failure contract:
        - Zero vectors are NEVER written into ``_EMBED_CACHE``, so previously
          failed texts are re-embedded on the next call after Ollama recovers.
        - The caller decides whether to save (zeros must not poison the store).
    """
    import requests as req_lib

    if not texts:
        return np.zeros((0, EMBED_DIM), dtype=np.float32), []

    keys = [hashlib.md5(t.encode("utf-8")).hexdigest() for t in texts]
    out = np.zeros((len(texts), EMBED_DIM), dtype=np.float32)
    uncached: list[int] = []
    for i, key in enumerate(keys):
        cached = _EMBED_CACHE.get(key)
        if cached is None:
            uncached.append(i)
        else:
            out[i] = cached

    if not uncached:
        return out, []

    uncached_texts = [texts[i] for i in uncached]
    uncached_keys = [keys[i] for i in uncached]

    # Quick connectivity check
    base_url = OLLAMA_API.replace("/api/embed", "")
    try:
        req_lib.get(base_url, timeout=2)
    except Exception:
        logger.warning(
            "Ollama offline at connectivity check; %d texts unembedded",
            len(uncached_texts),
        )
        return out, uncached_texts

    # Batched embedding with retry
    for attempt in range(_EMBED_RETRIES + 1):
        try:
            r = req_lib.post(
                OLLAMA_API,
                json={"model": EMBED_MODEL, "input": uncached_texts},
                timeout=30,
            )
            if r.status_code == 200:
                data = r.json()
                raw = np.array(data["embeddings"], dtype=np.float32)
                if raw.ndim != 2 or raw.shape[0] != len(uncached_texts):
                    raise ValueError(
                        f"embed response misaligned: rows={raw.shape[0] if raw.ndim else 'n/a'} "
                        f"expected={len(uncached_texts)}"
                    )
                for pos, idx in enumerate(uncached):
                    out[idx] = raw[pos]
                    if np.any(raw[pos] != 0):
                        _EMBED_CACHE[uncached_keys[pos]] = raw[pos]
                return out, []
            logger.warning("embed http status %s", r.status_code)
        except Exception as exc:
            logger.warning(
                "embed attempt %d failed: %s", attempt + 1, exc
            )
            if attempt < _EMBED_RETRIES:
                time.sleep(_EMBED_BACKOFF * (attempt + 1))
            continue

    # All retries exhausted — zeros for uncached, cached for the rest
    logger.warning(
        "Ollama embed retries exhausted; %d texts unembedded", len(uncached_texts)
    )
    return out, uncached_texts


def _rebuild_all_embeddings(collection: str) -> dict[str, Any]:
    """Rebuild every embedding of a collection from its chunk texts.

    Must be called while holding ``_lock`` (index calls it inside the lock;
    ``_embed`` itself does not take the lock, so no deadlock).

    Writes the new embeddings only when at least one real vector came back;
    otherwise leaves the existing file untouched so BM25 fallback stays active.

    Returns:
        {"rebuilt": bool, "failed": int, "total": int, "embeddings": ndarray}
    """
    chunks = _load_chunks(collection)
    texts = [c.get("text", "") for c in chunks]
    if not texts:
        return {
            "rebuilt": True,
            "failed": 0,
            "total": 0,
            "embeddings": np.zeros((0, EMBED_DIM), dtype=np.float32),
        }

    embs, failed = _embed(texts)
    failed_count = len(failed)

    if bool(np.any(embs != 0)):
        assert embs.shape[0] == len(texts), (
            f"rebuild alignment: embs={embs.shape[0]} texts={len(texts)}"
        )
        _save_embeddings(collection, embs)
        logger.info(
            "collection %r rebuilt: %d chunks, %d failed",
            collection, len(texts), failed_count,
        )
        return {
            "rebuilt": True,
            "failed": failed_count,
            "total": len(texts),
            "embeddings": embs,
        }

    logger.warning("rebuild %r skipped: Ollama still offline", collection)
    return {
        "rebuilt": False,
        "failed": len(texts),
        "total": len(texts),
        "embeddings": np.zeros((len(texts), EMBED_DIM), dtype=np.float32),
    }


# ─── BM25 keyword search (fallback when no embeddings) ───

_K1 = 1.5
_B = 0.75


def _bm25_scores(query_tokens: list[str], chunks: list[str]) -> list[float]:
    """Compute BM25 scores for a query against a list of documents.

    Works for both Chinese (no space) and English. For Chinese text,
    uses character-level n-gram tokenization.
    """
    if not query_tokens or not chunks:
        return [0.0] * len(chunks)

    n_docs = len(chunks)
    avg_dl = sum(len(d) for d in chunks) / max(n_docs, 1)

    # Document frequencies
    df = {}
    for token in query_tokens:
        count = 0
        for doc in chunks:
            if token in doc:
                count += 1
        df[token] = count

    scores = []
    for doc in chunks:
        doc_len = len(doc)
        score = 0.0
        for token in query_tokens:
            tf = doc.count(token)
            if tf == 0:
                continue
            idf = math.log((n_docs - df[token] + 0.5) / (df[token] + 0.5) + 1)
            tf_norm = tf * (_K1 + 1) / (tf + _K1 * (1 - _B + _B * doc_len / max(avg_dl, 1)))
            score += idf * tf_norm
        scores.append(score)

    return scores


def _tokenize(text: str) -> list[str]:
    """Tokenize for BM25. Uses Chinese bigrams + English words."""
    tokens = set()

    # English words
    for word in re.findall(r'[a-zA-Z0-9_]+', text):
        if len(word) >= 2:
            tokens.add(word.lower())

    # Chinese character bigrams (handles Chinese better than single chars)
    chars = "".join(c for c in text if '\u4e00' <= c <= '\u9fff')
    for i in range(len(chars) - 1):
        tokens.add(chars[i:i+2])

    # Also add individual Chinese chars for short queries
    if len(text) < 10:
        for c in chars:
            tokens.add(c)

    return list(tokens)


# ─── Cosine similarity ───

def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    dot = np.dot(a, b)
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(dot / (na * nb))


# ─── Text chunking ───

def _chunk_text(text: str, chunk_size: int = None, overlap: int = None) -> list[str]:
    """Split text into overlapping chunks by paragraph."""
    chunk_size = chunk_size or CHUNK_SIZE
    overlap = overlap or CHUNK_OVERLAP

    paragraphs = text.split("\n")
    chunks = []
    current = ""
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) > chunk_size and current:
            chunks.append(current.strip())
            current = para
        else:
            current = current + ("\n" if current else "") + para
    if current.strip():
        chunks.append(current.strip())
    if not chunks:
        chunks = [
            text[i: i + chunk_size]
            for i in range(0, len(text), chunk_size - overlap)
        ]
    return chunks


# ─── File parsing ───

SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx"}


def _parse_file(filepath: str) -> str:
    """Parse a file and return its text content."""
    ext = Path(filepath).suffix.lower()

    if ext in (".txt", ".md"):
        with open(filepath, encoding="utf-8", errors="replace") as f:
            return f.read()

    elif ext == ".pdf":
        import_errors = []
        for lib in ("pypdf", "PyPDF2", "pdfminer"):
            try:
                if lib == "pypdf":
                    from pypdf import PdfReader
                elif lib == "PyPDF2":
                    from PyPDF2 import PdfReader
                else:
                    from pdfminer.high_level import extract_text
                    return extract_text(filepath)
                reader = PdfReader(filepath)
                pages = []
                for page in reader.pages:
                    text = page.extract_text() or ""
                    pages.append(text)
                return "\n\n".join(pages)
            except ImportError:
                import_errors.append(lib)
                continue
        raise ImportError(
            "未安装 PDF 解析库。请运行: pip install pypdf\n"
            "备选: pdfminer.six (pip install pdfminer.six)"
        )

    elif ext == ".docx":
        try:
            from docx import Document
        except ImportError:
            raise ImportError("未安装 python-docx。请运行: pip install python-docx")
        doc = Document(filepath)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n".join(paragraphs)

    else:
        raise ValueError(f"不支持的文件类型: {ext}。支持的: {SUPPORTED_EXTENSIONS}")


# ─── Public API ───

def index(text: str, source: str, collection: str = "default") -> dict:
    """Index text into vector store.

    ENG-11 fix (vector↔text misalignment):
      1. Pre-check alignment: if ``len(existing_embs) != pre-index chunk count``
         the store is already corrupted (offline half-write / legacy doubling).
         Rebuild everything instead of vstack-ing on top of bad data.
      2. Embed ONLY the chunks added by this call — the old code re-embedded all
         chunks and stacked them, which doubled embeddings on Ollama recovery.
      3. Strict post-check: combined rows must equal total chunks; on mismatch we
         refuse to write (BM25 fallback stays) instead of silently poisoning.

    Returns:
        dict with: chunks_count, embedding_status, fallback, rebuilt,
        failed_embeddings
    """
    chunks = _chunk_text(text)
    with _lock:
        existing = _load_chunks(collection)
        n_before = len(existing)
        existing_embs = _load_embeddings(collection)
        if existing_embs is None:
            existing_embs = np.zeros((0, EMBED_DIM), dtype=np.float32)

        # ── Alignment pre-check (the core ENG-11 fix) ──
        rebuilt = False
        rebuild_failed = 0
        rebuild_pending = False
        if existing_embs.shape[0] != n_before:
            logger.warning(
                "collection %r misaligned: embs=%d chunks=%d -> rebuilding",
                collection, existing_embs.shape[0], n_before,
            )
            res = _rebuild_all_embeddings(collection)
            existing_embs = res["embeddings"]
            rebuilt = res["rebuilt"]
            rebuild_failed = res["failed"]
            # Rebuild produced no real vectors (Ollama still offline): keep the
            # store vector-less rather than mixing zeros with fresh vectors.
            rebuild_pending = not rebuilt

        # Append new chunks
        for i, chunk in enumerate(chunks):
            chunk_id = hashlib.md5(
                (source + str(n_before + i)).encode()
            ).hexdigest()[:12]
            existing.append({
                "id": chunk_id,
                "text": chunk,
                "source": source,
                "idx": n_before + i,
            })

        # Embed ONLY the new chunks — not all chunks (fixes doubling)
        new_embs, failed_texts = _embed(chunks)
        online = bool(np.any(new_embs != 0))

        _save_chunks(collection, existing)

        if online and not rebuild_pending:
            combined = np.vstack([existing_embs, new_embs])
            # Strict alignment check — never silently write a misaligned store
            if combined.shape[0] != len(existing):
                logger.error(
                    "collection %r alignment check failed: embs=%d chunks=%d "
                    "-> embeddings not saved (BM25 fallback)",
                    collection, combined.shape[0], len(existing),
                )
                return {
                    "chunks_count": len(chunks),
                    "embedding_status": "offline",
                    "fallback": "keyword (BM25)",
                    "rebuilt": rebuilt,
                    "failed_embeddings": len(failed_texts) + rebuild_failed,
                }
            _save_embeddings(collection, combined)
            return {
                "chunks_count": len(chunks),
                "embedding_status": "online",
                "fallback": "none",
                "rebuilt": rebuilt,
                "failed_embeddings": len(failed_texts) + rebuild_failed,
            }

        return {
            "chunks_count": len(chunks),
            "embedding_status": "offline",
            "fallback": "keyword (BM25)",
            "rebuilt": rebuilt,
            "failed_embeddings": len(failed_texts) + rebuild_failed,
        }


def index_file(filepath: str, collection: str = "default", filename: str = "") -> dict:
    """Parse and index a file.

    Args:
        filepath: Path to the file (PDF, DOCX, TXT, MD)
        collection: Collection name
        filename: Override display name

    Returns:
        dict with indexing results
    """
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"文件不存在: {filepath}")

    source = filename or os.path.basename(filepath)
    text = _parse_file(filepath)
    return index(text, source=source, collection=collection)


def query(
    query_text: str, collection: str = "default", top_k: int = 5
) -> list[dict]:
    """Search for most relevant chunks.

    Search strategy (in order):
      1. Vector search (cosine similarity) — needs Ollama online
      2. BM25 keyword search — works without Ollama, handles Chinese

    Args:
        query_text: The search query
        collection: Collection to search
        top_k: Max results

    Returns:
        List of {"text": str, "source": str, "score": float, "id": str}
    """
    chunks = _load_chunks(collection)
    if not chunks:
        return []

    embeddings = _load_embeddings(collection)
    chunk_texts = [c.get("text", "") for c in chunks]

    # ─── Strategy 1: Vector search (strict alignment, ENG-11/ENG-13) ───
    # Rows must match chunks EXACTLY: a partially-written store (chunks saved,
    # embeddings not) yields positionally wrong hits, so degrade to BM25 instead.
    use_vector = (
        embeddings is not None
        and embeddings.ndim == 2
        and len(chunks) > 0
        and embeddings.shape[0] == len(chunks)
    )
    if embeddings is not None and not use_vector:
        logger.warning(
            "RAG collection=%s embeddings(%d) != chunks(%d), degrading to BM25",
            collection, embeddings.shape[0], len(chunks),
        )

    if use_vector:
        try:
            query_emb, _ = _embed([query_text])
            if query_emb.ndim == 2 and query_emb.shape[0] > 0:
                qv = query_emb[0]
                if np.any(qv != 0):
                    scores = [
                        _cosine_similarity(qv, embeddings[i])
                        for i in range(len(chunks))
                    ]
                    scored = [(scores[i], i) for i in range(len(chunks))]
                    scored.sort(key=lambda x: x[0], reverse=True)
                    results = []
                    for score, idx in scored[:top_k]:
                        if score > 0.1:
                            results.append({
                                "text": chunk_texts[idx][:1000],
                                "source": chunks[idx].get("source", ""),
                                "score": round(score, 4),
                                "id": chunks[idx].get("id", ""),
                                "method": "vector",
                            })
                    if results:
                        return results
        except Exception:
            pass

    # ─── Strategy 2: BM25 keyword search ───
    query_tokens = _tokenize(query_text)
    scores = _bm25_scores(query_tokens, chunk_texts)
    scored = [(scores[i], i) for i in range(len(chunks))]
    scored.sort(key=lambda x: x[0], reverse=True)

    results = []
    for score, idx in scored[:top_k]:
        if score > 0.0:
            results.append({
                "text": chunk_texts[idx][:1000],
                "source": chunks[idx].get("source", ""),
                "score": round(score, 4),
                "id": chunks[idx].get("id", ""),
                "method": "bm25",
            })
    return results


def list_collections() -> list[str]:
    """List all collections **visible to the current scope**.

    过滤放在这里而不是调用方：`supervisor.py` 会把返回值直接拼进 system prompt，
    不过滤就是把所有人的 collection 物理名送给模型（进而送给用户）。
    """
    os.makedirs(RAG_DIR, exist_ok=True)
    s = _scope.get()
    with _lock:
        return sorted([
            d for d in os.listdir(RAG_DIR)
            if os.path.isdir(os.path.join(RAG_DIR, d))
            and (not s or d.startswith(s))
        ])


def delete_collection(collection: str) -> bool:
    """Delete a collection and all its data."""
    import shutil
    d = _coll_dir(collection)
    with _lock:
        if os.path.isdir(d):
            shutil.rmtree(d)
            return True
    return False


def collection_info(collection: str) -> dict:
    """Get info about a collection (incl. embedding↔chunk alignment)."""
    chunks = _load_chunks(collection)
    sources = set()
    total_chars = 0
    for c in chunks:
        sources.add(c.get("source", "unknown"))
        total_chars += len(c.get("text", ""))
    embs = _load_embeddings(collection)
    aligned = embs is not None and embs.shape[0] == len(chunks)
    return {
        "name": collection,
        "chunks": len(chunks),
        "sources": sorted(sources),
        "total_chars": total_chars,
        "doc_count": len(sources),
        "embedding_available": embs is not None,
        "embedding_aligned": aligned,
        "embedding_rows": int(embs.shape[0]) if embs is not None else 0,
    }
