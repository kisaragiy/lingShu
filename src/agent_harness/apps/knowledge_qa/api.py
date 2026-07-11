"""知识库问答助手 — 基于 RAG 的知识库问答场景。

TODO: 接入已有的 RAG 管线实现真正的知识库问答。
"""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pathlib import Path

router = APIRouter(prefix="/knowledge-qa", tags=["knowledge_qa"])

STATIC_DIR = Path(__file__).resolve().parent / "static"


@router.get("")
async def knowledge_qa_page():
    """知识库问答助手主页。"""
    index = STATIC_DIR / "index.html"
    if index.exists():
        html = index.read_text("utf-8")
        return HTMLResponse(html)
    return {"message": "知识库问答助手 — 请编辑 static/index.html"}


@router.post("/api/query")
async def query_knowledge(query: str):
    """查询知识库。

    接收: {"query": "用户问题", "top_k": 3}
    返回: {"results": [...], "answer": "..."}

    TODO: 接入 agent_harness.core.tools.rag_store 或 core.pipeline 的 RAG 管线。
    """
    return {
        "query": query,
        "results": [],
        "answer": "知识库问答功能开发中。请先上传文档到灵枢主应用的知识库。",
        "hint": "TODO: 接入 agent_harness.core.tools.rag_store.query_knowledge()",
    }
