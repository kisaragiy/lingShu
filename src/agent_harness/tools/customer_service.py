"""Customer Service Demo — tools and mock data for interview demo.

Features:
- Mock order database (lookup by order_id or phone)
- Mock ticket system (create, check status)
- FAQ lookup via RAG (reuses existing rag_store)
- Intent classification prompt

Usage (interview demo flow):
  1. Upload FAQ.pdf → RAG indexes it
  2. Customer asks "我的订单怎么还没到"
  3. System: classify intent → lookup order → check FAQ → respond
"""

import json
import random
import time

# ─── Mock Order Database ───

_MOCK_ORDERS = [
    {
        "order_id": "ORD20260708001",
        "customer": "kisaragiy",
        "phone": "138****1234",
        "product": "华为MatePad Pro 13.2",
        "price": 5699,
        "status": "已发货",
        "logistics": "顺丰快递 SF1234567890",
        "estimated_delivery": "2026-07-12",
        "created_at": "2026-07-08",
    },
    {
        "order_id": "ORD20260705002",
        "customer": "kisaragiy",
        "phone": "138****1234",
        "product": "小米手环9 Pro",
        "price": 399,
        "status": "已签收",
        "logistics": "圆通快递 YT9876543210",
        "estimated_delivery": "2026-07-07",
        "created_at": "2026-07-05",
    },
    {
        "order_id": "ORD20260701003",
        "customer": "李四",
        "phone": "139****5678",
        "product": "索尼WH-1000XM5 降噪耳机",
        "price": 2499,
        "status": "配送中",
        "logistics": "京东快递 JD123456789",
        "estimated_delivery": "2026-07-10",
        "created_at": "2026-07-01",
    },
    {
        "order_id": "ORD20260628004",
        "customer": "王五",
        "phone": "136****9012",
        "product": "戴尔XPS 16 笔记本",
        "price": 12999,
        "status": "待发货",
        "logistics": "",
        "estimated_delivery": "2026-07-15",
        "created_at": "2026-06-28",
        "notes": "客户要求定制配置，需3-5个工作日",
    },
]

_MOCK_TICKETS = [
    {"ticket_id": "TK20260708001", "order_id": "ORD20260708001", "type": "物流查询", "status": "进行中", "created_at": "2026-07-08"},
    {"ticket_id": "TK20260705001", "order_id": "ORD20260705002", "type": "退换货申请", "status": "已处理", "created_at": "2026-07-05"},
]


def _find_order_by_id(order_id: str) -> dict | None:
    for o in _MOCK_ORDERS:
        if o["order_id"].lower() == order_id.lower():
            return dict(o)
    return None


def _find_orders_by_phone(phone: str) -> list[dict]:
    # Support partial phone matching (last 4 digits)
    results = []
    phone_clean = phone.replace("-", "").replace(" ", "")
    for o in _MOCK_ORDERS:
        if phone_clean in o["phone"] or phone_clean in o["phone"].replace("*", ""):
            results.append(dict(o))
    return results


def _find_orders_by_name(name: str) -> list[dict]:
    return [dict(o) for o in _MOCK_ORDERS if name.lower() in o["customer"].lower()]


# ─── Public Tools ───

def cs_lookup_order(query: str) -> str:
    """Look up order information by order ID, customer name, or phone.

    Args:
        query: Order ID (ORD...) or customer name or phone number

    Returns:
        Formatted order info or not-found message
    """
    # Try order ID first
    order = _find_order_by_id(query.strip())
    if order:
        lines = [
            "📦 **订单信息**",
            "━━━━━━━━━━━━━━━━━",
            "**订单号**: %s" % order["order_id"],
            "**商品**: %s" % order["product"],
            "**金额**: ¥%d" % order["price"],
            "**状态**: %s" % order["status"],
            "**物流**: %s" % (order["logistics"] or "待发货"),
            "**预计送达**: %s" % order["estimated_delivery"],
        ]
        if order.get("notes"):
            lines.append("**备注**: %s" % order["notes"])
        return "\n".join(lines)

    # Try phone
    orders = _find_orders_by_phone(query)
    if orders:
        result = "📋 **找到 %d 个订单**:\n\n" % len(orders)
        for o in orders:
            result += "• `%s` — %s — **%s** — ¥%d\n" % (o["order_id"], o["product"], o["status"], o["price"])
        return result

    # Try name
    orders = _find_orders_by_name(query)
    if orders:
        result = "📋 **找到 %d 个订单**:\n\n" % len(orders)
        for o in orders:
            result += "• `%s` — %s — **%s** — ¥%d\n" % (o["order_id"], o["product"], o["status"], o["price"])
        return result

    return "❌ 未找到匹配的订单。请提供订单号、手机号或姓名。"


