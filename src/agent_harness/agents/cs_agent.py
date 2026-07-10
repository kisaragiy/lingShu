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

from .customer_service import (
    cs_lookup_order,
    cs_create_ticket,
    cs_check_ticket,
    cs_search_faq,
    classify_cs_intent,
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
        else:
            results["物流查询"] = cs_lookup_order("ORD20260708001")

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
            "• 需保持商品完好、配件齐全"
        )
        if order_id:
            results["工单"] = cs_create_ticket(order_id, "退换货", message[:100])

    elif intent == "投诉":
        results["投诉受理"] = (
            "投诉已记录，受理编号自动生成，24小时内专人联系。"
        )

    elif intent == "FAQ查询":
        faq = cs_search_faq(message)
        if faq:
            results["FAQ"] = faq
        else:
            results["FAQ"] = "知识库未匹配到精确结果，提供标准服务政策。"
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
    from ..agents.supervisor import _call_llm

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
        "8. 结尾加一句友好的话，如「还有其他需要吗？」"
    )

    user = (
        "【用户消息】\n%s\n\n"
        "【识别意图】\n%s\n\n"
        "【查询结果】\n%s\n\n"
        "【对话历史】\n%s\n\n"
        "请根据以上信息生成客服回复。"
    ) % (message, intent, tool_data, context or "（无）")

    reply = _call_llm(
        [{"role": "user", "content": user}],
        system_prompt=system,
        max_tokens=1024,
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

    if intent == "人工客服":
        return "正在为您转接人工客服，请稍候...\n\n当前排队人数: 2 人\n预计等待: 3-5 分钟\n\n📞 客服热线: 400-800-8888"

    # Chitchat
    msg = message.lower()
    if any(k in msg for k in ["你好", "您好", "hi", "hello"]):
        return (
            "你好！欢迎使用灵枢智能客服 🎉\n\n"
            "我可以帮你：查订单、查物流、退换货、咨询问题。\n"
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
        "👤 「转人工」— 联系真人客服"
    )


def _get_quick_replies(intent: str, tool_results: dict) -> list[str]:
    """Generate contextual quick reply suggestions."""
    base = {
        "查订单": ["查物流", "申请退货", "转人工"],
        "退换货": ["查另一个订单", "转人工"],
        "物流查询": ["查另一个订单", "投诉", "转人工"],
        "投诉": ["转人工", "查订单"],
        "FAQ查询": ["查订单", "转人工", "退换货"],
        "人工客服": ["查订单", "查物流"],
    }
    return base.get(intent, ["查订单", "退换货", "转人工"])
