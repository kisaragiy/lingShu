"""Customer Service Agent — intent → action → respond pipeline.

For interview demo: shows how AI can handle full customer service flow.

Flow:
  1. Receive customer message
  2. Classify intent (查订单/退换货/物流/投诉/人工/FAQ)
  3. Execute action (lookup order, search FAQ, create ticket, etc.)
  4. Generate natural language response
  5. Offer follow-up actions
"""

from .customer_service import (
    cs_lookup_order,
    cs_create_ticket,
    cs_check_ticket,
    cs_search_faq,
    classify_cs_intent,
)


def run_cs_agent(message: str, context: str = "") -> str:
    """Run the customer service agent on a customer message.

    Args:
        message: Customer's message
        context: Previous conversation context

    Returns:
        Agent response
    """
    # Step 1: Classify intent
    intent = classify_cs_intent(message)

    # Step 2: Execute based on intent
    if intent == "人工客服":
        return _handle_human_handoff(message)
    elif intent == "查订单":
        return _handle_order_query(message)
    elif intent == "退换货":
        return _handle_return_exchange(message)
    elif intent == "物流查询":
        return _handle_logistics(message)
    elif intent == "投诉":
        return _handle_complaint(message)
    elif intent == "FAQ查询":
        return _handle_faq(message)
    else:
        return _handle_chitchat(message)


def _handle_order_query(message: str) -> str:
    """Handle order lookup requests."""
    # Try to extract order ID or identifier from message
    import re
    order_id = None

    # Look for ORD-XXXX pattern
    m = re.search(r'ORD\d+', message)
    if m:
        order_id = m.group(0)

    # Look for phone number
    phone = None
    m = re.search(r'1[3-9]\d{9}', message)
    if m:
        phone = m.group(0)

    # Look for name after "我" or "我叫"
    name = None
    m = re.search(r'(?:我|我叫|我是)(.{1,6})[,，。！\s]?', message)
    if m:
        name = m.group(1).strip()

    info = ""
    if order_id:
        info = cs_lookup_order(order_id)
    elif phone:
        info = cs_lookup_order(phone)
    elif name:
        info = cs_lookup_order(name)
    else:
        info = cs_lookup_order("kisaragiy")  # Default demo user

    return (
        "【查订单】\n\n"
        "%s\n\n"
        "💡 *我可以帮你：查物流 / 申请退换货 / 创建售后工单*"
    ) % info


def _handle_return_exchange(message: str) -> str:
    """Handle return/exchange requests."""
    import re
    order_id = None
    m = re.search(r'ORD\d+', message)
    if m:
        order_id = m.group(0)

    result = "【退换货服务】\n\n"
    if order_id:
        order_info = cs_lookup_order(order_id)
        if "未找到" not in order_info:
            result += order_info + "\n\n"

    result += (
        "📋 **退换货政策**\n"
        "• 签收后 7 天内支持无理由退货\n"
        "• 质量问题 30 天内免费换货\n"
        "• 需保持商品完好、配件齐全\n\n"
    )

    if order_id:
        result += "已为您创建退换货工单，我们将尽快联系您确认。\n\n"
        result += cs_create_ticket(order_id, "退换货", message[:100])

    result += "\n\n💡 *需要其他帮助？我可以：查订单 / 联系人工客服*"
    return result


def _handle_logistics(message: str) -> str:
    """Handle logistics tracking."""
    import re
    order_id = None
    m = re.search(r'ORD\d+', message)
    if m:
        order_id = m.group(0)

    result = "【物流查询】\n\n"
    if order_id:
        info = cs_lookup_order(order_id)
        result += info
    else:
        # Show latest order's logistics
        info = cs_lookup_order("ORD20260708001")
        result += info

    result += "\n\n💡 *物流单号已显示在上方，可复制到快递公司官网查询详细轨迹*"
    return result


def _handle_complaint(message: str) -> str:
    """Handle customer complaints."""
    ticket_id = "TK%s%03d" % (__import__('time').strftime("%Y%m%d"), __import__('random').randint(1, 999))
    return (
        "【投诉受理】\n\n"
        "非常抱歉给您带来不愉快的体验。我们已记录您的反馈，并将认真处理。\n\n"
        "✅ **投诉已受理**\n"
        "**受理编号**: %s\n"
        "**预计回复**: 24 小时内专人联系\n\n"
        "💡 *如需加急处理，请回复「转人工」*"
    ) % ticket_id


def _handle_faq(message: str) -> str:
    """Handle FAQ / knowledge base queries."""
    faq_result = cs_search_faq(message)
    if faq_result:
        return (
            "【常见问题】\n\n"
            "%s\n\n"
            "💡 *如果以上未解决你的问题，我可以：转人工客服 / 查询订单*"
        ) % faq_result
    return (
        "【常见问题】\n\n"
        "我尝试搜索了相关知识库，未找到完全匹配的答案。以下是我们常见的服务政策：\n\n"
        "📋 **服务政策速览**\n"
        "• **退货政策**: 签收后 7 天无理由退货\n"
        "• **换货政策**: 质量问题 30 天内免费换货\n"
        "• **售后热线**: 400-800-8888（工作日 9:00-18:00）\n"
        "• **在线客服**: 回复「转人工」联系真人客服\n\n"
        "💡 *我可以帮你查订单 / 处理退换货 / 转接人工*"
    )


def _handle_human_handoff(message: str) -> str:
    """Handle human handoff request."""
    return (
        "【转接人工客服】\n\n"
        "正在为您转接人工客服，请稍候...\n\n"
        "🔄 **排队信息**\n"
        "• 当前排队人数: 2 人\n"
        "• 预计等待时间: 3-5 分钟\n\n"
        "📞 **其他联系方式**\n"
        "• 客服热线: 400-800-8888\n"
        "• 在线时间: 工作日 9:00-18:00\n\n"
        "💡 *等待期间，我可以继续帮你查询订单信息*"
    )


def _handle_chitchat(message: str) -> str:
    """Handle casual chat (greetings, thanks, etc.)."""
    msg = message.lower()
    if any(k in msg for k in ["你好", "您好", "hi", "hello", "在吗", "在不在"]):
        return (
            "你好！欢迎使用智能客服助手 🎉\n\n"
            "我可以帮你：\n"
            "📦 **查订单** — 输入订单号或手机号\n"
            "🚚 **查物流** — 查看配送进度\n"
            "🔄 **退换货** — 申请退换货\n"
            "❓ **常见问题** — 咨询售后政策\n"
            "👤 **转人工** — 联系真人客服\n\n"
            "请告诉我需要什么帮助？"
        )
    if any(k in msg for k in ["谢谢", "感谢", "好的", "ok"]):
        return "不客气！如果还有其他问题，随时找我 😊"
    if any(k in msg for k in ["再见", "拜拜", "bye"]):
        return "感谢您的咨询，祝您生活愉快！再见 👋"

    return (
        "您好！我是灵枢智能客服助手。\n\n"
        "我没完全理解您的问题，请问您是需要：\n"
        "1️⃣ **查订单** — 查询订单状态\n"
        "2️⃣ **查物流** — 追踪配送进度\n"
        "3️⃣ **退换货** — 申请售后\n"
        "4️⃣ **转人工** — 联系真人客服\n\n"
        "请回复数字或直接描述您的问题。"
    )
