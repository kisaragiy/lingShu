"""Customer Service Demo — tools and mock data for interview demo.

Expanded v2: 12 orders, product catalog, coupons, address management, delivery estimates.

Scenarios:
  - 查订单: lookup by order_id / phone / name
  - 售前咨询: product catalog, compare, compatibility, installments
  - 优惠查询: available coupons & promotions for user
  - 地址修改: modify shipping address
  - 物流查询: tracking info & estimated delivery
  - 退换货: return/exchange request, refund status
  - 投诉 + FAQ + 转人工
"""

import random
import time

# ═══════════════════════════════════════
# MOCK DATA
# ═══════════════════════════════════════

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
        "address": "广东省广州市天河区中山大道西109号",
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
        "address": "广东省广州市天河区中山大道西109号",
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
        "address": "广东省深圳市南山区科技园南路1号",
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
        "address": "北京市海淀区中关村大街99号",
        "notes": "客户要求定制配置（64GB内存+2TB），需3-5个工作日",
    },
    {
        "order_id": "ORD20260710005",
        "customer": "kisaragiy",
        "phone": "138****1234",
        "product": "苹果 AirPods Pro 2 USB-C",
        "price": 1899,
        "status": "待付款",
        "logistics": "",
        "estimated_delivery": "2026-07-18",
        "created_at": "2026-07-10",
        "address": "广东省广州市天河区中山大道西109号",
        "notes": "订单未付款，请在24小时内完成支付",
    },
    {
        "order_id": "ORD20260709006",
        "customer": "kisaragiy",
        "phone": "138****1234",
        "product": "罗技 G Pro X 游戏键盘",
        "price": 1299,
        "status": "已取消",
        "logistics": "",
        "estimated_delivery": "—",
        "created_at": "2026-07-09",
        "notes": "用户主动取消，退款已原路返还",
    },
    {
        "order_id": "ORD20260620007",
        "customer": "赵六",
        "phone": "137****3456",
        "product": "三星 990 Pro 2TB NVMe SSD",
        "price": 1599,
        "status": "已完成",
        "logistics": "中通快递 ZTO5555555555",
        "estimated_delivery": "2026-06-25",
        "created_at": "2026-06-20",
        "address": "上海市浦东新区张江高科技园区碧波路888号",
    },
    {
        "order_id": "ORD20260711008",
        "customer": "赵六",
        "phone": "137****3456",
        "product": "华硕 ROG Ally 掌机 (2025)",
        "price": 4999,
        "status": "待发货",
        "logistics": "",
        "estimated_delivery": "2026-07-20",
        "created_at": "2026-07-11",
        "address": "上海市浦东新区张江高科技园区碧波路888号",
    },
    {
        "order_id": "ORD20260615009",
        "customer": "孙七",
        "phone": "135****7890",
        "product": "雷蛇 毒蝰 V3 Pro 无线鼠标",
        "price": 799,
        "status": "已完成",
        "logistics": "韵达快递 YD1111111111",
        "estimated_delivery": "2026-06-20",
        "created_at": "2026-06-15",
        "address": "四川省成都市武侯区人民南路四段19号",
        # 仍在退货期内
    },
    {
        "order_id": "ORD20260707010",
        "customer": "孙七",
        "phone": "135****7890",
        "product": "小米14 Ultra 16+512GB 白色",
        "price": 5999,
        "status": "配送中",
        "logistics": "顺丰快递 SF5555555555",
        "estimated_delivery": "2026-07-13",
        "created_at": "2026-07-07",
        "address": "四川省成都市武侯区人民南路四段19号",
    },
    {
        "order_id": "ORD20260601011",
        "customer": "周八",
        "phone": "134****2468",
        "product": "LG 27GP95R 4K 显示器",
        "price": 4499,
        "status": "已完成",
        "logistics": "京东快递 JD999999999",
        "estimated_delivery": "2026-06-05",
        "created_at": "2026-06-01",
        "address": "浙江省杭州市余杭区文一西路998号",
        "notes": "已过退货期（超过30天）",
    },
]

_MOCK_TICKETS = [
    {"ticket_id": "TK20260708001", "order_id": "ORD20260708001", "type": "物流查询", "status": "进行中", "created_at": "2026-07-08"},
    {"ticket_id": "TK20260705001", "order_id": "ORD20260705002", "type": "退换货申请", "status": "已处理", "created_at": "2026-07-05"},
    {"ticket_id": "TK20260710001", "order_id": "ORD20260710005", "type": "支付问题", "status": "待处理", "created_at": "2026-07-10"},
]

