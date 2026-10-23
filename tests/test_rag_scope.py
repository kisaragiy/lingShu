"""SEC-2：RAG collection 的作用域隔离（数据层兜底）。

背景：API 层的 `_user_collection()` 只保护「API 自己拼出来的名字」，而 agent 的
`rag_query` / `rag_index` 工具允许模型直接传 collection 名 —— 被检索内容诱导后可以传
别人的物理名，绕过 API 那层映射。因此作用域校验下沉到 rag_store 的数据层：
所有读写都经过 `_coll_dir()`，一处覆盖全部入口。

反证测试（test_thread_pool_does_not_inherit_scope_without_copy_context）说明为什么
graph_multi 必须显式 `contextvars.copy_context()`。
"""

import concurrent.futures
import contextvars
import os
import sys

import pytest


@pytest.fixture()
def rs(tmp_path, monkeypatch):
    """隔离的 rag_store 实例（独立 HARNESS_RAG_DIR，不复用生产目录）。"""
    monkeypatch.setenv("HARNESS_RAG_DIR", str(tmp_path))
    monkeypatch.setenv("HARNESS_EMBED_DIM", "8")
    for mod_name in list(sys.modules):
        if mod_name == "agent_harness.core.tools.rag_store" or mod_name.startswith("rag_store"):
            del sys.modules[mod_name]
    import agent_harness.core.tools.rag_store as mod

    # 让 list_collections 看到的是 tmp_path
    assert mod.RAG_DIR.rstrip("/\\").endswith(str(tmp_path).rstrip("/\\").split(os.sep)[-1])
    return mod


def test_default_scope_is_empty_so_admin_and_cli_are_unaffected(rs):
    """CLI / demo / admin 路径不设 scope → 空串 → 全见（向后兼容，零回归）。

    这条是回归红线：安全改动不得让 demo 和单机脚本突然读不到自己的知识库。
    """
    assert rs.current_scope() == ""
    assert rs._coll_dir("anyones_collection")


def test_scope_allows_own_collection(rs):
    tok = rs.set_scope("user_a_")
    try:
        assert rs._coll_dir("user_a_docs").endswith("user_a_docs")
    finally:
        rs.reset_scope(tok)


def test_scope_denies_foreign_collection(rs):
    """越权必须**明确报错**而不是静默返回空 —— 空结果会被当成「库是空的」。"""
    tok = rs.set_scope("user_a_")
    try:
        with pytest.raises(PermissionError) as exc:
            rs._coll_dir("user_b_secret")
        assert "越权" in str(exc.value)
    finally:
        rs.reset_scope(tok)


@pytest.mark.parametrize("func_name,args", [
    ("collection_info", ("user_b_secret",)),
    ("delete_collection", ("user_b_secret",)),
])
def test_read_and_delete_paths_are_guarded(rs, func_name, args):
    """读写删都必须过同一道闸 —— 只防读不防删等于没防。"""
    func = getattr(rs, func_name)
    tok = rs.set_scope("user_a_")
    try:
        with pytest.raises(PermissionError):
            func(*args)
    finally:
        rs.reset_scope(tok)


def test_list_collections_is_filtered(rs, tmp_path):
    """supervisor 会把 list_collections() 直接拼进 system prompt ——
    不过滤就是把所有人的 collection 物理名泄露给模型和用户。"""
    (tmp_path / "user_a_docs").mkdir()
    (tmp_path / "user_b_secret").mkdir()

    tok = rs.set_scope("user_a_")
    try:
        assert rs.list_collections() == ["user_a_docs"]
    finally:
        rs.reset_scope(tok)

    # scope 清空后恢复全见
    assert rs.list_collections() == ["user_a_docs", "user_b_secret"]


def test_thread_pool_does_not_inherit_scope_without_copy_context(rs):
    """反证：ThreadPoolExecutor 裸提交会丢 contextvars。

    所以 graph_multi._run_with_trace 必须 copy_context()，否则 worker 线程里 scope
    变成空串（=全见），agent 的 rag_query 就能读到别人的 collection —— 改动不是装饰。
    """
    tok = rs.set_scope("user_a_")
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            bare = pool.submit(rs.current_scope).result()
            copied = pool.submit(contextvars.copy_context().run, rs.current_scope).result()
    finally:
        rs.reset_scope(tok)

    assert bare == "", "裸提交竟然继承了 scope —— 本测试的假设失效，需重新评估传播机制"
    assert copied == "user_a_"


def test_scope_reset_leaves_no_residue(rs):
    """scope 是上下文状态，泄漏到下一个请求等于给所有人开权限。"""
    tok = rs.set_scope("user_a_")
    rs.reset_scope(tok)
    assert rs.current_scope() == ""
    assert rs._coll_dir("user_b_secret")


# ─── 接线测试：数据层有闸还不够，API 层必须真的把 scope 传进来 ───

def test_execute_multi_wires_scope_into_agent_run(monkeypatch):
    """证明 _execute_multi 真的调了 set_scope 并正确 reset。

    只写 set_scope 而没人调用 = 闸门悬空，是最容易自我欺骗的"修好了"。
    """
    import agent_harness.apps.research.api as api_mod
    from agent_harness.core.tools import rag_store

    captured = {}

    def fake_run_multi_agent(prompt, task_id="", checkpointer=None):
        captured["scope"] = rag_store.current_scope()
        captured["checkpointer"] = checkpointer
        return {"ok": True}

    monkeypatch.setattr(
        "agent_harness.core.graph.graph_multi.run_multi_agent", fake_run_multi_agent
    )
    # 隔离副作用：不让单测真去建 ~/.agent-harness/checkpoints.sqlite
    monkeypatch.setattr(
        "agent_harness.core.graph.graph_multi.default_checkpointer", lambda: None
    )
    anchor = rag_store.set_scope("caller_scope_")   # 模拟调用方已有上下文
    try:
        api_mod._execute_multi("hi", "", session_id="s1", kb_scope="user_a_")
        assert captured["scope"] == "user_a_"
    finally:
        rag_store.reset_scope(anchor)

    assert rag_store.current_scope() == ""


def test_execute_single_wires_scope_into_agent_run(monkeypatch):
    import agent_harness.apps.research.api as api_mod
    from agent_harness.core.tools import rag_store

    captured = {}

    def fake_run_single(prompt):
        captured["scope"] = rag_store.current_scope()
        return "ok"

    monkeypatch.setattr("agent_harness.core.graph.run", fake_run_single)
    api_mod._execute_single("hi", "", kb_scope="user_b_")
    assert captured["scope"] == "user_b_"
    assert rag_store.current_scope() == ""

