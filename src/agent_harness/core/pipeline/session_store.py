"""Session Store — 事件源持久化（dsh P1 映射落地）。

设计（对应 docs/dsh-patterns-implementation-map.md 3.2）:
- 每 session 两个文件（分隔 meta 与消息，避免高频 meta 重写消息日志）:
    * {id}.log        JSONL 追加式事件日志（消息事件，append-only，seq 单调）
    * {id}.meta.json  会话元数据（title/pinned/counts/owner），原子写
- 投影层: 事件日志 → 模型可见消息数组。增量缓存 (SESSION_DIR, sid) → (last_seq, byte_offset, messages)，
  每次只读字节偏移后的新事件，O(new) 而非 O(all)。
- 公开 API 签名与旧版完全一致（save/load/list/search/delete/update_meta/count/clean/init），
  调用方无需改动。N4 兼容层: 旧版 {id}.json 一次性迁移成 .log + .meta.json。

事件类型:
    message: {seq, ts, type, surface_op:"append", role, content, ...其它消息字段}
    replace: {seq, ts, type, surface_op:"replace", messages:[完整快照]}  # 结构变更/压缩

不变式: model-visible means logged —— 投影出的每条消息必须能从日志重建。
"""
import contextlib
import json
import os
import threading
import time

# ─── Config ───

DEFAULT_DIR = os.path.join(os.path.expanduser("~"), ".agent-harness", "sessions")
SESSION_DIR = os.environ.get("HARNESS_SESSION_DIR", DEFAULT_DIR)

SESSION_TTL = int(os.environ.get("HARNESS_SESSION_TTL", str(7 * 24 * 3600)))  # 7 天

# Reentrant lock — covers all file operations (reads + writes)
_lock = threading.RLock()

# Incremental projection cache: (SESSION_DIR|sid) -> (last_seq, byte_offset, messages)
_proj_cache: dict[str, tuple[int, int, list[dict]]] = {}

# Session metadata keys preserved across saves (user-visible)
_META_KEYS = ("title", "pinned")


# ─── Path helpers ───

def _safe_id(session_id: str) -> str:
    return session_id.replace("/", "_").replace("\\", "_").replace("..", "_")


def _session_path(session_id: str) -> str:
    return os.path.join(SESSION_DIR, f"{_safe_id(session_id)}.json")


def _log_path(session_id: str) -> str:
    return os.path.join(SESSION_DIR, f"{_safe_id(session_id)}.log")


def _meta_path(session_id: str) -> str:
    return os.path.join(SESSION_DIR, f"{_safe_id(session_id)}.meta.json")


# ─── Low-level file ops ───

def _read_events(path: str) -> list[dict]:
    """Read all valid JSONL events from a log file. Skips torn/malformed trailing lines."""
    if not os.path.exists(path):
        return []
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                # 撕尾行（写一半被杀）——跳过，不当作损坏
                continue
    return events


