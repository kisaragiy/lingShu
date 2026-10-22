"""证据链持久化：provenance / factcheck 必须跟会话消息一起落盘。

原状（真断点）：会话只存 role/content/ts，刷新页面后来源与核验结果全丢，
「每条结论可回溯」只在单次请求的生命周期内成立；`/v1/reports/formalize`
也因此永远拿不到证据，正式报告没有「事实核验说明」汇总块（原代码注释自己承认了）。

本文件锁住四件事：
1. provenance / factcheck 存得下、读得回（session_store 的 message 事件保留全部字段）
2. 落盘前瘦身（防止会话 JSONL 无界膨胀）
3. 不给 LLM 喂 provenance（history_context 只取 role/content，省 token）
4. formalize 能从来源会话取证据，且**取别人的会话必须 403**
"""

import asyncio
import types

import pytest


@pytest.fixture()
def sess(tmp_path, monkeypatch):
    from agent_harness.core.pipeline import session_store

    monkeypatch.setattr(session_store, "SESSION_DIR", str(tmp_path))
    session_store._proj_cache.clear()
    session_store.init_store()
    return session_store


def _prov(url="https://stats.gov.cn/a", **kw):
    p = {"url": url, "title": "统计局公告", "tool": "search",
         "fetched_at": "2026-10-23T00:00:00", "snippet": "营收 987 亿元", "status": "unknown"}
    p.update(kw)
    return p


# ─── 1. 存得下、读得回 ───

def test_provenance_and_factcheck_survive_roundtrip(sess):
    msgs = [
        {"role": "user", "content": "2025 销量", "ts": 1.0},
        {"role": "assistant", "content": "结论 [1]", "ts": 2.0,
         "provenance": [_prov()], "factcheck": {"checked": True, "not_found": []}},
    ]
    sess.save_session("s1", msgs, owner_id="u1")

    back = sess.load_session("s1")
    assert back[1]["provenance"][0]["url"] == "https://stats.gov.cn/a"
    assert back[1]["provenance"][0]["tool"] == "search"
    assert back[1]["factcheck"]["checked"] is True


def test_messages_without_evidence_still_load(sess):
    """没有证据的老消息不能被新逻辑破坏（向后兼容）。"""
    sess.save_session("s2", [{"role": "user", "content": "q", "ts": 1.0},
                             {"role": "assistant", "content": "a", "ts": 2.0}], owner_id="u1")
    back = sess.load_session("s2")
    assert "provenance" not in back[1]


# ─── 2. 瘦身 ───

def test_slim_provenance_truncates_and_caps():
    from agent_harness.apps.research.api import _PROV_MAX_ITEMS, _PROV_TEXT_LIMIT, _slim_provenance

    many = [_prov(url=f"https://x/{i}", snippet="字" * 9999) for i in range(_PROV_MAX_ITEMS + 20)]
    slim = _slim_provenance(many)
    assert len(slim) == _PROV_MAX_ITEMS, "必须限量，否则会话 JSONL 无界膨胀"
    assert len(slim[0]["snippet"]) == _PROV_TEXT_LIMIT
    assert set(slim[0]) == {"url", "title", "tool", "fetched_at", "snippet", "status"}


def test_slim_provenance_survives_garbage():
    """外部来源是模型/网页给的，字段类型不可信。"""
    from agent_harness.apps.research.api import _slim_provenance

    assert _slim_provenance(None) == []
    assert _slim_provenance([None, "x", 42]) == []
    assert _slim_provenance([{"url": None, "title": 123}])[0]["url"] == ""
    assert _slim_provenance([{"url": None, "title": 123}])[0]["title"] == "123"
    assert _slim_provenance([{"url": None}])[0]["title"] == ""


# ─── 3. 不喂给 LLM ───

def test_history_context_excludes_provenance():
    from agent_harness.apps.research.api import _build_history_context

    ctx = _build_history_context([{
        "role": "assistant", "content": "结论在这里",
        "provenance": [_prov(url="https://should-not-appear.example/secret")],
    }])
    assert "结论在这里" in ctx
    assert "should-not-appear" not in ctx, "来源不能进 history —— 白烧 token 且污染上下文"


# ─── 4. 从会话取证据 + owner 闸 ───

def test_sources_from_provenance_dedupes_by_url():
    from agent_harness.apps.research.api import _sources_from_provenance

    src = _sources_from_provenance([_prov(), _prov(title="重复"), _prov(url="https://b.cn/x")])
    assert [s["url"] for s in src] == ["https://stats.gov.cn/a", "https://b.cn/x"]
    assert src[0]["title"] == "统计局公告"


def test_evidence_from_session_denies_other_owner(sess):
    from agent_harness.apps.research.api import _evidence_from_session

    sess.save_session("s3", [{"role": "assistant", "content": "a", "ts": 1.0,
                              "provenance": [_prov()]}], owner_id="u1")
    prov, fc, err = _evidence_from_session("s3", owner_id="u2", role="user")
    assert prov is None and fc is None
    assert err is not None and err.status_code == 403, "取别人的会话必须拒绝（含 URL 与摘录）"


def test_evidence_from_session_missing_session_is_404(sess):
    from agent_harness.apps.research.api import _evidence_from_session

    _, _, err = _evidence_from_session("nope", owner_id="u1", role="user")
    assert err is not None and err.status_code == 404


def test_evidence_from_session_picks_latest_with_provenance(sess):
    from agent_harness.apps.research.api import _evidence_from_session

    sess.save_session("s4", [
        {"role": "assistant", "content": "第一轮", "ts": 1.0, "provenance": [_prov(url="https://old.cn/x")]},
        {"role": "user", "content": "追问", "ts": 2.0},
        {"role": "assistant", "content": "第二轮", "ts": 3.0, "provenance": [_prov(url="https://new.cn/x")]},
    ], owner_id="u1")
    prov, _, err = _evidence_from_session("s4", owner_id="u1", role="user")
    assert err is None
    assert prov[0]["url"] == "https://new.cn/x", "应取最近一轮的证据"


