"""Report Store — persist agent-generated reports as Markdown files.

Each report is saved as a .md file with JSON metadata index.
Storage: ~/.agent-harness/reports/
"""

import json
import os
import time
from pathlib import Path
from typing import Optional

REPORTS_DIR = Path(os.environ.get(
    "HARNESS_REPORTS_DIR",
    os.path.expanduser("~/.agent-harness/reports"),
))
INDEX_FILE = REPORTS_DIR / "index.json"


def _ensure():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _load_index() -> list[dict]:
    _ensure()
    if INDEX_FILE.exists():
        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_index(index: list[dict]):
    _ensure()
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def save_report(title: str, content: str, tags: list[str] = None, source_session: str = "") -> dict:
    """Save a report to disk.

    Args:
        title: Report title
        content: Markdown content
        tags: Optional tags for categorization
        source_session: Session ID that generated this report

    Returns:
        Report metadata dict
    """
    _ensure()
    timestamp = int(time.time())
    report_id = "rpt_%d_%s" % (timestamp, _slugify(title)[:20])
    filename = "%s.md" % report_id
    filepath = REPORTS_DIR / filename

    # Write markdown file
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("---\n")
        f.write("title: %s\n" % title)
        f.write("created: %d\n" % timestamp)
        f.write("tags: %s\n" % json.dumps(tags or [], ensure_ascii=False))
        f.write("---\n\n")
        f.write(content)

    # Update index
    meta = {
        "id": report_id,
        "title": title,
        "created_at": timestamp,
        "tags": tags or [],
        "source_session": source_session,
        "filename": filename,
        "chars": len(content),
    }
    index = _load_index()
    index.insert(0, meta)
    _save_index(index)

    return meta


def list_reports(limit: int = 50, offset: int = 0) -> list[dict]:
    """List reports, newest first."""
    index = _load_index()
    return index[offset:offset + limit]


def get_report(report_id: str) -> Optional[dict]:
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
    index = _load_index()
    for i, meta in enumerate(index):
        if meta["id"] == report_id:
            # Delete file
            filepath = REPORTS_DIR / meta["filename"]
            if filepath.exists():
                filepath.unlink()
            # Remove from index
            index.pop(i)
            _save_index(index)
            return True
    return False


def search_reports(query: str) -> list[dict]:
    """Search reports by title and tags."""
    q = query.lower()
    index = _load_index()
    results = []
    for meta in index:
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
