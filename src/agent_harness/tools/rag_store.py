"""RAG Vector Store — local embedding + similarity + BM25 keyword fallback.

Stability improvements over v0.16:
  - Batched embedding API calls (not one-by-one)
  - Retry with exponential backoff when Ollama is down
  - Threading.Lock for all file operations + atomic writes (tmp+replace)
  - BM25 keyword search as fallback (character overlap was too weak)
  - Graceful handling of corrupted .npy / .json files
  - Embedding cache: skips re-embedding chunks that already have vectors

Storage: JSON chunks + NPY embeddings per collection.
Supports PDF, DOCX, TXT, Markdown.
"""

import hashlib
import json
import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np

# ─── Config ───

HARNESS_DIR = Path(__file__).resolve().parent.parent
RAG_DIR = os.environ.get(
    "HARNESS_RAG_DIR",
    str(HARNESS_DIR.parent.parent / "rag_data"),
)
os.makedirs(RAG_DIR, exist_ok=True)

# Embedding API
OLLAMA_API = os.environ.get(
    "HARNESS_OLLAMA_API",
    "http://172.18.9.126:11434/api/embed",
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

def _coll_dir(collection: str) -> str:
    d = os.path.join(RAG_DIR, str(collection))
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
                with open(p, "r", encoding="utf-8") as f:
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
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            raise


def _load_embeddings(collection: str) -> Optional[np.ndarray]:
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


def _save_embeddings(collection: str, embs: np.ndarray):
    """Save embeddings to temp file then atomic replace."""
    p = _embs_path(collection)
    tmp = p + ".tmp." + hashlib.md5(str(time.time()).encode()).hexdigest()[:8]
    with _lock:
        try:
            np.save(tmp, embs)
            os.replace(tmp, p)
        except Exception:
            if os.path.exists(tmp + ".npy"):
                try:
                    os.unlink(tmp + ".npy")
                except OSError:
                    pass
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            raise


# ─── Embedding (batched + retry) ───

_EMBED_CACHE: dict[str, np.ndarray] = {}  # md5(text) → vector


def _embed(texts: list[str]) -> np.ndarray:
    """Generate embeddings using Ollama nomic-embed-text (batched, with retry).

    Returns all-zero vectors if Ollama is unreachable after retries.
    Results are cached by text hash to avoid re-computation.
    """
    import requests as req_lib

    if not texts:
        return np.zeros((0, EMBED_DIM), dtype=np.float32)

    # Check cache first
    uncached = []
    cached_vecs = []
    for t in texts:
        key = hashlib.md5(t.encode("utf-8")).hexdigest()
        if key in _EMBED_CACHE:
            cached_vecs.append(_EMBED_CACHE[key])
        else:
            uncached.append((key, t))

    if not uncached:
        return np.array(cached_vecs)

    uncached_texts = [t for _, t in uncached]
    uncached_keys = [k for k, _ in uncached]

    # Quick connectivity check
    base_url = OLLAMA_API.replace("/api/embed", "")
    try:
        req_lib.get(base_url, timeout=2)
    except Exception:
        # Ollama down — use zeros
        zeros = np.zeros((len(texts), EMBED_DIM), dtype=np.float32)
        return zeros

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
                # Cache and return
                for i, key in enumerate(uncached_keys):
                    if i < len(raw):
                        _EMBED_CACHE[key] = raw[i]
                all_vecs = cached_vecs + [raw[i] for i in range(len(raw))]
                return np.array(all_vecs)
        except Exception:
            if attempt < _EMBED_RETRIES:
                time.sleep(_EMBED_BACKOFF * (attempt + 1))
            continue

    # All retries exhausted — return zeros for uncached, cached for the rest
    empty = np.zeros((len(uncached_texts), EMBED_DIM), dtype=np.float32)
    for i, key in enumerate(uncached_keys):
        if i < len(empty):
            _EMBED_CACHE[key] = empty[i]
    all_vecs = cached_vecs + [empty[i] for i in range(len(empty))]
    return np.array(all_vecs)


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
    chars = [c for c in text if '\u4e00' <= c <= '\u9fff']
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
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
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
        raise ValueError("不支持的文件类型: %s。支持的: %s" % (ext, SUPPORTED_EXTENSIONS))


# ─── Public API ───

def index(text: str, source: str, collection: str = "default") -> dict:
    """Index text into vector store.

    Returns:
        dict with: chunks_count, embedding_status, fallback
    """
    chunks = _chunk_text(text)
    with _lock:
        existing = _load_chunks(collection)
        existing_embs = _load_embeddings(collection)

        new_embs = _embed(chunks)
        ollama_online = bool(np.any(new_embs != 0))

        start_idx = len(existing)
        for i, chunk in enumerate(chunks):
            chunk_id = hashlib.md5(
                (source + str(start_idx + i)).encode()
            ).hexdigest()[:12]
            existing.append({
                "id": chunk_id,
                "text": chunk,
                "source": source,
                "idx": start_idx + i,
            })

        _save_chunks(collection, existing)

        if ollama_online:
            if existing_embs is not None and len(existing_embs) > 0:
                combined = np.vstack([existing_embs, new_embs]) if new_embs.ndim > 1 else np.vstack([existing_embs, new_embs.reshape(1, -1)])
            else:
                combined = new_embs
            _save_embeddings(collection, combined)

    return {
        "chunks_count": len(chunks),
        "embedding_status": "online" if ollama_online else "offline",
        "fallback": "none" if ollama_online else "keyword (BM25)",
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
        raise FileNotFoundError("文件不存在: %s" % filepath)

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

    # ─── Strategy 1: Vector search ───
    if embeddings is not None and len(embeddings) > 0 and len(embeddings) >= len(chunks):
        try:
            query_emb = _embed([query_text])
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
    """List all collections."""
    os.makedirs(RAG_DIR, exist_ok=True)
    with _lock:
        return sorted([
            d for d in os.listdir(RAG_DIR)
            if os.path.isdir(os.path.join(RAG_DIR, d))
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
    """Get info about a collection."""
    chunks = _load_chunks(collection)
    sources = set()
    total_chars = 0
    for c in chunks:
        sources.add(c.get("source", "unknown"))
        total_chars += len(c.get("text", ""))
    return {
        "name": collection,
        "chunks": len(chunks),
        "sources": sorted(sources),
        "total_chars": total_chars,
        "doc_count": len(sources),
        "embedding_available": _load_embeddings(collection) is not None,
    }
