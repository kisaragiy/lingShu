"""RAG Vector Store — local embedding + similarity search for document knowledge base.

Uses Ollama nomic-embed-text for embeddings, JSON+NPY for storage.
Supports PDF, DOCX, TXT, and Markdown files.

Usage:
    from agent_harness.tools.rag_store import index_file, query, list_collections, delete_collection

    # Index a file
    count = index_file("path/to/doc.pdf", collection="my_kb")

    # Query
    results = query("What is this document about?", collection="my_kb", top_k=5)

    # List
    cols = list_collections()

    # Delete
    delete_collection("my_kb")
"""

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Optional

import numpy as np

# ─── Config ───

# Store RAG data under the package directory by default
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


# ─── Internal helpers ───

def _coll_dir(collection: str) -> str:
    d = os.path.join(RAG_DIR, collection)
    os.makedirs(d, exist_ok=True)
    return d


def _chunks_path(collection: str) -> str:
    return os.path.join(_coll_dir(collection), "chunks.json")


def _embs_path(collection: str) -> str:
    return os.path.join(_coll_dir(collection), "embeddings.npy")


def _load_chunks(collection: str) -> list[dict]:
    p = _chunks_path(collection)
    if os.path.isfile(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_chunks(collection: str, chunks: list[dict]):
    p = _chunks_path(collection)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)


def _load_embeddings(collection: str) -> Optional[np.ndarray]:
    p = _embs_path(collection)
    if os.path.isfile(p):
        return np.load(p)
    return None


def _save_embeddings(collection: str, embs: np.ndarray):
    np.save(_embs_path(collection), embs)


# ─── Embedding ───

def _embed(texts: list[str]) -> np.ndarray:
    """Generate embeddings using Ollama nomic-embed-text.
    
    Returns all-zero vectors if Ollama is unreachable (caller falls back to keyword search).
    """
    import requests as req_lib

    # Quick connectivity check
    base_url = OLLAMA_API.replace("/api/embed", "")
    try:
        req_lib.get(base_url, timeout=2)
    except Exception:
        return np.zeros((len(texts), EMBED_DIM), dtype=np.float32)

    embeddings = []
    for text in texts:
        try:
            r = req_lib.post(
                OLLAMA_API,
                json={"model": EMBED_MODEL, "input": text},
                timeout=10,
            )
            if r.status_code == 200:
                emb = np.array(r.json()["embeddings"][0], dtype=np.float32)
                embeddings.append(emb)
            else:
                embeddings.append(np.zeros(EMBED_DIM, dtype=np.float32))
        except Exception:
            embeddings.append(np.zeros(EMBED_DIM, dtype=np.float32))
    return np.array(embeddings)


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
        # Fallback: fixed-size chunks
        chunks = [
            text[i : i + chunk_size]
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
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ImportError("pypdf not installed. Run: pip install pypdf")
        reader = PdfReader(filepath)
        pages = []
        for page in reader.pages:
            text = page.extract_text() or ""
            pages.append(text)
        return "\n\n".join(pages)

    elif ext == ".docx":
        try:
            from docx import Document
        except ImportError:
            raise ImportError(
                "python-docx not installed. Run: pip install python-docx"
            )
        doc = Document(filepath)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        return "\n".join(paragraphs)

    else:
        raise ValueError(f"Unsupported file type: {ext}. Supported: {SUPPORTED_EXTENSIONS}")


# ─── Public API ───

def index(text: str, source: str, collection: str = "default") -> int:
    """Index text into vector store. Returns number of chunks indexed."""
    chunks = _chunk_text(text)
    existing = _load_chunks(collection)
    existing_embs = _load_embeddings(collection)

    # Generate embeddings for new chunks
    new_embs = _embed(chunks)

    # If embeddings are all zeros (Ollama offline), still store chunks for keyword search
    if np.all(new_embs == 0):
        new_embs = np.zeros((len(chunks), EMBED_DIM), dtype=np.float32)

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

    if existing_embs is not None and len(existing_embs) > 0:
        combined = (
            np.vstack([existing_embs, new_embs])
            if new_embs.ndim > 1
            else np.vstack([existing_embs, new_embs.reshape(1, -1)])
        )
    else:
        combined = new_embs
    _save_embeddings(collection, combined)

    return len(chunks)


def index_file(filepath: str, collection: str = "default", filename: str = "") -> int:
    """Parse and index a file. Returns number of chunks indexed.

    Args:
        filepath: Path to the file (PDF, DOCX, TXT, MD)
        collection: Collection name (default: "default")
        filename: Override display name (default: basename of filepath)

    Returns:
        Number of chunks indexed
    """
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    source = filename or os.path.basename(filepath)
    text = _parse_file(filepath)
    return index(text, source=source, collection=collection)


def query(
    query_text: str, collection: str = "default", top_k: int = 5
) -> list[dict]:
    """Search for most relevant chunks.

    Uses vector search (cosine similarity) when Ollama is available.
    Falls back to keyword search when offline.

    Args:
        query_text: The search query
        collection: Collection to search
        top_k: Max results

    Returns:
        List of {"text": str, "source": str, "score": float, "id": str}
    """
    chunks = _load_chunks(collection)
    embeddings = _load_embeddings(collection)
    if not chunks:
        return []

    # Try vector search first
    try:
        query_emb = _embed([query_text])
        # Check if embeddings look valid (not all zeros)
        if embeddings is not None and len(embeddings) > 0 and len(chunks) <= len(embeddings):
            if query_emb.ndim == 2:
                query_emb = query_emb[0]
            # If query_emb is all zeros, Ollama is down
            if np.any(query_emb != 0):
                scores = []
                for i in range(len(chunks)):
                    score = _cosine_similarity(query_emb, embeddings[i])
                    scores.append((score, i))
                scores.sort(key=lambda x: x[0], reverse=True)
                results = []
                for score, idx in scores[:top_k]:
                    if score > 0.1:
                        chunk = chunks[idx]
                        results.append({
                            "text": chunk["text"][:1000],
                            "source": chunk.get("source", ""),
                            "score": round(score, 4),
                            "id": chunk.get("id", ""),
                        })
                if results:
                    return results
    except Exception:
        pass

    # Fallback: substring matching (works for Chinese and English)
    query_lower = query_text.lower()
    query_chars = set(c for c in query_lower if c.strip())  # non-whitespace chars
    scored = []
    for i, chunk in enumerate(chunks):
        text = chunk.get("text", "").lower()
        # Count character overlap (handles Chinese where words aren't space-separated)
        char_matches = sum(1 for c in query_chars if c in text)
        if char_matches > 0:
            score = char_matches / max(len(query_chars), 1)
            scored.append((score, i))
    scored.sort(key=lambda x: x[0], reverse=True)
    results = []
    for score, idx in scored[:top_k]:
        chunk = chunks[idx]
        results.append({
            "text": chunk["text"][:1000],
            "source": chunk.get("source", ""),
            "score": round(score, 4),
            "id": chunk.get("id", ""),
        })
    return results


def list_collections() -> list[str]:
    """List all collections."""
    os.makedirs(RAG_DIR, exist_ok=True)
    return sorted([
        d for d in os.listdir(RAG_DIR)
        if os.path.isdir(os.path.join(RAG_DIR, d))
    ])


def delete_collection(collection: str) -> bool:
    """Delete a collection and all its data."""
    import shutil
    d = _coll_dir(collection)
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
    }