# ─── 5. formalize 真正用上会话里的证据 ───

class _FakeRequest:
    def __init__(self, body, user):
        self._body = body
        self.state = types.SimpleNamespace(user=user)

    async def json(self):
        return self._body


def test_formalize_pulls_evidence_from_source_session(sess, monkeypatch):
    """回归：此前 formalize 只认调用方显式传入 → agent 侧不传 → 报告永远没有核验块。"""
    import agent_harness.apps.research.api as api_mod
    from agent_harness.apps.research.pipeline import report_formatter

    sess.save_session("s5", [
        {"role": "user", "content": "q", "ts": 1.0},
        {"role": "assistant", "content": "结论 [1]", "ts": 2.0,
         "provenance": [_prov()], "factcheck": {"checked": True, "not_found": []}},
    ], owner_id="u1")

    captured = {}

    def _fake_generate(title, content, sources=None, provenance=None, factcheck=None):
        captured.update(sources=sources, provenance=provenance, factcheck=factcheck)
        return "<html>report</html>"

    monkeypatch.setattr(report_formatter, "generate_report_html", _fake_generate)
    monkeypatch.setattr(report_formatter, "save_formal_report", lambda **kw: {"id": "r1"})

    req = _FakeRequest({"title": "t", "content": "c", "source_session": "s5"},
                       {"id": "u1", "role": "user"})
    asyncio.run(api_mod.formalize_report(req))

    assert captured["provenance"], "formalize 没有从会话取 provenance"
    assert captured["provenance"][0]["url"] == "https://stats.gov.cn/a"
    assert captured["factcheck"]["checked"] is True
    assert captured["sources"][0]["url"] == "https://stats.gov.cn/a", "sources 应从 provenance 派生"


def test_formalize_respects_explicit_arguments(sess, monkeypatch):
    """调用方显式传的证据优先，不能被会话里的覆盖。"""
    import agent_harness.apps.research.api as api_mod
    from agent_harness.apps.research.pipeline import report_formatter

    sess.save_session("s6", [{"role": "assistant", "content": "a", "ts": 1.0,
                              "provenance": [_prov(url="https://session.cn/x")]}], owner_id="u1")
    captured = {}
    monkeypatch.setattr(report_formatter, "generate_report_html",
                        lambda title, content, sources=None, provenance=None, factcheck=None:
                        captured.update(provenance=provenance) or "<html/>")
    monkeypatch.setattr(report_formatter, "save_formal_report", lambda **kw: {"id": "r2"})

    req = _FakeRequest({
        "title": "t", "content": "c", "source_session": "s6",
        "provenance": [_prov(url="https://explicit.cn/x")],
    }, {"id": "u1", "role": "user"})
    asyncio.run(api_mod.formalize_report(req))
    assert captured["provenance"][0]["url"] == "https://explicit.cn/x"


def test_formalize_blocks_other_owners_session(sess, monkeypatch):
    import agent_harness.apps.research.api as api_mod
    from agent_harness.apps.research.pipeline import report_formatter

    sess.save_session("s7", [{"role": "assistant", "content": "a", "ts": 1.0,
                              "provenance": [_prov()]}], owner_id="u1")
    called = {"gen": False}
    monkeypatch.setattr(report_formatter, "generate_report_html",
                        lambda *a, **k: called.update(gen=True) or "<html/>")

    req = _FakeRequest({"title": "t", "content": "c", "source_session": "s7"},
                       {"id": "u2", "role": "user"})
    resp = asyncio.run(api_mod.formalize_report(req))
    assert getattr(resp, "status_code", None) == 403
    assert called["gen"] is False, "拒绝后不应继续生成报告"


# ─── 6. 流式路径也要把证据交出来 ───

def test_stream_queue_emits_evidence_event(monkeypatch):
    """流式路径此前只 put 文本，证据根本无法落盘。"""
    import queue as _queue

    import agent_harness.apps.research.api as api_mod

    fake = {
        "final_output": "结论", "rounds": 1, "worker_results": {"searcher": {}},
        "provenance": [_prov()], "factcheck": {"checked": True, "not_found": []},
    }
    monkeypatch.setattr(api_mod, "_execute_multi", lambda *a, **k: fake)

    q = _queue.Queue()
    api_mod._run_with_queue("问题", "", "agent-harness-multi", q, session_id="")

    events = []
    while not q.empty():
        events.append(q.get())
    evidence = [e for e in events if e.get("type") == "evidence"]
    assert evidence, "流式路径没有交出 evidence 事件 → 刷新后来源仍然会丢"
    assert evidence[0]["provenance"][0]["url"] == "https://stats.gov.cn/a"
    assert evidence[0]["factcheck"]["checked"] is True


def test_stream_queue_leaves_text_only_on_single_agent(monkeypatch):
    """单 Agent 分支没有结构化结果：不能因为找 evidence 而崩。"""
    import queue as _queue

    import agent_harness.apps.research.api as api_mod

    monkeypatch.setattr(api_mod, "_execute_single", lambda *a, **k: "纯文本回答")
    q = _queue.Queue()
    api_mod._run_with_queue("问题", "", "agent-harness-single", q, session_id="")

    events = []
    while not q.empty():
        events.append(q.get())
    assert not [e for e in events if e.get("type") == "evidence"]
    assert any(e.get("type") == "result" for e in events)
    assert events[-1]["type"] == "done"