# Product catalog for pre-sales inquiries
_PRODUCT_CATALOG = [
    {"id": "P001", "name": "华为MatePad Pro 13.2", "category": "平板电脑", "price": 5699, "stock": True,
     "desc": "13.2英寸OLED柔性屏，麒麟9010芯片，10100mAh电池，支持星闪手写笔",
     "installment": "支持3/6/12期免息（月均¥475起）"},
    {"id": "P002", "name": "苹果 AirPods Pro 2 USB-C", "category": "耳机", "price": 1899, "stock": True,
     "desc": "H2芯片，自适应降噪，自适应音频，IP54防尘抗汗",
     "installment": "支持3/6期免息（月均¥317起）"},
    {"id": "P003", "name": "索尼WH-1000XM5 降噪耳机", "category": "耳机", "price": 2499, "stock": True,
     "desc": "行业顶级降噪，30小时续航，佩戴舒适，支持LDAC高清编码",
     "installment": "支持3/6/12期免息（月均¥208起）"},
    {"id": "P004", "name": "小米14 Ultra 16+512GB", "category": "手机", "price": 5999, "stock": True,
     "desc": "骁龙8 Gen3，徕卡光学Summilux四摄，5300mAh，90W快充",
     "installment": "支持3/6/12/24期免息（月均¥250起）"},
    {"id": "P005", "name": "戴尔XPS 16 笔记本", "category": "笔记本", "price": 12999, "stock": True,
     "desc": "Intel Core Ultra 9，32GB LPDDR5X，RTX 4060，4K OLED触控屏",
     "installment": "支持3/6/12期（月均¥1083起）"},
    {"id": "P006", "name": "华硕 ROG Ally 掌机 (2025)", "category": "游戏", "price": 4999, "stock": True,
     "desc": "AMD Ryzen Z2 Extreme，7英寸1080p 120Hz，1TB SSD，Windows 11",
     "installment": "支持3/6/12期免息（月均¥417起）"},
    {"id": "P007", "name": "雷蛇 毒蝰 V3 Pro 无线鼠标", "category": "外设", "price": 799, "stock": True,
     "desc": "54g超轻量，Focus Pro 30K传感器，90小时续航，8K Hz轮询率",
     "installment": "支持3期免息（月均¥266起）"},
    {"id": "P008", "name": "小米手环9 Pro", "category": "穿戴", "price": 399, "stock": True,
     "desc": "1.74英寸AMOLED屏，GNSS定位，心率血氧监测，21天续航"},
    {"id": "P009", "name": "LG 27GP95R 4K 电竞显示器", "category": "显示器", "price": 4499, "stock": False,
     "desc": "27英寸4K 160Hz，Nano IPS，HDR600，HDMI 2.1，已售罄"},
    {"id": "P010", "name": "三星 990 Pro 2TB NVMe SSD", "category": "存储", "price": 1599, "stock": True,
     "desc": "PCIe 4.0，顺序读取7450MB/s，写入6900MB/s，2TB大容量"},
]

# Available promotions / coupons
_PROMOTIONS = [
    {"code": "SUMMER2026", "name": "夏日大促全场8折", "discount": "8折", "min_amount": 0, "valid_until": "2026-07-31",
     "products": "全场通用", "description": "夏季促销，全场商品8折，部分新品除外"},
    {"code": "NEWUSER50", "name": "新用户满500减50", "discount": "满500减50", "min_amount": 500, "valid_until": "2026-12-31",
     "products": "全场通用", "description": "新注册用户专享，满500元立减50元"},
    {"code": "PHONE300", "name": "手机品类满3000减300", "discount": "满3000减300", "min_amount": 3000, "valid_until": "2026-08-15",
     "products": "手机品类", "description": "购买手机类商品，满3000元立减300元"},
    {"code": "FREESHIP", "name": "全场包邮", "discount": "免运费", "min_amount": 0, "valid_until": "2026-12-31",
     "products": "全场通用", "description": "全场订单免运费，无门槛"},
    {"code": "MATE200", "name": "华为MatePad专属200元券", "discount": "减200", "min_amount": 5000, "valid_until": "2026-07-20",
     "products": "华为MatePad Pro 13.2", "description": "华为MatePad Pro 13.2 专属优惠券"},
]

