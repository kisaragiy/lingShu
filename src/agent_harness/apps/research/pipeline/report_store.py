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

_lock = threading.Lock()


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
                 owner_id: str | None = None) -> list[dict]:
    """List reports, newest first.

    Args:
        limit: Max reports to return
        offset: Pagination offset
        owner_id: If set, only return reports owned by this user.
                  If None, return all (admin view).

    Returns:
        List of report metadata dicts
    """
    index = _load_index()
    if owner_id is not None:
        index = [m for m in index if m.get("owner_id", "") == owner_id]
    return index[offset:offset + limit]


def get_report(report_id: str) -> dict | None:
    """Get a report's metadata and content."""
    index = _load_index()
    for meta in index:
        if meta["id"] == report_id:
            filepath = REPORTS_DIR / meta["filename"]
            if filepath.exists():
                content = filepath.read_text(encoding="utf-8")
                return {**meta, "content": content}
            return {**meta, "content": ""}
    return None


def delete_report(report_id: str) -> bool:
    """Delete a report and remove from index."""
    with _lock:
        index = _load_index()
        for i, meta in enumerate(index):
            if meta["id"] == report_id:
                filepath = REPORTS_DIR / meta["filename"]
                if filepath.exists():
                    filepath.unlink()
                index.pop(i)
                _save_index(index)
                return True
    return False


def search_reports(query: str, owner_id: str | None = None) -> list[dict]:
    """Search reports by title and tags.

    Args:
        query: Search string
        owner_id: If set, only search this user's reports.

    Returns:
        List of matching report metadata dicts
    """
    q = query.lower()
    index = _load_index()
    results = []
    for meta in index:
        # Filter by owner
        if owner_id is not None and meta.get("owner_id", "") != owner_id:
            continue
        if q in meta.get("title", "").lower():
            results.append(meta)
            continue
        for tag in meta.get("tags", []):
            if q in tag.lower():
                results.append(meta)
                break
    return results


def _slugify(text: str) -> str:
    """Create a safe filename slug from text."""
    import re
    text = text.lower()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[-\s]+', '_', text)
    return text.strip('_')[:30]
