"""
Misc tools — think, files, code, RAG, finance, stock, permissions
"""
import json
import os
import time

from ..pipeline.llm import _session, HARNESS_DIR, WORKSPACE_DIR, call_llama, call_llama_censored, is_censored_content, _post_cloud

from .registry import register_tool


# ==================== 路径安全解析 ====================

def _resolve_workspace_path(path: str) -> str:
    """将相对路径解析为 workspace 内绝对路径，禁止逃逸"""
    path = path.replace("\\", "/").lstrip("/")
    if not path or ".." in path.split("/"):
        raise ValueError("路径无效或不允许包含 ..")
    full = os.path.normpath(os.path.join(WORKSPACE_DIR, path))
    if not full.startswith(WORKSPACE_DIR):
        raise ValueError("路径超出 workspace 范围")
    return full


def _tool_file_read(path: str) -> str:
    try:
        full = _resolve_workspace_path(path)
    except ValueError as e:
        return f"[file_read] {e}"
    if not os.path.isfile(full):
        return f"[file_read] 文件不存在: {path}"
    with open(full, encoding="utf-8") as f:
        return f.read()


def _tool_file_write(path: str, content: str, mode: str = "append") -> str:
    try:
        full = _resolve_workspace_path(path)
    except ValueError as e:
        return f"[file_write] {e}"
    parent = os.path.dirname(full)
    if parent:
        os.makedirs(parent, exist_ok=True)
    write_mode = "w" if mode in ("overwrite", "w", "write") else "a"
    with open(full, write_mode, encoding="utf-8") as f:
        f.write(content)
    return f"已写入 {path} ({len(content)} 字符, mode={'overwrite' if write_mode == 'w' else 'append'})"


def _tool_think(prompt: str = "", **kwargs) -> str:
    text = prompt or kwargs.get("text") or kwargs.get("content") or kwargs.get("query") or kwargs.get("message") or ""
    if not text:
        return "[think] 缺少 prompt/text 参数"
    if is_censored_content(str(text)):
        return call_llama_censored([{"role": "user", "content": str(text)}])
    content, _ = call_llama([{"role": "user", "content": str(text)}])
    return content


def _tool_summarize(text: str, max_output: int = 500) -> str:
    """压缩长文本为精简摘要 — 减少下游 prompt token 消耗"""
    if len(text) < max_output:
        return text
    prompt = f"将以下文本压缩为{max_output}字以内的摘要，保留关键信息:\n\n{text[:6000]}"
    result, _ = call_llama([{"role": "user", "content": prompt}], system_prompt="你是文本压缩器，只输出摘要，不加评论。")
    return result.strip()[:max_output]


