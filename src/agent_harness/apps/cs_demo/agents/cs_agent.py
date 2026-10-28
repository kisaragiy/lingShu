"""Customer Service Agent — intent + tools + LLM response pipeline (v2).

v2 upgrade: Uses LLM to generate natural, empathetic responses while
keeping structured tools for data operations (order lookup, ticket, FAQ).

Flow:
  1. Classify intent (rule-based, fast)
  2. Execute tools based on intent (get order data, FAQ, ticket info)
  3. Build a structured prompt with tool results
  4. LLM generates the final customer-facing response
  5. Return response + tool results metadata
"""

import json

from agent_harness.apps.cs_demo.tools.customer_service import (
    classify_cs_intent,
    cs_check_promotion,
    cs_create_ticket,
    cs_estimate_delivery,
    cs_lookup_order,
    cs_query_product,
    cs_search_faq,
)


def run_cs_agent(message: str, context: str = "") -> dict:
    """Run the AI-powered customer service agent.

    Args:
        message: Customer's message
        context: Previous conversation history (for multi-turn)

    Returns:
        dict with: reply, intent, tool_results
    """
    # Step 1: Classify intent
    intent = classify_cs_intent(message)

    # Step 2: Execute tools based on intent
    tool_results = _execute_tools(intent, message)
    tool_summary = "\n".join(tool_results.values()) if tool_results else "无相关数据"

    # Step 3: Build LLM prompt
    reply = _call_cs_llm(message, intent, tool_summary, context)

    # Step 4: Build quick replies based on intent
    quick_replies = _get_quick_replies(intent, tool_results)

    return {
        "reply": reply,
        "intent": intent,
        "tool_results": tool_results,
        "quick_replies": quick_replies,
    }


def _execute_tools(intent: str, message: str) -> dict[str, str]:
    """Execute tools based on detected intent. Returns tool_name -> result."""
    import re
    results = {}

    if intent == "查订单":
        order_id = None
        m = re.search(r'ORD\d+', message)
        if m:
            order_id = m.group(0)
        phone = None
        m = re.search(r'1[3-9]\d{9}', message)
        if m:
            phone = m.group(0)
        if order_id:
            results["订单查询"] = cs_lookup_order(order_id)
        elif phone:
            results["订单查询"] = cs_lookup_order(phone)
        else:
            results["订单查询"] = cs_lookup_order("kisaragiy")

    elif intent == "物流查询":
        order_id = None
        m = re.search(r'ORD\d+', message)
        if m:
            order_id = m.group(0)
        if order_id:
            results["物流查询"] = cs_lookup_order(order_id)
            results["配送时效"] = cs_estimate_delivery(order_id)
        else:
            results["物流查询"] = cs_lookup_order("ORD20260708001")
            results["配送时效"] = cs_estimate_delivery()

    elif intent == "退换货":
        order_id = None
        m = re.search(r'ORD\d+', message)
        if m:
            order_id = m.group(0)
        if order_id:
            results["订单信息"] = cs_lookup_order(order_id)
        results["退换货政策"] = (
            "退换货政策:\n"
            "• 签收后 7 天内支持无理由退货\n"
            "• 质量问题 30 天内免费换货\n"
            "• 需保持商品完好、配件齐全\n"
            "• 退款 1-3 个工作日原路返回"
        )
        if order_id:
            results["工单"] = cs_create_ticket(order_id, "退换货", message[:100])

    elif intent == "投诉":
        results["投诉受理"] = (
            "投诉已记录，受理编号自动生成，24小时内专人联系。"
        )
        # Also check if there's a related order
        m = re.search(r'ORD\d+', message)
        if m:
            results["订单信息"] = cs_lookup_order(m.group(0))

    elif intent == "FAQ查询":
        faq = cs_search_faq(message)
        if faq:
            results["FAQ"] = faq
        else:
            results["FAQ"] = "知识库未匹配到精确结果，提供标准服务政策。"

    elif intent == "售前咨询":
        # Extract product name or keyword from message
        keywords = re.sub(r'(推荐|介绍|看看|有什么|怎么样|好不好|适合|对比|区别)', '', message)
        keywords = keywords.strip().rstrip('?？的')
        if keywords and len(keywords) > 1:
            results["商品查询"] = cs_query_product(keywords)
        else:
            # Show popular products
            results["热门推荐"] = (
                "🔥 **热门推荐**\n"
                "━━━━━━━━━━━━━━━━━\n"
                "1. 小米14 Ultra ¥5,999 — 徕卡四摄旗舰\n"
                "2. 索尼WH-1000XM5 ¥2,499 — 降噪标杆\n"
                "3. 华硕ROG Ally ¥4,999 — 掌机首选\n"
                "4. 苹果AirPods Pro 2 ¥1,899 — 真无线王者\n\n"
                "💡 回复商品名称查看详情，或告诉我您的预算和需求。"
            )
        # Also check for available promotions
        results["优惠信息"] = cs_check_promotion()

    elif intent == "优惠查询":
        results["优惠信息"] = cs_check_promotion(message)
        # Check if user's order qualifies
        m = re.search(r'ORD\d+', message)
        if m:
            results["订单信息"] = cs_lookup_order(m.group(0))

    elif intent == "地址修改":
        order_id = None
        m = re.search(r'ORD\d+', message)
        if m:
            order_id = m.group(0)
        if order_id:
            results["订单信息"] = cs_lookup_order(order_id)
        else:
            # Show user's pending orders that can be modified
            orders = cs_lookup_order("kisaragiy")
            results["当前订单"] = orders + "\n\n💡 请提供想修改的订单号和新地址。"
        results["地址修改指南"] = (
            "📋 **地址修改说明**\n"
            "━━━━━━━━━━━━━━━━━\n"
            "• 仅「待发货」和「待付款」订单可修改地址\n"
            "• 已发货订单请联系物流公司改址\n"
            "• 请提供完整收货地址（省市区+街道+门牌）"
        )

    elif intent == "人工客服":
        results["转人工"] = "排队中，预计等待3-5分钟。备用热线: 400-800-8888"
    else:
        # chitchat — no tools needed
        pass

    return results