# Built-in FAQ (no RAG required)
_FAQ_ENTRIES = [
    {"question": "退货政策", "answer": "自签收之日起7天内支持无理由退货（需商品完好、配件齐全）。15天内质量问题可换货。部分商品（如SSD、耳机等个人卫生相关）拆封后不支持无理由退货。"},
    {"question": "保修政策", "answer": "所有商品均享受国家三包服务。品牌商品享受厂家原厂保修（通常1-2年）。我们提供全程售后协助，保修期内免运费维修。"},
    {"question": "发货时间", "answer": "现货商品下单后24小时内发货。定制/预售商品按页面标注时间发货。工作日订单当天处理，周末订单顺延至周一。"},
    {"question": "支付方式", "answer": "支持微信支付、支付宝、银行卡（储蓄卡/信用卡）、花呗分期（3/6/12期免息）。部分商品支持24期免息分期。"},
    {"question": "运费政策", "answer": "全场满99元包邮（港澳台除外）。不满99元运费8元。使用优惠码 FREESHIP 可享免运费。"},
    {"question": "退款时效", "answer": "退货审核通过后，退款将在1-3个工作日内原路返回。信用卡支付可能需要5-7个工作日到账。"},
    {"question": "发票开具", "answer": "支持开具电子发票（默认）和纸质发票。下单时选择发票类型，电子发票将以邮件形式发送。企业发票需提供税号。"},
    {"question": "价格保护", "answer": "自签收之日起7天内，若商品降价，可申请价格保护退还差价。需提供降价截图凭证。"},
    {"question": "以旧换新", "answer": "支持手机、平板、笔记本等品类以旧换新。在线估价后，旧机抵扣款将在新机签收后3个工作日内返还。"},
    {"question": "分期付款", "answer": "支持花呗3/6/12期免息分期，部分商品支持24期。具体期数以商品页面标注为准。分期金额不可使用优惠券。"},
]


# ═══════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════

def _find_order_by_id(order_id: str) -> dict | None:
    for o in _MOCK_ORDERS:
        if o["order_id"].lower() == order_id.lower():
            return dict(o)
    return None


def _find_orders_by_phone(phone: str) -> list[dict]:
    results = []
    phone_clean = phone.replace("-", "").replace(" ", "")
    for o in _MOCK_ORDERS:
        if phone_clean in o["phone"] or phone_clean in o["phone"].replace("*", ""):
            results.append(dict(o))
    return results


def _find_orders_by_name(name: str) -> list[dict]:
    return [dict(o) for o in _MOCK_ORDERS if name.lower() in o["customer"].lower()]


# ═══════════════════════════════════════
# PUBLIC TOOLS
# ═══════════════════════════════════════

def cs_lookup_order(query: str) -> str:
    """Look up order information by order ID, customer name, or phone."""
    order = _find_order_by_id(query.strip())
    if order:
        lines = [
            "📦 **订单信息**",
            "━━━━━━━━━━━━━━━━━",
            "**订单号**: {}".format(order["order_id"]),
            "**商品**: {}".format(order["product"]),
            "**金额**: ¥%d" % order["price"],
            "**状态**: {}".format(order["status"]),
            "**物流**: %s" % (order["logistics"] or "待发货"),
            "**预计送达**: {}".format(order["estimated_delivery"]),
        ]
        if order.get("notes"):
            lines.append("**备注**: {}".format(order["notes"]))
        return "\n".join(lines)

    orders = _find_orders_by_phone(query)
    if orders:
        result = "📋 **找到 %d 个订单**:\n\n" % len(orders)
        for o in orders:
            result += "• `%s` — %s — **%s** — ¥%d\n" % (o["order_id"], o["product"], o["status"], o["price"])
        return result

    orders = _find_orders_by_name(query)
    if orders:
        result = "📋 **找到 %d 个订单**:\n\n" % len(orders)
        for o in orders:
            result += "• `%s` — %s — **%s** — ¥%d\n" % (o["order_id"], o["product"], o["status"], o["price"])
        return result

    return "❌ 未找到匹配的订单。请提供订单号、手机号或姓名。"


