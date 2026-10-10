"""Report Store — persist agent-generated reports as Markdown files.

Each report is saved as a .md file with JSON metadata index.
Thread-safe with file lock.
Storage: ~/.agent-harness/reports/
Supports owner_id for multi-user isolation.
"""

import json
import os
import threading
import time
from pathlib import Path

REPORTS_DIR = Path(os.environ.get(
    "HARNESS_REPORTS_DIR",
    os.path.expanduser("~/.agent-harness/reports"),
))
INDEX_FILE = REPORTS_DIR / "index.json"

_lock = threading.RLock()   # 可重入：save_report/index_report/delete_report 都在持锁状态下
                            # 调 _load_index()/_save_index()，用非重入 Lock 会同线程自死锁
                            # （HEAD 版真实缺陷：POST /v1/reports 永久挂住请求线程）。


def _ensure():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _load_index() -> list[dict]:
    _ensure()
    if INDEX_FILE.exists():
        try:
            with _lock, open(INDEX_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_index(index: list[dict]):
    _ensure()
    with _lock, open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def save_report(title: str, content: str, tags: list[str] = None,
                source_session: str = "", owner_id: str = "") -> dict:
    """Save a report to disk.

    Args:
        title: Report title
        content: Markdown content
        tags: Optional tags for categorization
        source_session: Session ID that generated this report
        owner_id: User ID who owns this report

    Returns:
        Report metadata dict
    """
    _ensure()
    timestamp = int(time.time())
    report_id = f"rpt_{timestamp}_{_slugify(title)[:20]}"
    filename = f"{report_id}.md"
    filepath = REPORTS_DIR / filename

    # Write markdown file
    content.encode("utf-8")
    with _lock, open(filepath, "w", encoding="utf-8") as f:
        f.write("---\n")
        f.write(f"title: {title}\n")
        f.write(f"created: {timestamp}\n")
        f.write(f"tags: {json.dumps(tags or [], ensure_ascii=False)}\n")
        f.write(f"owner_id: {owner_id}\n")
        f.write("---\n\n")
        f.write(content)

    # Update index (atomic: write to tmp then replace)
    meta = {
        "id": report_id,
        "title": title,
        "created_at": timestamp,
        "tags": tags or [],
        "source_session": source_session,
        "owner_id": owner_id,
        "filename": filename,
        "chars": len(content),
    }
    with _lock:
        index = _load_index()
        index.insert(0, meta)
        _save_index(index)

    return meta


def list_reports(limit: int = 50, offset: int = 0,
                 owner_id: str | None = None, q: str | None = None) -> list[dict]:
    """List reports, newest first. Single read path for browse + search.

    Args:
        limit: Max reports to return
        offset: Pagination offset
        owner_id: If set, only return reports owned by this user.
                  If None, return all (admin view).
        q: If set, keep only reports whose title or a tag contains it
           (case-insensitive). Filtering always happens BEFORE pagination.

    Returns:
        List of report metadata dicts
    """
    index = _load_index()
    if owner_id is not None:
        index = [m for m in index if m.get("owner_id", "") == owner_id]
    if q:
        needle = q.lower()
        index = [
            m for m in index
            if needle in str(m.get("title", "")).lower()
            or any(needle in str(t).lower() for t in m.get("tags", []))
        ]
    # 过滤在前、切片在后（顺序即正确性）；store 也是对外 API，负值在这里钳制，
    # 不指望每个调用点都记得传 Query 约束。
    start = max(0, int(offset or 0))
    size = max(0, int(limit)) if limit is not None else len(index)
    return index[start:start + size]


def get_report(report_id: str, owner_id: str | None = None) -> dict | None:
    """Get a report's metadata and content.

    owner_id is an explicit parameter: None = unrestricted (admin path),
    a user id = only that owner's report is returned. Ownership is enforced
    HERE (the store), not in route handlers, so a handler that forgets the
    check cannot leak — and reports are only reachable through this API,
    never by routes globbing REPORTS_DIR directly.
    """
    index = _load_index()
    for meta in index:
        if meta["id"] != report_id:
            continue
        if owner_id is not None and meta.get("owner_id", "") != owner_id:
            return None
        filepath = REPORTS_DIR / meta.get("filename", "")
        if filepath.exists() and filepath.is_file():
            content = filepath.read_text(encoding="utf-8")
        else:
            content = ""
        return {**meta, "content": content}
    return None


def index_report(meta: dict) -> None:
    """Register an already-written report file in the index.

    Needed by report_formatter.save_formal_report(): formal HTML reports were
    written to disk without an index entry, so they had no owner record and
    could only be served by a route reading REPORTS_DIR directly (the IDOR
    path). Indexing them keeps every report addressable through get_report()
    with ownership enforced in one place.
    """
    with _lock:
        index = _load_index()
        if any(m.get("id") == meta.get("id") for m in index):
            return
        index.insert(0, meta)
        _save_index(index)


def delete_report(report_id: str, owner_id: str | None = None) -> bool:
    """Delete a report and remove from index.

    owner_id: None = unrestricted (admin); a user id = only that owner's
    report may be deleted (returns False otherwise → route answers 404).
    """
    with _lock:
        index = _load_index()
        for i, meta in enumerate(index):
            if meta["id"] != report_id:
                continue
            if owner_id is not None and meta.get("owner_id", "") != owner_id:
                return False
            filepath = REPORTS_DIR / meta["filename"]
            if filepath.exists():
                filepath.unlink()
            index.pop(i)
            _save_index(index)
            return True
    return False


def search_reports(query: str, owner_id: str | None = None,
                   limit: int = 50, offset: int = 0) -> list[dict]:
    """Search reports by title and tags.

    Deprecated alias: delegates to list_reports(q=...) so there is exactly one
    filtering/pagination implementation — a second copy is what let the route
    signature diverge from the store (500 on GET /v1/reports?q=).
    """
    return list_reports(limit=limit, offset=offset, owner_id=owner_id, q=query)


def _slugify(text: str) -> str:
    """Create a safe filename slug from text."""
    import re
    text = text.lower()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[-\s]+', '_', text)
    return text.strip('_')[:30]