def _append_events(path: str, events: list[dict]) -> None:
    """Atomically append events to the log (single write + flush)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events)
    with open(path, "a", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def _read_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _write_json_atomic(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ─── Projection ───

def _project_events(events: list[dict]) -> list[dict]:
    """Replay events into the model-visible message list."""
    messages: list[dict] = []
    for ev in events:
        t = ev.get("type")
        if t == "message":
            # 还原消息字段（去掉事件簿记字段；保留 ts，它是消息自带时间戳）
            msg = {k: v for k, v in ev.items() if k not in ("seq", "type", "surface_op")}
            messages.append(msg)
        elif t == "replace":
            messages = list(ev.get("messages", []))
    return messages


def _read_from_offset(path: str, byte_offset: int) -> list[dict]:
    """Read only the events appended after a byte offset (O(new)). Skips torn lines."""
    if byte_offset < 0:
        return []
    events: list[dict] = []
    if not os.path.exists(path):
        return events
    with open(path, "rb") as f:
        f.seek(byte_offset)
        for raw in f:
            line = raw.decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _project_cached(session_id: str) -> tuple[list[dict], int]:
    """Incremental projection using byte-offset cache. Returns (messages, last_seq)."""
    path = _log_path(session_id)
    key = f"{SESSION_DIR}|{session_id}"
    cached = _proj_cache.get(key)

    if not os.path.exists(path):
        if cached is not None:
            _proj_cache.pop(key, None)
        return [], -1

    if cached is None:
        events = _read_events(path)
        messages = _project_events(events)
        last_seq = messages_to_last_seq(events)
        _proj_cache[key] = (last_seq, os.path.getsize(path), messages)
        return messages, last_seq

    last_seq, byte_offset, messages = cached
    new_events = _read_from_offset(path, byte_offset)
    if new_events:
        # 顺序 replay：message 追加，replace 快照重建
        for ev in new_events:
            if ev.get("type") == "replace":
                messages = list(ev.get("messages", []))
            elif ev.get("type") == "message":
                msg = {k: v for k, v in ev.items() if k not in ("seq", "type", "surface_op")}
                messages = messages + [msg]
        last_seq = max(last_seq, messages_to_last_seq(new_events))
        byte_offset = os.path.getsize(path)
        _proj_cache[key] = (last_seq, byte_offset, messages)

    return messages, last_seq


def messages_to_last_seq(events: list[dict]) -> int:
    return max((e.get("seq", -1) for e in events), default=-1)


def project_from_seq(session_id: str, from_seq: int) -> list[dict]:
    """Return messages contributed by events with seq >= from_seq (checkpoint delta)."""
    events = _read_events(_log_path(session_id))
    tail = [e for e in events if e.get("seq", 0) >= from_seq]
    return _project_events(tail)


# ─── Event builders ───

def _message_event(msg: dict, seq: int, ts: float | None = None) -> dict:
    ev = {
        "seq": seq,
        "ts": ts or msg.get("ts") or time.time(),
        "type": "message",
        "surface_op": "append",
    }
    for k, v in msg.items():
        if k not in ("seq", "ts", "type", "surface_op"):
            ev[k] = v
    return ev


def _replace_event(messages: list[dict], seq: int) -> dict:
    return {"seq": seq, "ts": time.time(), "type": "replace",
            "surface_op": "replace", "messages": list(messages)}


# ─── Meta helpers ───

def _load_meta(session_id: str) -> dict:
    return _read_json(_meta_path(session_id)) or {}


def _now() -> float:
    return time.time()


# ─── Public API ───

def init_store():
    os.makedirs(SESSION_DIR, exist_ok=True)
    _migrate_legacy()
    clean_expired()


def save_session(session_id: str, messages: list[dict], owner_id: str = ""):
    """Save a session's messages (event-sourced). Preserves title/pinned from meta."""
    if not messages:
        return
    log_path = _log_path(session_id)
    meta_path = _meta_path(session_id)

    with _lock:
        current, last_seq = _project_cached(session_id)

        # 决定追加哪些事件
        if messages == current:
            events_to_append = []  # 幂等，不重复 append
        elif not current or (len(messages) > len(current) and messages[:len(current)] == current):
            # 尾部延伸（或首次写）→ 只追加新增消息为 message 事件
            start = len(current)
            events_to_append = [
                _message_event(messages[i], last_seq + 1 + (i - start))
                for i in range(start, len(messages))
            ]
        else:
            # 结构变更 → 单个 replace 快照事件
            events_to_append = [_replace_event(messages, last_seq + 1)]

        if events_to_append:
            _append_events(log_path, events_to_append)
            # append-only 下直接更新投影缓存（字节偏移递增）→ 下次 load 走 O(new) 增量
            _proj_cache[f"{SESSION_DIR}|{session_id}"] = (
                last_seq + len(events_to_append),
                os.path.getsize(log_path),
                list(messages),
            )

        # Meta: 分离存储，原子写，保留 title/pinned
        now = _now()
        meta = _load_meta(session_id)
        meta.update({
            "session_id": session_id,
            "title": meta.get("title", ""),
            "pinned": meta.get("pinned", False),
            "updated_at": now,
            "created_at": meta.get("created_at", messages[0].get("ts", now)),
            "message_count": len(messages),
            "exchanges": len(messages) // 2,
            "last_preview": messages[-1].get("content", "")[:120],
            "owner_id": owner_id,
        })
        _write_json_atomic(meta_path, meta)


def load_session(session_id: str) -> list[dict] | None:
    with _lock:
        if not os.path.exists(_log_path(session_id)):
            return None
        messages, _last_seq = _project_cached(session_id)
        return messages


def list_sessions(owner_id: str | None = None) -> list[dict]:
    if not os.path.isdir(SESSION_DIR):
        return []
    now = _now()
    sessions = []
    with _lock:
        for fname in os.listdir(SESSION_DIR):
            if not fname.endswith(".meta.json"):
                continue
            sid = fname[: -len(".meta.json")]
            meta = _read_json(os.path.join(SESSION_DIR, fname))
            if not meta:
                continue
            age = now - meta.get("updated_at", 0)
            if age > SESSION_TTL:
                with contextlib.suppress(OSError):
                    os.unlink(os.path.join(SESSION_DIR, fname))
                    with contextlib.suppress(OSError):
                        os.unlink(_log_path(sid))
                continue
            if owner_id is not None and meta.get("owner_id", "") != owner_id:
                continue
            sessions.append({
                "id": meta.get("session_id", sid),
                "title": meta.get("title", ""),
                "pinned": meta.get("pinned", False),
                "exchanges": meta.get("exchanges", 0),
                "message_count": meta.get("message_count", 0),
                "created_at": meta.get("created_at", 0),
                "updated_at": meta.get("updated_at", 0),
                "last_active": int(age),
                "preview": meta.get("last_preview", ""),
                "owner_id": meta.get("owner_id", ""),
            })
    sessions.sort(key=lambda s: (not s.get("pinned", False), -s.get("updated_at", 0)))
    return sessions