def cs_create_ticket(order_id: str, issue_type: str, description: str) -> str:
    """Create a customer service ticket.

    Args:
        order_id: Related order ID
        issue_type: 退换货/物流查询/投诉/售后/其他
        description: Issue description

    Returns:
        Ticket info
    """
    ticket_id = "TK%s%03d" % (time.strftime("%Y%m%d"), random.randint(1, 999))
    ticket = {
        "ticket_id": ticket_id,
        "order_id": order_id,
        "type": issue_type,
        "status": "已提交",
        "description": description[:200],
        "created_at": time.strftime("%Y-%m-%d %H:%M"),
    }
    _MOCK_TICKETS.insert(0, ticket)
    return (
        "✅ **工单已创建**\n"
        "━━━━━━━━━━━━━━━━━\n"
        "**工单号**: %s\n"
        "**类型**: %s\n"
        "**状态**: %s\n"
        "**时间**: %s\n\n"
        "我们会尽快处理您的请求，预计在 24 小时内回复。"
    ) % (ticket_id, issue_type, ticket["status"], ticket["created_at"])


def cs_check_ticket(ticket_id: str) -> str:
    """Check the status of a ticket.

    Args:
        ticket_id: Ticket ID (TK...)

    Returns:
        Ticket status info
    """
    for t in _MOCK_TICKETS:
        if t["ticket_id"].lower() == ticket_id.lower():
            return (
                "🎫 **工单状态**\n"
                "━━━━━━━━━━━━━━━━━\n"
                "**工单号**: %s\n"
                "**类型**: %s\n"
                "**状态**: %s\n"
                "**创建时间**: %s\n"
            ) % (t["ticket_id"], t["type"], t["status"], t["created_at"])
    return "❌ 未找到工单 `%s`，请检查工单号是否正确。" % ticket_id


def cs_search_faq(query: str, top_k: int = 3) -> str:
    """Search the FAQ knowledge base for relevant answers.

    Requires documents to be uploaded to the 'cs_faq' RAG collection first.
    Falls back to a built-in FAQ if no RAG collection exists.

    Args:
        query: Customer question
        top_k: Max results

    Returns:
        FAQ answers
    """
    # Try RAG first
    try:
        from ..tools.rag_store import query as rag_query
        results = rag_query(query, collection="cs_faq", top_k=top_k)
        if results:
            items = []
            for r in results:
                items.append(r["text"][:300])
            return "\n\n".join(items)
    except Exception:
        pass

    # Fallback: built-in FAQ
    return None  # Signal no FAQ found


# ─── Intent Classification ───

CS_INTENTS = [
    "查订单", "退换货", "物流查询", "投诉", "人工客服", "FAQ查询", "闲聊"
]


def classify_cs_intent(message: str) -> str:
    """Simple rule-based intent classification for customer service.

    Args:
        message: Customer message

    Returns:
        Intent name from CS_INTENTS
    """
    msg = message.lower()

    # Order queries
    if any(k in msg for k in ["订单", "ord", "买", "下单", "购买", "付款", "支付"]):
        if any(k in msg for k in ["退", "换", "取消", "拒收"]):
            return "退换货"
        if any(k in msg for k in ["物流", "快递", "发货", "配送", "送到", "到哪", "多久"]):
            return "物流查询"
        return "查订单"

    # Returns/Exchanges
    if any(k in msg for k in ["退货", "换货", "退款", "退钱", "不想要", "质量问题"]):
        return "退换货"

    # Logistics
    if any(k in msg for k in ["物流", "快递", "送货", "配送", "运输", "到哪"]):
        return "物流查询"

    # Complaints
    if any(k in msg for k in ["投诉", "举报", "差评", "不满", "生气", "态度差"]):
        return "投诉"

    # Human handoff
    if any(k in msg for k in ["人工", "客服", "转人工", "真人", "人类", "接线员", "专员"]):
        return "人工客服"

    # FAQ / general questions
    if any(k in msg for k in ["怎么", "如何", "?", "？", "什么", "吗", "能", "可以", "规则", "政策", "保修", "售后"]):
        return "FAQ查询"

    return "闲聊"