def cs_query_product(query: str) -> str:
    """Query the product catalog by name, category, or keyword.

    Supports: product lookup, category browse, price comparison.
    """
    results = []
    q = query.lower()

    # Try exact name match first
    for p in _PRODUCT_CATALOG:
        if p["name"].lower() == q or p["id"].lower() == q:
            stock_icon = "✅ 有货" if p["stock"] else "❌ 已售罄"
            lines = [
                "🛒 **{}**".format(p["name"]),
                "━━━━━━━━━━━━━━━━━",
                "**价格**: ¥%d" % p["price"],
                f"**库存**: {stock_icon}",
                "**分类**: {}".format(p["category"]),
                "**简介**: {}".format(p["desc"]),
            ]
            if p.get("installment"):
                lines.append("**分期**: {}".format(p["installment"]))
            return "\n".join(lines)

    # Partial match by name
    for p in _PRODUCT_CATALOG:
        if q in p["name"].lower() or q in p["category"].lower():
            results.append(p)

    # General keyword match
    if not results:
        for p in _PRODUCT_CATALOG:
            if q in p["desc"].lower():
                results.append(p)

    if results:
        result = "🔍 **找到 %d 件相关商品**:\n\n" % len(results)
        for p in results:
            stock = "✅" if p["stock"] else "❌"
            result += "• `%s` — ¥%d %s\n   %s\n" % (p["name"], p["price"], stock, p["desc"][:60] + "...")
        return result

    return f"❌ 未找到相关商品 `{query}`。试试其他关键词。"


def cs_check_promotion(query: str = "") -> str:
    """Check available promotions, coupons, and discounts.

    Args:
        query: Optional keyword to filter promotions

    Returns:
        Available promotions list
    """
    now = time.strftime("%Y-%m-%d")
    available = []
    for p in _PROMOTIONS:
        if p["valid_until"] >= now:
            available.append(p)

    if not available:
        return "当前没有可用优惠活动。"

    if query:
        q = query.lower()
        filtered = [p for p in available if q in p["name"].lower() or q in p["products"].lower() or q in p["code"].lower()]
        if filtered:
            available = filtered
        # else show all (query didn't match)

    result = "🎉 **当前可用优惠**\n"
    result += "━━━━━━━━━━━━━━━━━\n"
    for p in available:
        result += "\n**{}** (`{}`)\n".format(p["name"], p["code"])
        result += "└ {} | 有效期至 {}\n".format(p["discount"], p["valid_until"])
        result += "└ 适用: {}\n".format(p["products"])
    result += "\n💡 结账时输入优惠码即可使用。"
    return result


def cs_estimate_delivery(order_id: str = "") -> str:
    """Estimate delivery time for an order or general delivery inquiry."""
    if order_id:
        order = _find_order_by_id(order_id)
        if not order:
            return f"❌ 未找到订单 `{order_id}`。"

        status = order["status"]
        if status in ("已签收", "已完成"):
            return f"✅ 订单 `{order_id}` 已于预计日期前送达。如未收到，请联系客服。"
        elif status == "已取消":
            return f"ℹ️ 订单 `{order_id}` 已取消，无需配送。"
        elif status == "待付款":
            return f"⚠️ 订单 `{order_id}` 尚未付款，请完成支付后安排发货。"
        elif order.get("logistics"):
            return "🚚 订单 `{}` 配送中（{}），预计 **{}** 前送达。".format(
                order_id, order["logistics"], order["estimated_delivery"])
        else:
            return "📦 订单 `{}` 正在准备中，预计 **{}** 前发货。".format(order_id, order["estimated_delivery"])
    else:
        return (
            "📬 **配送时效参考**\n"
            "━━━━━━━━━━━━━━━━━\n"
            "• **同城配送**: 1-2个工作日\n"
            "• **省内**: 2-3个工作日\n"
            "• **跨省**: 3-5个工作日\n"
            "• **偏远地区**: 5-7个工作日\n\n"
            "🚚 合作快递: 顺丰、京东、圆通、中通、韵达\n"
            "💡 下单时间 16:00 前，当天发货。"
        )


def cs_modify_address(order_id: str, new_address: str) -> str:
    """Modify the shipping address for an order.

    Only allowed for 待发货 and 待付款 orders.
    """
    order = _find_order_by_id(order_id)
    if not order:
        return f"❌ 未找到订单 `{order_id}`。"

    if order["status"] not in ("待发货", "待付款"):
        return "❌ 订单 `{}` 当前状态为「{}」，已无法修改地址。\n已发货订单请联系物流公司改址。".format(order_id, order["status"])

    # Simulate address change
    old_addr = order["address"]
    return (
        "✅ **地址修改成功**\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"**订单**: `{order_id}`\n"
        f"**原地址**: {old_addr}\n"
        f"**新地址**: {new_address}\n\n"
        "新地址将在下次配送时生效。"
    )


