"""ENG-19 + ENG-21 回归锁：流式路径纳入并发上限 + 断开取消 + 取消按 session 作用域。

背景（源码锚点 `apps/research/api.py`）：
  - 流式分支此前直接 `StreamingResponse(_stream_progress(...))`，**不 acquire 信号量**，
    默认路径（前端 `stream=true`）不受 `_MAX_CONCURRENT_AGENTS` 保护；
  - `_stream_progress` 没有 try/finally，客户端断开后 daemon 线程继续跑；
  - `core/pipeline/cancel.py` 用**全局单点** event，并发下 A 取消会错杀 B。

本文件只做单测，不起 uvicorn：直接调 handler / async generator。
"""

import asyncio
import contextlib
import json
import threading
import time

import pytest
from fastapi.responses import JSONResponse, StreamingResponse

from agent_harness.apps.research import api as research_api
from agent_harness.core.pipeline import cancel as cancel_mod


# ── 测试替身 ────────────────────────────────────────────────────────────────


class _FakeState:
    user: dict = {}


class _FakeRequest:
    """只实现 handler 用到的 headers / state 两处接口。"""

    def __init__(self, session_id: str = ""):
        self.headers = {"X-Session-Id": session_id} if session_id else {}
        self.state = _FakeState()


def _llm_configured(monkeypatch):
    """绕开 demo 回退分支（否则 handler 直接返回 demo 回复，走不到信号量）。"""
    import agent_harness.core.config as cfg

    monkeypatch.setattr(cfg, "LLAMA_API", "http://fake.invalid/v1")
    monkeypatch.setattr(cfg, "CLOUD_API_KEY", "")


def _no_session_io(monkeypatch):
    monkeypatch.setattr(research_api, "_load_session", lambda sid: [])
    monkeypatch.setattr(research_api, "_save_session", lambda *a, **k: None)


_WORKER_SEEN: list[bool] = []


def _quick_worker(prompt, history, model, q, session_id="", kb_scope="", pre_acquired=False):
    """替身 worker：立刻吐 status/result/done，不碰 LLM。"""
    _WORKER_SEEN.append(pre_acquired)
    q.put({"type": "status", "content": "🤔 分析请求中...\n"})
    q.put({"type": "result", "content": "done-result"})
    q.put({"type": "done"})


async def _drain(agen):
    return [chunk async for chunk in agen]


def _chat_req(stream=True):
    return research_api.ChatRequest(
        model="agent-harness-multi",
        messages=[{"role": "user", "content": "hi"}],
        stream=stream,
    )


@contextlib.contextmanager
def _cancel_lock():
    """改动前的 cancel.py 还没有 _lock/_events —— 让每个用例各自失败，别在 fixture 里炸。"""
    lock = getattr(cancel_mod, "_lock", None)
    with (lock if lock is not None else contextlib.nullcontext()):
        yield


def _reset_cancel_registry():
    with _cancel_lock():
        events = getattr(cancel_mod, "_events", None)
        if events is not None:
            events.clear()
    setter = getattr(cancel_mod, "set_cancel_event", None)
    if setter is not None:
        setter(None)


@pytest.fixture(autouse=True)
def _clean_registries():
    _WORKER_SEEN.clear()
    with research_api._running_tasks_lock:
        research_api._running_tasks.clear()
    _reset_cancel_registry()
    yield
    with research_api._running_tasks_lock:
        research_api._running_tasks.clear()
    _reset_cancel_registry()


# ── ① 流式分支受并发上限保护：拿不到信号量 → 503 ───────────────────────────


def test_stream_branch_returns_503_when_semaphore_exhausted(monkeypatch):
    _llm_configured(monkeypatch)
    # 信号量已被占满（真实 Semaphore(0)，不做假对象）
    monkeypatch.setattr(research_api, "_agent_semaphore", threading.Semaphore(0))
    monkeypatch.setattr(research_api, "_SEMAPHORE_TIMEOUT_S", 0.05, raising=False)

    resp = asyncio.run(research_api.chat_completions(_chat_req(stream=True), _FakeRequest("sess-sat")))

    assert isinstance(resp, JSONResponse), f"饱和时必须直接拒绝，实测返回 {type(resp).__name__}"
    assert resp.status_code == 503
    payload = json.loads(resp.body)
    assert "服务器繁忙" in payload["error"]