def _call_cs_llm(message: str, intent: str, tool_data: str, context: str) -> str:
    """Use LLM to generate a natural customer service response.

    Falls back to template response if LLM is unavailable.
    """
    from agent_harness.core.agents.supervisor import _call_llm

    system = (
        "你是灵枢电商平台的智能客服助手。你的风格：专业、耐心、有同理心。\n\n"
        "回复规则:\n"
        "1. 先确认用户需求，再给出具体信息\n"
        "2. 使用自然、友好的语气，像真人客服\n"
        "3. 引用订单数据时要准确\n"
        "4. 在回复末尾提供后续操作建议\n"
        "5. 如果用户表达不满，先道歉再解决问题\n"
        "6. 回复用 Markdown 格式，300 字以内\n"
        "7. 不要提及「系统提示」「工具结果」等内部术语\n"
        "8. 结尾加一句友好的话，如「还有其他需要吗？」\n\n"
        "你可以处理的场景：查订单、查物流、退换货、投诉、FAQ解答、\n"
        "售前咨询（商品推荐/对比/参数）、优惠查询（优惠券/促销活动）、地址修改、转人工。"
    )

    user = (
        "【用户消息】\n{}\n\n"
        "【识别意图】\n{}\n\n"
        "【查询结果】\n{}\n\n"
        "【对话历史】\n{}\n\n"
        "请根据以上信息生成客服回复。"
    ).format(message, intent, tool_data, context or "（无）")

    reply = _call_llm(
        [{"role": "user", "content": user}],
        system_prompt=system,
        max_tokens=1024,
        timeout=2,  # short timeout - fall back to template if LLM slow
    )

    # Fallback to template if LLM returns nothing
    if not reply or len(reply.strip()) < 10:
        reply = _template_fallback(intent, message, tool_data)

    return reply