def delete_session(session_id: str) -> bool:
    locked = False
    for p in (_log_path(session_id), _meta_path(session_id), _session_path(session_id)):
        try:
            with _lock:
                os.unlink(p)
        except FileNotFoundError:
            pass
        else:
            locked = True
    return locked


def clean_expired() -> int:
    if not os.path.isdir(SESSION_DIR):
        return 0
    now = _now()
    count = 0
    with _lock:
        for fname in os.listdir(SESSION_DIR):
            if not fname.endswith(".meta.json"):
                continue
            sid = fname[: -len(".meta.json")]
            meta = _read_json(os.path.join(SESSION_DIR, fname))
            if not meta:
                continue
            age = now - meta.get("updated_at", 0)
            if age > SESSION_TTL:
                with contextlib.suppress(OSError):
                    os.unlink(os.path.join(SESSION_DIR, fname))
                with contextlib.suppress(OSError):
                    os.unlink(_log_path(sid))
                count += 1
    return count


def session_count() -> int:
    return len(list_sessions())


def get_session_summary(session_id: str) -> dict | None:
    meta = _load_meta(session_id)
    if not meta:
        return None
    return {
        "id": meta.get("session_id", session_id),
        "title": meta.get("title", ""),
        "pinned": meta.get("pinned", False),
        "exchanges": meta.get("exchanges", 0),
        "message_count": meta.get("message_count", 0),
    }


def update_session_meta(session_id: str, **kwargs) -> dict | None:
    """Update metadata (title, pinned) without touching the message log (atomic meta write)."""
    meta_path = _meta_path(session_id)
    with _lock:
        meta = _read_json(meta_path)
        if meta is None:
            return None
        for k in ("title", "pinned"):
            if k in kwargs:
                meta[k] = kwargs[k]
        _write_json_atomic(meta_path, meta)
    return {"id": meta.get("session_id", session_id),
            "title": meta.get("title", ""),
            "pinned": meta.get("pinned", False),
            "exchanges": meta.get("exchanges", 0),
            "message_count": meta.get("message_count", 0)}


def search_messages(query: str, owner_id: str | None = None, limit: int = 20) -> list[dict]:
    q = query.lower()
    results = []
    with _lock:
        sessions = list_sessions(owner_id=owner_id)
        for s in sessions:
            sid = s["id"]
            msgs = load_session(sid) or []
            for msg in msgs:
                content = msg.get("content", "")
                if q in content.lower():
                    ts = msg.get("ts", 0)
                    results.append({
                        "session_id": sid,
                        "session_title": s.get("title", "") or s.get("preview", ""),
                        "role": msg.get("role", ""),
                        "content_preview": content[:200],
                        "ts": ts,
                        "time": time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "",
                    })
                    if len(results) >= limit:
                        break
            if len(results) >= limit:
                break
    results.sort(key=lambda r: r.get("ts", 0), reverse=True)
    return results[:limit]


# ─── Legacy migration (N4) ───

def _migrate_legacy() -> int:
    """Migrate old {id}.json sessions → .log (events) + .meta.json. Returns migrated count."""
    if not os.path.isdir(SESSION_DIR):
        return 0
    count = 0
    with _lock:
        for fname in os.listdir(SESSION_DIR):
            # 只处理旧格式 {id}.json（排除 .meta.json、.log、.tmp、已迁移备份）
            if not fname.endswith(".json") or fname.endswith(".meta.json") or fname.endswith(".tmp"):
                continue
            sid = fname[: -len(".json")]
            # 已有事件日志 → 不再迁移，可能是新格式
            if os.path.exists(_log_path(sid)):
                continue
            old_path = os.path.join(SESSION_DIR, fname)
            legacy = _read_json(old_path)
            if not legacy or not isinstance(legacy.get("messages"), list):
                continue
            messages = legacy["messages"]
            # 写成 meta 文件
            meta = {
                "session_id": sid,
                "title": legacy.get("title", ""),
                "pinned": legacy.get("pinned", False),
                # 迁移视为激活一次：updated_at 用 now，避免紧接着 clean_expired 判过期删除
                "updated_at": _now(),
                "created_at": legacy.get("created_at", messages[0].get("ts") if messages else _now()),
                "message_count": len(messages),
                "exchanges": legacy.get("exchanges", len(messages) // 2),
                "last_preview": messages[-1].get("content", "")[:120] if messages else "",
                "owner_id": legacy.get("owner_id", ""),
            }
            _write_json_atomic(_meta_path(sid), meta)
            # 写成事件日志
            events = [
                _message_event(messages[i], i, messages[i].get("ts"))
                for i in range(len(messages))
            ]
            _append_events(_log_path(sid), events)
            # 旧 JSON 备份为 .json.migrated（保留数据，可回滚）
            with contextlib.suppress(OSError):
                os.replace(old_path, old_path + ".migrated")
            count += 1
    return count
