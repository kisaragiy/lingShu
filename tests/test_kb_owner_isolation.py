"""SEC-16：knowledge collection 按用户隔离（越权读 + 越权删库）。

修复前：四个 knowledge 端点全无 owner 概念，collection 是全局命名空间 ——
任意登录用户可列出所有人的 collection、上传进任意 collection、删除任意 collection。

修法：API 层把普通用户的 collection 名映射为物理名 `user_<uid>_<name>`，admin 直通原名。
本文件锁定 A/B/admin/guest 四身份的 列表/查询/上传/删除 矩阵；隔离失败必须是 404，
且绝不能真的删掉别人的目录。
"""
import numpy as np
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import agent_harness.apps.research.api as research_api
from agent_harness.core.tools import rag_store

IDENTITIES = {
    "A": {"id": "u_a", "username": "a", "role": "user"},
    "B": {"id": "u_b", "username": "b", "role": "user"},
    "admin": {"id": "u_admin", "username": "root", "role": "admin"},
}


def _client(user: dict) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_user(request: Request, call_next):
        if user:
            request.state.user = dict(user)
        return await call_next(request)

    app.include_router(research_api.router)
    return TestClient(app)


@pytest.fixture
def rag_tmp(monkeypatch, tmp_path):
    """RAG_DIR 指向临时目录；嵌入 stub 掉（只走 BM25，测试不依赖 ollama）。"""
    monkeypatch.setattr(rag_store, "RAG_DIR", str(tmp_path), raising=True)
    monkeypatch.setattr(
        rag_store,
        "_embed",
        lambda texts: (
            np.zeros((len(texts), rag_store.EMBED_DIM), dtype=np.float32),
            list(texts),
        ),
    )
    return tmp_path


def _upload(client: TestClient, text: str, collection: str = "default", name: str = "note.txt"):
    return client.post(
        "/v1/knowledge/upload",
        files={"file": (name, text.encode("utf-8"), "text/plain")},
        data={"collection": collection},
    )


def test_upload_lands_in_owner_prefixed_collection(rag_tmp):
    r = _upload(_client(IDENTITIES["A"]), "A 的私有研究笔记 zebra-unicorn")
    assert r.status_code == 200, r.text
    assert (rag_tmp / "user_u_a_default").is_dir()
    assert r.json()["collection"] == "default"          # 展示名，不泄漏物理名给前端


def test_other_user_cannot_query_owner_content(rag_tmp):
    _upload(_client(IDENTITIES["A"]), "A 的私有研究笔记 zebra-unicorn")
    r = _client(IDENTITIES["B"]).get(
        "/v1/knowledge/query", params={"q": "zebra-unicorn", "collection": "default"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["results"] == [], "B 检索到了 A 的知识库内容"


def test_list_hides_other_users_collections(rag_tmp):
    _upload(_client(IDENTITIES["A"]), "A 的笔记")
    r = _client(IDENTITIES["B"]).get("/v1/knowledge/collections")
    names = [c["physical_name"] for c in r.json()["collections"]]
    assert not any(n.startswith("user_u_a") for n in names), names
    r_admin = _client(IDENTITIES["admin"]).get("/v1/knowledge/collections")
    names_admin = [c["physical_name"] for c in r_admin.json()["collections"]]
    assert any(n.startswith("user_u_a") for n in names_admin)


def test_list_shows_stripped_name_for_owner(rag_tmp):
    _upload(_client(IDENTITIES["A"]), "A 的笔记", collection="mine")
    r = _client(IDENTITIES["A"]).get("/v1/knowledge/collections")
    item = [c for c in r.json()["collections"] if c["physical_name"] == "user_u_a_mine"][0]
    assert item["name"] == "mine"


def test_non_owner_cannot_delete_by_physical_name(rag_tmp):
    _upload(_client(IDENTITIES["A"]), "A 的笔记")
    target = rag_tmp / "user_u_a_default"
    assert target.is_dir()

    # B 直接拿 A 的物理名来删 → 会被再套一层自己的前缀 → 404，且目录必须还在
    r = _client(IDENTITIES["B"]).delete("/v1/knowledge/collections/user_u_a_default")
    assert r.status_code == 404, r.text
    assert target.is_dir(), "非 owner 删掉了别人的 collection（越权删库）"


def test_owner_can_delete_own_collection(rag_tmp):
    _upload(_client(IDENTITIES["A"]), "A 的笔记")
    r = _client(IDENTITIES["A"]).delete("/v1/knowledge/collections/default")
    assert r.status_code == 200, r.text
    assert not (rag_tmp / "user_u_a_default").is_dir()


def test_admin_can_delete_any_collection(rag_tmp):
    _upload(_client(IDENTITIES["A"]), "A 的笔记")
    r = _client(IDENTITIES["admin"]).delete("/v1/knowledge/collections/user_u_a_default")
    assert r.status_code == 200, r.text
    assert not (rag_tmp / "user_u_a_default").is_dir()


def test_anonymous_gets_guest_scope(rag_tmp):
    """无认证信息的请求落到 guest 前缀，既写不到用户库也读不到用户库。"""
    _upload(_client(IDENTITIES["A"]), "A 的私有笔记 zebra-unicorn")
    anon = _client({})
    r = anon.get("/v1/knowledge/query", params={"q": "zebra-unicorn", "collection": "default"})
    assert r.json()["results"] == []
    r2 = _upload(anon, "匿名内容")
    assert r2.status_code == 200, r2.text
    assert (rag_tmp / "user_guest_default").is_dir()


def test_import_endpoint_scoped(rag_tmp, monkeypatch):
    """第 5 个端点（/v1/knowledge/import）同类洞，方案清单未列 —— 一并锁住。"""
    import json as _json
    from agent_harness.core.tools import misc

    def _stub_import(text, source="", collection="memory"):
        rag_store.index(text, source=source, collection=collection)   # 真实落库
        return _json.dumps({"ok": True, "collection": collection})

    monkeypatch.setattr(misc, "_tool_memory_import", _stub_import)
    r = _client(IDENTITIES["A"]).post(
        "/v1/knowledge/import", json={"text": "A 的记忆片段", "source": "chatgpt"}
    )
    assert r.status_code == 200, r.text
    assert (rag_tmp / "user_u_a_memory").is_dir()
    assert not (rag_tmp / "memory").exists()


def test_chat_rag_injection_respects_scope(rag_tmp, monkeypatch):
    """chat 路径会把知识库内容注入 prompt —— 只能注入当前用户可见的 collection。"""
    _upload(_client(IDENTITIES["A"]), "A 的机密项目代号 zebra-unicorn")

    import agent_harness.core.graph.graph_multi as gm

    seen = {}

    def _fake_run_multi(enhanced, **kwargs):
        seen["prompt"] = enhanced
        return {"final_output": "ok", "rounds": 1, "worker_results": {}}

    monkeypatch.setattr(gm, "run_multi_agent", _fake_run_multi)

    # B 的 scope：看不到 u_a 的 collection
    research_api._execute_multi("项目代号是什么", "", kb_scope="user_u_b_")
    assert "zebra-unicorn" not in seen.get("prompt", ""), "跨用户知识库内容被注入 B 的 prompt"

    # A 的 scope：自己能看到（双向断言，防止"过滤过度 = 功能失效"）
    research_api._execute_multi("项目代号是什么", "", kb_scope="user_u_a_")
    assert "zebra-unicorn" in seen.get("prompt", ""), "owner 自己也检索不到自己的知识库"