def _tool_code_execute(code: str) -> str:
    """执行 Python 代码并返回输出（沙箱模式：限定安全模块和 builtins）"""
    import io
    import sys as _sys
    import traceback as _tb
    import signal as _sig

    # ─── Restricted builtins ───
    _SAFE_BUILTINS = {
        'abs': abs, 'all': all, 'any': any, 'ascii': ascii, 'bin': bin,
        'bool': bool, 'bytearray': bytearray, 'bytes': bytes, 'callable': callable,
        'chr': chr, 'complex': complex, 'dict': dict, 'dir': dir, 'divmod': divmod,
        'enumerate': enumerate, 'filter': filter, 'float': float, 'format': format,
        'frozenset': frozenset, 'getattr': getattr, 'hasattr': hasattr,
        'hash': hash, 'hex': hex, 'id': id, 'int': int, 'isinstance': isinstance,
        'issubclass': issubclass, 'iter': iter, 'len': len, 'list': list,
        'map': map, 'max': max, 'min': min, 'next': next, 'object': object,
        'oct': oct, 'ord': ord, 'pow': pow, 'print': print, 'range': range,
        'repr': repr, 'reversed': reversed, 'round': round, 'set': set,
        'slice': slice, 'sorted': sorted, 'str': str, 'sum': sum,
        'tuple': tuple, 'type': type, 'zip': zip,
        'True': True, 'False': False, 'None': None,
    }

    # ─── Safe module whitelist (only these can be imported) ───
    _SAFE_MODULES = {
        'math', 'statistics', 'random', 'json', 'csv', 're', 'string',
        'collections', 'itertools', 'functools', 'datetime', 'time',
        'decimal', 'fractions', 'typing', 'dataclasses',
        'hashlib', 'base64', 'uuid', 'bisect', 'heapq',
        'textwrap', 'pprint', 'enum',
    }

    class _SafeImporter:
        """Custom import hook that only allows whitelisted modules."""
        def __init__(self, whitelist):
            self._whitelist = whitelist
        def find_spec(self, fullname, path, target=None):
            base = fullname.split('.')[0]
            if base in self._whitelist:
                return None  # Use default import
            raise ImportError("模块 '%s' 不在安全沙箱白名单中。只允许: %s" %
                              (fullname, ', '.join(sorted(self._whitelist))))

    # ─── Pre-scan for dangerous patterns ───
    _DANGEROUS_KEYWORDS = [
        '__import__', '__builtins__', '__subclass', '__base__',
        '__mro__', '__globals__', '__code__', '__closure__',
        'os.system', 'os.popen', 'subprocess', 'shutil',
        'ctypes', 'socket', 'win32api', 'multiprocessing',
        'threading', '_thread', 'importlib',
    ]
    for kw in _DANGEROUS_KEYWORDS:
        if kw in code:
            return "[code_execute] ⚠️ 安全沙箱拒绝: 代码包含危险关键字 '%s'" % kw

    # Capture stdout
    old_stdout = _sys.stdout
    redirected = io.StringIO()
    _sys.stdout = redirected

    # ─── Timeout (30s max) ───
    def _timeout_handler(signum, frame):
        raise TimeoutError("代码执行超时（30s）")

    try:
        compiled = compile(code.strip(), '<exec>', 'exec')

        # Set timeout on Windows
        old_alarm = None
        if hasattr(_sig, 'SIGALRM'):
            _sig.signal(_sig.SIGALRM, _timeout_handler)
            _sig.alarm(30)

        # Install safe importer
        safe_importer = _SafeImporter(_SAFE_MODULES)
        if 'sys' in _sys.modules:
            orig_meta_path = list(_sys.meta_path)
        _sys.meta_path.insert(0, safe_importer)

        safe_globals = {
            '__builtins__': _SAFE_BUILTINS,
            '__name__': '__sandbox__',
        }

        exec(compiled, safe_globals)

        # Clear alarm
        if hasattr(_sig, 'SIGALRM'):
            _sig.alarm(0)

        output = redirected.getvalue()
        if not output:
            return "[code_execute] 代码执行完成（无输出）"
        return output.strip()

    except TimeoutError:
        return "[code_execute] ⚠️ 代码执行超时（30s）"
    except Exception as e:
        return "[code_execute] 执行失败: %s\n%s" % (e, _tb.format_exc()[:200])
    finally:
        _sys.stdout = old_stdout
        # Restore original meta path
        if 'orig_meta_path' in dir():
            _sys.meta_path = orig_meta_path
        if hasattr(_sig, 'SIGALRM'):
            try:
                _sig.alarm(0)
            except (ValueError, OSError):
                pass


def _tool_github_issues(repo: str = "", state: str = "open", limit: int = 10) -> str:
    try:
        import sys
        skills_dir = os.path.join(os.path.dirname(HARNESS_DIR), "skills")
        if skills_dir not in sys.path:
            sys.path.insert(0, skills_dir)
        from github_connector import check_new_issues, list_issues
        if repo:
            issues = list_issues(repo=repo, state=state, limit=limit)
        else:
            result = check_new_issues(since_hours=48)
            return result.get("summary", str(result))
        if issues and "error" in issues[0]:
            return issues[0]["error"]
        lines = [f"#{i.get('number')} {i.get('title','')} [{i.get('url','')}]" for i in issues[:limit]]
        return "\n".join(lines) if lines else "无 open issues"
    except Exception as e:
        return f"[GitHub失败] {e}"


