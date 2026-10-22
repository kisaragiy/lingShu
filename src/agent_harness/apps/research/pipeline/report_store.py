"""Report Store — persist agent-generated reports as Markdown files.

Each report is saved as a .md file with JSON metadata index.
Thread-safe with file lock.
Storage: ~/.agent-harness/reports/
Supports owner_id for multi-user isolation.
"""

import contextlib
import json
import os
import sys
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
_local = threading.local()


def _ensure():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)


@contextlib.contextmanager
def _file_lock():
    """跨进程排他锁（同一线程可重入）。

    进程内的 `_lock`（RLock）只挡同进程并发。server 与 CLI 同时写、或多 worker
    部署时，两个进程各自 load→modify→_save_index 会互相覆盖（后写者赢，前者的
    报告从索引里消失——文件还在，但 list/get 都找不到）。

    可重入靠线程局部深度计数：外层已持锁时，内层 `_save_index` 直接放行，
    否则同线程二次 msvcrt.locking 会自死锁。
    """
    depth = getattr(_local, "depth", 0)
    if depth:
        _local.depth = depth + 1
        try:
            yield
        finally:
            _local.depth = depth + 1 - 1
        return

    _ensure()
    fh = open(REPORTS_DIR / ".index.lock", "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            while True:
                try:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.02)   # LK_LOCK 自带约 10s 重试，这里兜住极端争抢
            _local.depth = 1
            try:
                yield
            finally:
                _local.depth = 0
                try:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            _local.depth = 1
            try:
                yield
            finally:
                _local.depth = 0
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


def _quarantine_index(exc: Exception) -> None:
    """索引读不出来时先隔离坏文件，再按空索引继续。

    原实现损坏就 return []，而下一次保存会把空列表写回 → 历史索引永久消失
    （报告 .md 还在，但全部从列表里消失，且没有任何痕迹）。隔离后至少留证。
    """
    try:
        bad = INDEX_FILE.with_name(f"index.json.corrupt-{int(time.time())}")
        os.replace(INDEX_FILE, bad)
        print(f"[report_store] 索引文件损坏（{exc}），已隔离为 {bad.name}，按空索引继续",
              file=sys.stderr)
    except OSError as e:
        print(f"[report_store] 索引损坏（{exc}）且隔离失败（{e}），按空索引继续", file=sys.stderr)


def _load_index() -> list[dict]:
    _ensure()
    if not INDEX_FILE.exists():
        return []
    try:
        with _lock, open(INDEX_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        _quarantine_index(exc)
        return []
    if not isinstance(data, list):
        _quarantine_index(ValueError(f"索引顶层应为列表，实际是 {type(data).__name__}"))
        return []
    return data


def _save_index(index: list[dict]):
    """原子写：tmp + os.replace。

    直接 open(INDEX_FILE, "w") 会先截断文件，此刻崩溃/断电 → 索引只剩半截 JSON
    （或空文件）→ 下次读取当空索引 → 所有报告从列表里消失。
    """
    _ensure()
    tmp = INDEX_FILE.with_suffix(".json.tmp")
    with _lock, _file_lock():
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(index, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, INDEX_FILE)
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise


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

    # Write markdown file（原子：tmp + replace，避免留下半截报告）
    content.encode("utf-8")
    tmp_md = filepath.with_suffix(".md.tmp")
    with _lock, _file_lock():
        with open(tmp_md, "w", encoding="utf-8") as f:
            f.write("---\n")
            f.write(f"title: {title}\n")
            f.write(f"created: {timestamp}\n")
            f.write(f"tags: {json.dumps(tags or [], ensure_ascii=False)}\n")
            f.write(f"owner_id: {owner_id}\n")
            f.write("---\n\n")
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_md, filepath)

    # Update index — 跨进程锁内做 load→modify→save，否则并发写会互相覆盖
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
    with _lock, _file_lock():
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
    with _lock, _file_lock():
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
    with _lock, _file_lock():
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