def cs_search_faq(query: str, top_k: int = 3) -> str:
    """Search the built-in FAQ for relevant answers."""
    q = query.lower()
    scored = []
    for entry in _FAQ_ENTRIES:
        score = 0
        for kw in q.split():
            if kw in entry["question"].lower():
                score += 3
            if kw in entry["answer"].lower():
                score += 1
        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda x: -x[0])
    results = scored[:top_k]

    if results:
        items = []
        for _, entry in results:
            items.append("**❓ {}**\n{}".format(entry["question"], entry["answer"]))
        return "\n\n".join(items)

    # Try RAG as fallback
    try:
        from ..tools.rag_store import query as rag_query
        rag_results = rag_query(query, collection="cs_faq", top_k=top_k)
        if rag_results:
            items = [r["text"][:300] for r in rag_results]
            return "\n\n".join(items)
    except Exception:
        pass

    return None  # No FAQ found


def cs_create_ticket(order_id: str, issue_type: str, description: str) -> str:
    """Create a customer service ticket."""
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
        "**工单号**: {}\n"
        "**类型**: {}\n"
        "**状态**: {}\n"
        "**时间**: {}\n\n"
        "我们会尽快处理您的请求，预计在 24 小时内回复。"
    ).format(ticket_id, issue_type, ticket["status"], ticket["created_at"])


def cs_check_ticket(ticket_id: str) -> str:
    """Check the status of a ticket."""
    for t in _MOCK_TICKETS:
        if t["ticket_id"].lower() == ticket_id.lower():
            return (
                "🎫 **工单状态**\n"
                "━━━━━━━━━━━━━━━━━\n"
                "**工单号**: {}\n"
                "**类型**: {}\n"
                "**状态**: {}\n"
                "**创建时间**: {}\n"
            ).format(t["ticket_id"], t["type"], t["status"], t["created_at"])
    return f"❌ 未找到工单 `{ticket_id}`，请检查工单号是否正确。"


# ═══════════════════════════════════════
# INTENT CLASSIFICATION
# ═══════════════════════════════════════

CS_INTENTS = [
    "查订单", "退换货", "物流查询", "投诉",
    "人工客服", "FAQ查询", "售前咨询", "优惠查询",
    "地址修改", "闲聊",
]


def classify_cs_intent(message: str) -> str:
    """Rule-based intent classification with expanded scenarios."""
    msg = message.lower()

    # ── 地址修改 ──
    if any(k in msg for k in ["改地址", "换地址", "改收货", "修改地址", "换收货", "送到别处",
                               "换个地址", "地址错了", "地址变更"]):
        return "地址修改"

    # ── 售前咨询 ──
    if any(k in msg for k in ["推荐", "有什么", "介绍", "对比", "区别", "哪个好", "适合",
                               "参数", "配置", "怎么样", "好不好", "值得", "能做什么",
                               "支持", "兼容", "配套"]):
        if any(k in msg for k in ["优惠", "券", "打折", "促销", "活动", "减", "便宜"]):
            pass  # fall through to 优惠查询
        else:
            return "售前咨询"

    # ── 优惠查询 ──
    if any(k in msg for k in ["优惠", "券", "优惠券", "打折", "促销", "活动", "满减",
                               "减免", "折扣", "省钱", "特价", "划算"]):
        return "优惠查询"

    # ── Order queries ──
    if any(k in msg for k in ["订单", "ord", "买", "下单", "购买", "付款", "支付"]):
        if any(k in msg for k in ["退", "换", "取消", "拒收"]):
            return "退换货"
        if any(k in msg for k in ["物流", "快递", "发货", "配送", "送到", "到哪", "多久"]):
            return "物流查询"
        return "查订单"

    # ── Returns/Exchanges ──
    if any(k in msg for k in ["退货", "换货", "退款", "退钱", "不想要", "质量问题",
                               "坏了", "破损", "瑕疵", "不满意"]):
        return "退换货"

    # ── Logistics ──
    if any(k in msg for k in ["物流", "快递", "送货", "配送", "运输", "到哪",
                               "什么时候到", "预计到达", "几天到"]):
        return "物流查询"

    # ── Complaints ──
    if any(k in msg for k in ["投诉", "举报", "差评", "不满", "生气", "态度差",
                               "投诉", "太慢", "太差", "垃圾"]):
        return "投诉"

    # ── Human handoff ──
    if any(k in msg for k in ["人工", "客服", "转人工", "真人", "人类", "接线员",
                               "专员", "找人工", "活人"]):
        return "人工客服"

    # ── FAQ / general ──
    if any(k in msg for k in ["怎么", "如何", "?", "？", "什么", "吗", "能", "可以",
                               "规则", "政策", "保修", "售后", "发票", "运费",
                               "能退", "能换", "多久到"]):
        return "FAQ查询"

    return "闲聊"