def _template_fallback(intent: str, message: str, tool_data: str) -> str:
    """Template-based fallback when LLM is unavailable."""
    import re

    if intent == "查订单":
        order_id = None
        m = re.search(r'ORD\d+', message)
        if m:
            order_id = m.group(0)
        prefix = "为您查询到以下订单信息：\n\n" if order_id else "为您查询到以下订单信息：\n\n"
        return prefix + tool_data + "\n\n💡 如需进一步帮助，请告诉我。"

    if intent == "物流查询":
        return "您的物流信息如下：\n\n" + tool_data + "\n\n💡 可复制单号到快递官网查询详细轨迹。"

    if intent == "退换货":
        return "关于退换货：\n\n" + tool_data + "\n\n💡 已为您创建售后工单，我们会尽快联系您确认。"

    if intent == "投诉":
        return "非常抱歉给您带来不愉快的体验。\n\n您的投诉已受理，我们将在 24 小时内由专人联系您处理。\n\n💡 如需加急，请回复「转人工」。"

    if intent == "FAQ查询":
        return "为您找到以下相关信息：\n\n" + tool_data + "\n\n💡 如果未解决您的问题，我可以转接人工客服。"

    if intent == "售前咨询":
        return "为您推荐以下商品：\n\n" + tool_data + "\n\n💡 告诉我您的预算或具体需求，我可以帮您进一步筛选。"

    if intent == "优惠查询":
        return "为您查询到以下优惠活动：\n\n" + tool_data + "\n\n💡 结账时输入优惠码即可享受折扣。"

    if intent == "地址修改":
        return "关于地址修改：\n\n" + tool_data + "\n\n💡 请提供订单号和新的收货地址，我会帮您修改。"

    if intent == "人工客服":
        return "正在为您转接人工客服，请稍候...\n\n当前排队人数: 2 人\n预计等待: 3-5 分钟\n\n📞 客服热线: 400-800-8888"

    # Chitchat
    msg = message.lower()
    if any(k in msg for k in ["你好", "您好", "hi", "hello"]):
        return (
            "你好！欢迎使用灵枢智能客服 🎉\n\n"
            "我可以帮你：查订单、查物流、退换货、售前咨询、查优惠。\n"
            "请告诉我需要什么帮助？"
        )
    if any(k in msg for k in ["谢谢", "感谢"]):
        return "不客气！如果还有其他问题，随时找我 😊"

    return (
        "您好！请问有什么可以帮您？\n\n"
        "您可以说：\n"
        "📦 「查订单」— 查询订单状态\n"
        "🚚 「查物流」— 追踪配送\n"
        "🔄 「退换货」— 申请售后\n"
        "🛒 「推荐商品」— 售前咨询\n"
        "💰 「优惠」— 查看优惠券\n"
        "👤 「转人工」— 联系真人客服"
    )


def _get_quick_replies(intent: str, tool_results: dict) -> list[str]:
    """Generate contextual quick reply suggestions."""
    base = {
        "查订单": ["查物流", "申请退货", "转人工"],
        "退换货": ["查另一个订单", "退货退款多久到账", "转人工"],
        "物流查询": ["查另一个订单", "配送时效", "投诉", "转人工"],
        "投诉": ["转人工", "查订单", "退货"],
        "FAQ查询": ["查订单", "转人工", "退换货", "优惠"],
        "售前咨询": ["什么分期方案", "优惠券", "对比", "查订单"],
        "优惠查询": ["夏日大促8折", "手机优惠", "查订单", "推荐商品"],
        "地址修改": ["查订单", "配送时效", "转人工"],
        "人工客服": ["查订单", "查物流", "优惠"],
    }
    return base.get(intent, ["查订单", "退换货", "推荐商品", "转人工"])


def _call_cs_llm_stream_tokens(
    message: str, intent: str, tool_data: str, context: str,
) -> list[str]:
    """Call LLM with streaming, return list of tokens.

    Uses requests with stream=True. Returns empty list if LLM fails
    (caller should fall back to template).
    """

    import requests as req_lib

    from agent_harness.core.pipeline.llm import runtime_endpoint, runtime_auth_headers, thinking_payload

    system = (
        "你是灵枢电商平台的智能客服助手。你的风格：专业、耐心、有同理心。\n\n"
        "回复规则:\n"
        "1. 先确认用户需求，再给出具体信息\n"
        "2. 使用自然、友好的语气，像真人客服\n"
        "3. 引用订单数据时要准确\n"
        "4. 在回复末尾提供后续操作建议\n"
        "5. 如果用户表达不满，先道歉再解决问题\n"
        "6. 回复用 Markdown 格式，300 字以内\n"
        "7. 不要提及「系统提示」「工具结果」等内部术语\n"
        "8. 结尾加一句友好的话，如「还有其他需要吗？」\n\n"
        "你可以处理的场景：查订单、查物流、退换货、投诉、FAQ解答、\n"
        "售前咨询（商品推荐/对比/参数）、优惠查询（优惠券/促销活动）、地址修改、转人工。"
    )

    user = (
        "【用户消息】\n{}\n\n"
        "【识别意图】\n{}\n\n"
        "【查询结果】\n{}\n\n"
        "【对话历史】\n{}\n\n"
        "请根据以上信息生成客服回复。"
    ).format(message, intent, tool_data, context or "（无）")

    api_url, model = runtime_endpoint()
    if not api_url:
        return []

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": 1024,
        "temperature": 0.3,
        "stream": True,
        **thinking_payload(),
    }

    tokens: list[str] = []
    try:
        resp = req_lib.post(api_url, json=payload, headers=runtime_auth_headers(api_url), stream=True, timeout=(5, 60))
        for line in resp.iter_lines():
            if line:
                decoded = line.decode("utf-8")
                if decoded.startswith("data: "):
                    data_str = decoded[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        token = delta.get("content", "")
                        if token:
                            tokens.append(token)
                    except json.JSONDecodeError:
                        pass
    except Exception:
        pass

    return tokens