def test_stream_branch_acquires_semaphore_before_streaming(monkeypatch):
    """未饱和时：handler 必须占用一个名额，且把 pre_acquired=True 传给 worker。"""
    _llm_configured(monkeypatch)
    _no_session_io(monkeypatch)
    sema = threading.Semaphore(2)
    monkeypatch.setattr(research_api, "_agent_semaphore", sema)
    monkeypatch.setattr(research_api, "_run_with_queue", _quick_worker)

    resp = asyncio.run(research_api.chat_completions(_chat_req(stream=True), _FakeRequest("sess-ok")))
    assert isinstance(resp, StreamingResponse)

    # 流还没被消费：名额已被 handler 占用
    assert sema._value == 1, "handler 层未 acquire —— 流式路径没受并发上限保护"

    asyncio.run(_drain(resp.body_iterator))
    assert sema._value == 2, "generator 收尾未 release —— 并发上限会永久缩水"
    assert _WORKER_SEEN == [True], "worker 应收到 pre_acquired=True（避免双重 acquire）"


# ── ② 流结束后不留残留（_running_tasks / 取消注册表） ───────────────────────


def test_stream_finishes_without_registry_residue(monkeypatch):
    _llm_configured(monkeypatch)
    _no_session_io(monkeypatch)
    monkeypatch.setattr(research_api, "_agent_semaphore", threading.Semaphore(2))
    monkeypatch.setattr(research_api, "_run_with_queue", _quick_worker)

    chunks = asyncio.run(
        _drain(research_api._stream_progress("hi", "", "agent-harness-multi", "sess-clean", sem_acquired=True))
    )
    assert any("done-result" in c for c in chunks)
    assert "sess-clean" not in research_api._running_tasks
    with _cancel_lock():
        assert "sess-clean" not in getattr(cancel_mod, "_events", {}), "正常结束后取消事件残留 → 注册表泄漏"


# ── ③ ENG-21：取消按 session 作用域，A 取消不影响 B ─────────────────────────


def test_cancel_registry_is_scoped_per_task():
    ev_a = cancel_mod.register_task("A")
    ev_b = cancel_mod.register_task("B")

    assert cancel_mod.cancel_task("A") is True
    assert ev_a.is_set() is True
    assert cancel_mod.is_cancelled("A") is True
    assert ev_b.is_set() is False, "取消 A 波及了 B —— 还是全局单点"
    assert cancel_mod.is_cancelled("B") is False
    assert cancel_mod.cancel_task("不存在") is False

    cancel_mod.unregister_task("A")
    cancel_mod.unregister_task("B")
    assert cancel_mod.is_cancelled("A") is False


def test_legacy_global_api_still_works():
    """旧签名（CLI/脚本/既有测试）必须继续可用：走 _default_event 兜底。"""
    ev = threading.Event()
    cancel_mod.set_cancel_event(ev)
    assert cancel_mod.is_cancelled() is False
    ev.set()
    assert cancel_mod.is_cancelled() is True
    assert cancel_mod.is_cancelled("some-task") is False, "带 task_id 时不能回落到全局兜底"
    cancel_mod.clear_cancel_event()
    assert cancel_mod.is_cancelled() is False


def test_cancel_endpoint_only_sets_target_session(monkeypatch):
    _llm_configured(monkeypatch)
    ev_a = research_api._register_running_task("A")
    ev_b = research_api._register_running_task("B")

    resp = asyncio.run(research_api.cancel_task("A"))

    assert resp == {"status": "cancelled", "session_id": "A"}
    assert ev_a.is_set() is True
    assert ev_b.is_set() is False

    missing = asyncio.run(research_api.cancel_task("nope"))
    assert missing.status_code == 404


# ── ④ pre_acquired=True 不双重 release ────────────────────────────────────


def test_pre_acquired_worker_does_not_release(monkeypatch):
    """handler 已 acquire → worker 不 acquire 也不 release；release 只由 generator 做一次。

    用 BoundedSemaphore：多 release 会直接抛 ValueError（而不是悄悄把上限放大）。
    """
    _llm_configured(monkeypatch)
    _no_session_io(monkeypatch)
    sema = threading.BoundedSemaphore(1)
    assert sema.acquire(timeout=1)  # 模拟 handler 层已占用
    monkeypatch.setattr(research_api, "_agent_semaphore", sema)
    monkeypatch.setattr(research_api, "_run_with_queue", _quick_worker)

    asyncio.run(
        _drain(research_api._stream_progress("hi", "", "agent-harness-multi", "sess-pre", sem_acquired=True))
    )

    assert sema._value == 1, "名额没有回到初值 —— 双重 release 或漏 release"
    assert _WORKER_SEEN == [True]