def _tool_datetime(format_str: str = "%Y-%m-%d %H:%M:%S") -> str:
    """获取当前动态时间"""
    from datetime import datetime
    try:
        return datetime.now().strftime(format_str)
    except ValueError:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tool_permission_gate(action: str = "", reason: str = "", confirm_code: str = "") -> str:
    """权限门 — 记录高风险操作并等待确认"""
    import uuid
    log_dir = os.path.join(WORKSPACE_DIR, "loop", "permissions")
    os.makedirs(log_dir, exist_ok=True)
    if confirm_code:
        path = os.path.join(log_dir, f"{confirm_code}.json")
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                record = json.load(f)
            record["confirmed_at"] = _now_iso()
            record["status"] = "confirmed"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
            return f"已确认操作: {record['action'][:100]}"
        return f"确认码无效: {confirm_code}"
    code = uuid.uuid4().hex[:8]
    record = {
        "code": code, "action": action, "reason": reason,
        "created_at": _now_iso(), "status": "pending",
    }
    with open(os.path.join(log_dir, f"{code}.json"), "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    return f"[需要确认] {action[:60]}\n原因: {reason[:80] if reason else '未提供'}\n回复 /confirm {code} 以继续"


def _tool_rag_query(query: str, collection: str = "default", top_k: int = 5) -> str:
    """本地 RAG 检索 — 从指定 collection 检索最相关片段"""
    try:
        from .rag_store import query as rag_q
        results = rag_q(query, collection, top_k)
        if not results:
            return json.dumps({"results": [], "hint": "collection 为空或不存在"}, ensure_ascii=False)
        return json.dumps({"results": results[:top_k]}, ensure_ascii=False)
    except ImportError as e:
        return json.dumps({"error": "rag_store 模块未安装", "hint": str(e)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def _tool_rag_index(text: str, source: str, collection: str = "default") -> str:
    """本地 RAG 索引 — 将文本切割为 chunks 并存入向量存储"""
    try:
        from .rag_store import index as rag_i
        count = rag_i(text, source, collection)
        return json.dumps({"indexed_chunks": count, "collection": collection, "source": source}, ensure_ascii=False)
    except ImportError as e:
        return json.dumps({"error": "rag_store 模块未安装", "hint": str(e)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ==================== 金融工具（Phase 10） ====================

def _mk_stock_tool(fn_name: str):
    """为 tool_finance 各函数生成桥接"""
    import importlib
    def inner(**kwargs):
        try:
            mod = importlib.import_module("tool_finance")
            fn = getattr(mod, fn_name)
            result = fn(**{k: v for k, v in kwargs.items() if v is not None})
            return result
        except Exception as e:
            return json.dumps({"error": str(e)[:200]}, ensure_ascii=False)
    inner.__name__ = fn_name
    return inner


# ==================== 工具注册 ====================

register_tool("think", _tool_think, {
    "description": "用模型自身能力思考或回答",
    "properties": {"prompt": "string"},
}, privilege="read-only")
register_tool("code_execute", _tool_code_execute, {
    "description": "执行 Python 代码（沙箱模式）",
    "properties": {"code": "string"},
}, privilege="irreversible")
register_tool("github_issues", _tool_github_issues, {
    "description": "列出或检查 GitHub Issues",
    "properties": {"repo": "string", "state": "string", "limit": "integer"},
}, privilege="read-only")
register_tool("file_read", _tool_file_read, {
    "description": "读取 workspace 内文件",
    "properties": {"path": "string"},
}, privilege="read-only")
register_tool("file_write", _tool_file_write, {
    "description": "写入 workspace 内文件（默认 append）",
    "properties": {"path": "string", "content": "string", "mode": "string"},
}, privilege="reversible")
register_tool("datetime", _tool_datetime, {
    "description": "获取当前日期时间",
    "properties": {"format_str": "string"},
}, privilege="read-only")
register_tool("summarize", _tool_summarize, {
    "description": "压缩长文本为精简摘要（减少 token 消耗）",
    "properties": {"text": "string", "max_output": "integer"},
}, privilege="read-only")
register_tool("permission_gate", _tool_permission_gate, {
    "description": "权限门 — 高风险操作记录+确认",
    "properties": {"action": "string", "reason": "string", "confirm_code": "string"},
}, privilege="reversible")
register_tool("rag_query", _tool_rag_query, {
    "description": "查询知识库（已上传的文档/PDF/笔记），检索最相关的片段。当你需要回答关于已上传文档的问题时使用这个工具。",
    "properties": {"query": "string", "collection": "string", "top_k": "integer"},
}, privilege="read-only")
register_tool("rag_index", _tool_rag_index, {
    "description": "将文本加入知识库索引。用 rag_query 来搜索。",
    "properties": {"text": "string", "source": "string", "collection": "string"},
}, privilege="reversible")

# 股票工具（read-only）
register_tool("stock_realtime", _mk_stock_tool("stock_realtime"), {
    "description": "A 股实时行情（价格/涨跌幅/成交量）",
    "properties": {"symbol": "string"},
}, privilege="read-only")
register_tool("stock_history", _mk_stock_tool("stock_history"), {
    "description": "A 股历史日线数据（OHLCV）",
    "properties": {"symbol": "string", "period": "string", "start_date": "string", "end_date": "string"},
}, privilege="read-only")
register_tool("stock_indicator", _mk_stock_tool("stock_indicator"), {
    "description": "技术指标（MA/MACD/RSI/布林带）",
    "properties": {"symbol": "string", "indicators": "string"},
}, privilege="read-only")
register_tool("stock_financial", _mk_stock_tool("stock_financial"), {
    "description": "财报数据（营收/利润/ROE）",
    "properties": {"symbol": "string"},
}, privilege="read-only")
register_tool("stock_search", _mk_stock_tool("stock_search"), {
    "description": "按名称/代码搜索 A 股",
    "properties": {"query": "string"},
}, privilege="read-only")
register_tool("stock_compare", _mk_stock_tool("stock_compare"), {
    "description": "多股横向对比",
    "properties": {"symbols": "string"},
}, privilege="read-only")
register_tool("stock_market_index", _mk_stock_tool("stock_market_index"), {
    "description": "大盘指数（上证/深证/创业）",
    "properties": {"index_name": "string"},
}, privilege="read-only")
register_tool("stock_alert_condition", _mk_stock_tool("stock_alert_condition"), {
    "description": "条件预警（涨跌幅/成交量异动）",
    "properties": {"symbol": "string", "condition": "string"},
}, privilege="read-only")
