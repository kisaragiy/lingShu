"""CS Demo API — routes for the Customer Service demo."""
import json
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

router = APIRouter()


@router.get("/cs-demo", include_in_schema=False)
async def serve_cs_demo():
    """Standalone Customer Service Demo page (productized)."""
    import os
    cs_path = os.path.join(os.path.dirname(__file__), "static", "cs-demo.html")
    if os.path.exists(cs_path):
        html = open(cs_path, encoding="utf-8").read()
        # Demo mode banner
        try:
            from agent_harness.core.config import DISABLE_AUTH
            if DISABLE_AUTH:
                banner = '<div style="background:#fef3c7;color:#92400e;text-align:center;padding:6px 12px;font-size:13px;border-bottom:1px solid #fbbf24">\uD83D\uDFE1 Demo \u6A21\u5F0F \u2014 \u56DE\u590D\u4E3A\u6A21\u677F\u793A\u4F8B\uFF0C\u975E\u771F\u5B9E LLM \u751F\u6210</div>'
                if "<body>" in html:
                    html = html.replace("<body>", "<body>" + banner)
        except ImportError:
            pass
        return HTMLResponse(html)
    return JSONResponse({"error": "cs-demo.html not found"}, status_code=404)


@router.post("/v1/cs/chat")
async def cs_chat(request: Request):
    """Customer Service demo chat endpoint.

    Body: {"message": "customer message", "session_id": "optional"}
    Returns: {"reply": "...", "intent": "...", "session_id": "..."}
    """
    body = await request.json()
    message = (body.get("message", "") or "").strip()
    session_id = body.get("session_id", "") or "cs_" + str(uuid.uuid4())[:8]

    if not message:
        return JSONResponse({"error": "消息不能为空"}, status_code=400)

    from agent_harness.core.pipeline.session_store import load_session as _load_cs_session
    history = _load_cs_session(session_id) or []
    context = ""
    if history:
        recent = history[-4:]
        context = "\n".join(
            "{}: {}".format(m["role"], m["content"][:100])
            for m in recent
        )
    from agent_harness.apps.cs_demo.agents.cs_agent import run_cs_agent
    from agent_harness.apps.cs_demo.tools.customer_service import classify_cs_intent
    result = run_cs_agent(message, context=context)
    reply = result.get("reply", "")
    intent = result.get("intent", classify_cs_intent(message))
    tool_results = result.get("tool_results", {})
    quick_replies = result.get("quick_replies", _get_cs_quick_replies(intent))

    session_messages = history + [
        {"role": "user", "content": message, "ts": time.time()},
        {"role": "assistant", "content": reply, "ts": time.time()},
    ]
    from agent_harness.core.pipeline.session_store import save_session as _save_cs_session
    _save_cs_session(session_id, session_messages, owner_id="__cs_demo__")

    return {
        "reply": reply,
        "intent": intent,
        "session_id": session_id,
        "quick_replies": quick_replies,
        "tool_results": tool_results,
    }


@router.post("/v1/cs/chat/stream")
async def cs_chat_stream(request: Request):
    """Customer Service demo SSE streaming endpoint."""
    body = await request.json()
    message = (body.get("message", "") or "").strip()
    session_id = body.get("session_id", "") or "cs_" + str(uuid.uuid4())[:8]

    if not message:
        return JSONResponse({"error": "消息不能为空"}, status_code=400)

    from agent_harness.core.pipeline.session_store import load_session as _load_cs_session
    history = _load_cs_session(session_id) or []
    context = ""
    if history:
        recent = history[-4:]
        context = "\n".join(
            "{}: {}".format(m["role"], m["content"][:100])
            for m in recent
        )

    from agent_harness.apps.cs_demo.agents.cs_agent import _call_cs_llm_stream_tokens, _execute_tools, _get_quick_replies, _template_fallback
    from agent_harness.apps.cs_demo.tools.customer_service import classify_cs_intent

    intent = classify_cs_intent(message)
    tool_results = _execute_tools(intent, message)
    tool_summary = "\n".join(tool_results.values()) if tool_results else "无相关数据"

    async def event_generator():
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        yield "data: {}\n\n".format(json.dumps({"type": "intent", "intent": intent}, ensure_ascii=False))

        for name, result in tool_results.items():
            yield "data: {}\n\n".format(json.dumps({"type": "tool", "name": name, "result": result[:200]}, ensure_ascii=False))

        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as pool:
            tokens = await loop.run_in_executor(
                pool,
                _call_cs_llm_stream_tokens,
                message, intent, tool_summary, context,
            )

        full_reply = "".join(tokens)

        if not full_reply or len(full_reply.strip()) < 10:
            fallback = _template_fallback(intent, message, tool_summary)
            yield "data: {}\n\n".format(json.dumps({"type": "token", "content": fallback}, ensure_ascii=False))
            full_reply = fallback
        else:
            for t in tokens:
                yield "data: {}\n\n".format(json.dumps({"type": "token", "content": t}, ensure_ascii=False))

        quick_replies = _get_quick_replies(intent, tool_results)
        yield "data: {}\n\n".format(json.dumps({
            "type": "done",
            "quick_replies": quick_replies,
            "session_id": session_id,
        }, ensure_ascii=False))

        session_messages = history + [
            {"role": "user", "content": message, "ts": time.time()},
            {"role": "assistant", "content": full_reply, "ts": time.time()},
        ]
        from agent_harness.core.pipeline.session_store import save_session as _save_cs_session
        _save_cs_session(session_id, session_messages, owner_id="__cs_demo__")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _get_cs_quick_replies(intent: str) -> list[str]:
    replies = {
        "查订单": ["查物流", "申请退货", "转人工"],
        "退换货": ["查订单", "查物流", "退货退款多久到账", "转人工"],
        "物流查询": ["查另一个订单", "配送时效", "转人工", "投诉"],
        "投诉": ["转人工", "查订单", "退换货"],
        "FAQ查询": ["查订单", "转人工", "退换货", "优惠"],
        "售前咨询": ["什么分期方案", "优惠券", "对比", "查订单"],
        "优惠查询": ["夏日大促8折", "手机优惠", "查订单", "推荐商品"],
        "地址修改": ["查订单", "配送时效", "转人工"],
        "人工客服": ["查订单", "查物流", "优惠"],
    }
    return replies.get(intent, ["查订单", "退换货", "推荐商品", "转人工"])