# ── ⑤ 客户端断开 → 触发 cancel + 清理注册项 ─────────────────────────────────


def test_client_disconnect_cancels_and_cleans_up(monkeypatch):
    _llm_configured(monkeypatch)
    _no_session_io(monkeypatch)
    monkeypatch.setattr(research_api, "_agent_semaphore", threading.Semaphore(2))
    monkeypatch.setattr(research_api, "_STREAM_JOIN_TIMEOUT_S", 0.2, raising=False)

    cancelled: list[str] = []
    monkeypatch.setattr(research_api, "_cancel_task", lambda sid: cancelled.append(sid) or True)

    def _slow_worker(prompt, history, model, q, session_id="", kb_scope="", pre_acquired=False):
        q.put({"type": "status", "content": "🤔 分析请求中...\n"})
        time.sleep(0.5)  # 模拟还在跑 agent
        q.put({"type": "done"})

    monkeypatch.setattr(research_api, "_run_with_queue", _slow_worker)

    async def _open_then_disconnect():
        agen = research_api._stream_progress(
            "hi", "", "agent-harness-multi", "sess-drop", sem_acquired=True
        )
        await agen.__anext__()  # 收到首个 chunk（此时已注册运行态）
        assert "sess-drop" in research_api._running_tasks
        await agen.aclose()  # 模拟 Starlette 在客户端断开时关闭 generator

    asyncio.run(_open_then_disconnect())

    assert cancelled == ["sess-drop"], "客户端断开后没有向 worker 发取消信号"
    assert "sess-drop" not in research_api._running_tasks, "断开后注册项残留"
    with _cancel_lock():
        assert "sess-drop" not in getattr(cancel_mod, "_events", {})


# ── ⑥ ENG-21 透传：worker / graph 层用 session 作用域的 event 判断取消 ──────


def test_graph_and_worker_use_scoped_cancel(monkeypatch):
    """graph_multi / workers 的 is_cancelled 调用必须带 task_id。"""
    from agent_harness.core.agents import workers as workers_mod
    from agent_harness.core.graph import graph_multi

    seen_graph: list[str] = []
    seen_worker: list[str] = []

    monkeypatch.setattr(graph_multi, "is_cancelled", lambda tid="": seen_graph.append(tid) or False)
    monkeypatch.setattr(workers_mod, "is_cancelled", lambda tid="": seen_worker.append(tid) or False)

    cancel_mod.register_task("sess-A")
    cancel_mod.register_task("sess-B")

    graph_multi.supervisor_dispatch({"request": "r", "task_id": "sess-A", "workers_assigned": []})
    workers_mod._worker_executor({"worker_name": "search", "task": "t", "plan": [], "task_id": "sess-B"})

    assert seen_graph == ["sess-A"], f"graph 层取消检查没带 task_id: {seen_graph}"
    assert seen_worker == ["sess-B"], f"worker 层取消检查没带 task_id: {seen_worker}"

    # 真实语义：取消 A 不阻断 B 的检查点
    assert cancel_mod.cancel_task("sess-A") is True
    assert cancel_mod.is_cancelled("sess-A") is True
    assert cancel_mod.is_cancelled("sess-B") is False


def test_semaphore_timeout_does_not_release_unacquired_slot(monkeypatch):
    """acquire 超时（返回 False）绝不能 release。

    否则每超时一次并发上限就 +1 —— 闸门被自己的错误处理漏光，比不设闸门更糟
    （看起来有上限，实际无上限）。用 BoundedSemaphore 做机器判据：多 release 一次
    会抛 ValueError。
    """
    import queue as _queue

    sem = threading.BoundedSemaphore(1)
    assert sem.acquire(timeout=0) is True          # 先把唯一名额占死
    monkeypatch.setattr(research_api, "_agent_semaphore", sem)
    monkeypatch.setattr(research_api, "_SEMAPHORE_TIMEOUT_S", 0.05)

    q = _queue.Queue()
    research_api._run_with_queue("p", "", "agent-harness-multi", q, "sess-busy", "")

    events = []
    while not q.empty():
        events.append(q.get_nowait())

    kinds = [e["type"] for e in events]
    assert "error" in kinds, f"排队超时没有报错事件，用户会看到空回复: {kinds}"
    assert any("繁忙" in str(e.get("content", "")) for e in events), events

    # 关键断言：名额没有被错误归还。若代码在超时后 release，这里会抛 ValueError。
    sem.release()
    assert sem.acquire(timeout=0) is True
