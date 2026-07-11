"""CS Demo 独立服务器 — 仅包含客服 Demo 路由，无 LingShu 全量依赖。

用于 PyInstaller 打包为独立 exe，体积 ~50MB 而非 800MB+。

入口点：cs_demo.server:app
"""
import json
import os
import random
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="灵枢智能客服 Demo", version="0.44.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── In-memory session store ───
_sessions: dict[str, list[dict]] = {}

# Static file path: handle both dev and PyInstaller bundled modes
def _find_static_dir() -> Path:
    """Locate the static directory in dev or PyInstaller bundle."""
    # PyInstaller extracts to sys._MEIPASS
    import sys
    try:
        base = Path(sys._MEIPASS)
        candidate = base / "cs_demo" / "static"
        if candidate.exists():
            return candidate
    except AttributeError:
        pass
    # Dev mode: relative to this file's project root
    return Path(__file__).resolve().parent.parent.parent / "src" / "agent_harness" / "static"

STATIC_DIR = _find_static_dir()


# ═══════════════════════════════════════
# TOOLS (inlined from customer_service.py)
# ═══════════════════════════════════════

_MOCK_ORDERS = [
    {"order_id": "ORD20260708001", "customer": "kisaragiy", "phone": "138****1234",
     "product": "华为MatePad Pro 13.2", "price": 5699, "status": "已发货",
     "logistics": "顺丰快递 SF1234567890", "estimated_delivery": "2026-07-12",
     "created_at": "2026-07-08", "address": "广东省广州市天河区中山大道西109号"},
    {"order_id": "ORD20260705002", "customer": "kisaragiy", "phone": "138****1234",
     "product": "小米手环9 Pro", "price": 399, "status": "已签收",
     "logistics": "圆通快递 YT9876543210", "estimated_delivery": "2026-07-07",
     "created_at": "2026-07-05", "address": "广东省广州市天河区中山大道西109号"},
    {"order_id": "ORD20260701003", "customer": "李四", "phone": "139****5678",
     "product": "索尼WH-1000XM5 降噪耳机", "price": 2499, "status": "配送中",
     "logistics": "京东快递 JD123456789", "estimated_delivery": "2026-07-10",
     "created_at": "2026-07-01", "address": "广东省深圳市南山区科技园南路1号"},
    {"order_id": "ORD20260628004", "customer": "王五", "phone": "136****9012",
     "product": "戴尔XPS 16 笔记本", "price": 12999, "status": "待发货",
     "logistics": "", "estimated_delivery": "2026-07-15", "created_at": "2026-06-28",
     "address": "北京市海淀区中关村大街99号", "notes": "定制配置（64GB+2TB），需3-5个工作日"},
    {"order_id": "ORD20260710005", "customer": "kisaragiy", "phone": "138****1234",
     "product": "苹果 AirPods Pro 2 USB-C", "price": 1899, "status": "待付款",
     "logistics": "", "estimated_delivery": "2026-07-18", "created_at": "2026-07-10",
     "address": "广东省广州市天河区中山大道西109号", "notes": "24小时内完成支付"},
    {"order_id": "ORD20260709006", "customer": "kisaragiy", "phone": "138****1234",
     "product": "罗技 G Pro X 游戏键盘", "price": 1299, "status": "已取消",
     "logistics": "", "estimated_delivery": "—", "created_at": "2026-07-09",
     "notes": "用户主动取消，退款已原路返还"},
    {"order_id": "ORD20260620007", "customer": "赵六", "phone": "137****3456",
     "product": "三星 990 Pro 2TB NVMe SSD", "price": 1599, "status": "已完成",
     "logistics": "中通快递 ZTO5555555555", "estimated_delivery": "2026-06-25",
     "created_at": "2026-06-20", "address": "上海市浦东新区张江碧波路888号"},
    {"order_id": "ORD20260711008", "customer": "赵六", "phone": "137****3456",
     "product": "华硕 ROG Ally 掌机 (2025)", "price": 4999, "status": "待发货",
     "logistics": "", "estimated_delivery": "2026-07-20", "created_at": "2026-07-11",
     "address": "上海市浦东新区张江碧波路888号"},
    {"order_id": "ORD20260615009", "customer": "孙七", "phone": "135****7890",
     "product": "雷蛇 毒蝰 V3 Pro 无线鼠标", "price": 799, "status": "已完成",
     "logistics": "韵达快递 YD1111111111", "estimated_delivery": "2026-06-20",
     "created_at": "2026-06-15", "address": "四川省成都市武侯区人民南路四段19号"},
    {"order_id": "ORD20260707010", "customer": "孙七", "phone": "135****7890",
     "product": "小米14 Ultra 16+512GB 白色", "price": 5999, "status": "配送中",
     "logistics": "顺丰快递 SF5555555555", "estimated_delivery": "2026-07-13",
     "created_at": "2026-07-07", "address": "四川省成都市武侯区人民南路四段19号"},
    {"order_id": "ORD20260601011", "customer": "周八", "phone": "134****2468",
     "product": "LG 27GP95R 4K 显示器", "price": 4499, "status": "已完成",
     "logistics": "京东快递 JD999999999", "estimated_delivery": "2026-06-05",
     "created_at": "2026-06-01", "address": "浙江省杭州市余杭区文一西路998号",
     "notes": "已过退货期"},
]

_PRODUCT_CATALOG = [
    {"id": "P001", "name": "华为MatePad Pro 13.2", "category": "平板电脑", "price": 5699,
     "desc": "13.2英寸OLED，麒麟9010，10100mAh", "installment": "3/6/12期免息（月均¥475起）", "stock": True},
    {"id": "P002", "name": "苹果 AirPods Pro 2 USB-C", "category": "耳机", "price": 1899,
     "desc": "H2芯片，自适应降噪，IP54", "installment": "3/6期免息（月均¥317起）", "stock": True},
    {"id": "P003", "name": "索尼WH-1000XM5 降噪耳机", "category": "耳机", "price": 2499,
     "desc": "行业顶级降噪，30h续航，LDAC", "installment": "3/6/12期免息（月均¥208起）", "stock": True},
    {"id": "P004", "name": "小米14 Ultra 16+512GB", "category": "手机", "price": 5999,
     "desc": "骁龙8 Gen3，徕卡四摄，5300mAh", "installment": "3/6/12/24期免息", "stock": True},
    {"id": "P005", "name": "戴尔XPS 16 笔记本", "category": "笔记本", "price": 12999,
     "desc": "Ultra 9+32GB+RTX 4060+4K OLED", "installment": "3/6/12期（月均¥1083起）", "stock": True},
    {"id": "P006", "name": "华硕 ROG Ally 掌机 (2025)", "category": "游戏", "price": 4999,
     "desc": "Z2 Extreme，7\"1080p 120Hz，1TB", "installment": "3/6/12期免息（月均¥417起）", "stock": True},
    {"id": "P007", "name": "雷蛇 毒蝰 V3 Pro 无线鼠标", "category": "外设", "price": 799,
     "desc": "54g超轻，Focus Pro 30K，90h续航", "installment": "3期免息（月均¥266起）", "stock": True},
    {"id": "P008", "name": "小米手环9 Pro", "category": "穿戴", "price": 399,
     "desc": "1.74\"AMOLED，GNSS，21天续航", "stock": True},
    {"id": "P010", "name": "三星 990 Pro 2TB NVMe SSD", "category": "存储", "price": 1599,
     "desc": "PCIe 4.0，读7450MB/s，写6900MB/s", "stock": True},
]

_PROMOTIONS = [
    {"code": "SUMMER2026", "name": "夏日大促全场8折", "discount": "8折", "min_amount": 0,
     "valid_until": "2026-07-31", "products": "全场通用"},
    {"code": "NEWUSER50", "name": "新用户满500减50", "discount": "满500减50", "min_amount": 500,
     "valid_until": "2026-12-31", "products": "全场通用"},
    {"code": "PHONE300", "name": "手机满3000减300", "discount": "满3000减300", "min_amount": 3000,
     "valid_until": "2026-08-15", "products": "手机品类"},
    {"code": "FREESHIP", "name": "全场包邮", "discount": "免运费", "min_amount": 0,
     "valid_until": "2026-12-31", "products": "全场通用"},
    {"code": "MATE200", "name": "华为MatePad专属200", "discount": "减200", "min_amount": 5000,
     "valid_until": "2026-07-20", "products": "华为MatePad Pro 13.2"},
]

_FAQ = [
    {"q": "退货政策", "a": "签收7天内无理由退货（商品完好）。15天质量问题可换货。"},
    {"q": "保修政策", "a": "品牌原厂保修1-2年，全程售后协助。保内免运费维修。"},
    {"q": "发货时间", "a": "现货24小时内发货。定制/预售按页面标注。工作日当天处理。"},
    {"q": "支付方式", "a": "微信、支付宝、银行卡、花呗分期（3/6/12期免息）。"},
    {"q": "运费政策", "a": "全场满99元包邮（港澳台除外）。不满99元运费8元。"},
    {"q": "退款时效", "a": "审核通过后1-3个工作日原路返回。信用卡5-7个工作日。"},
    {"q": "发票开具", "a": "电子发票默认，企业发票需税号。下单时选择。"},
    {"q": "价格保护", "a": "签收7天内降价可申请保价退款，需降价截图凭证。"},
    {"q": "分期付款", "a": "花呗3/6/12期免息，部分24期。分期不可用优惠券。"},
]

_MOCK_TICKETS = [
    {"ticket_id": "TK20260708001", "order_id": "ORD20260708001", "type": "物流查询", "status": "进行中",
     "created_at": "2026-07-08"},
    {"ticket_id": "TK20260705001", "order_id": "ORD20260705002", "type": "退换货申请", "status": "已处理",
     "created_at": "2026-07-05"},
]

CS_INTENTS = ["查订单", "退换货", "物流查询", "投诉", "人工客服", "FAQ查询", "售前咨询", "优惠查询", "地址修改", "闲聊"]


def _find_order(q: str):
    qs = q.strip().lower()
    for o in _MOCK_ORDERS:
        if o["order_id"].lower() == qs or qs in o["phone"] or qs in o["customer"].lower():
            return dict(o)
    for o in _MOCK_ORDERS:
        if q in o["customer"].lower():
            return dict(o)
    return None


def classify_intent(msg: str) -> str:
    m = msg.lower()
    if any(k in m for k in ["改地址", "换地址", "改收货", "修改地址", "地址错了"]):
        return "地址修改"
    if any(k in m for k in ["推荐", "介绍", "对比", "哪个好", "怎么样", "参数", "兼容"]):
        if not any(k in m for k in ["优惠", "券", "打折"]):
            return "售前咨询"
    if any(k in m for k in ["优惠", "券", "优惠券", "打折", "促销", "活动", "满减", "折扣"]):
        return "优惠查询"
    if any(k in m for k in ["订单", "ord", "买", "下单", "付款", "支付"]):
        if any(k in m for k in ["退", "换", "取消"]):
            return "退换货"
        if any(k in m for k in ["物流", "快递", "发货", "配送", "到哪"]):
            return "物流查询"
        return "查订单"
    if any(k in m for k in ["退货", "换货", "退款", "不想要", "质量问题", "坏了"]):
        return "退换货"
    if any(k in m for k in ["物流", "快递", "送货", "配送", "到哪", "什么时候到"]):
        return "物流查询"
    if any(k in m for k in ["投诉", "举报", "不满", "态度差", "太慢", "太差"]):
        return "投诉"
    if any(k in m for k in ["人工", "客服", "转人工", "真人", "专员"]):
        return "人工客服"
    if any(k in m for k in ["怎么", "如何", "?", "？", "什么", "吗", "可以", "规则", "政策", "保修"]):
        return "FAQ查询"
    return "闲聊"


def _fmt_order(o: dict) -> str:
    lines = [
        "📦 **订单信息**", "━━━━━━━━━━━━━━━━━",
        "**订单号**: %s" % o["order_id"],
        "**商品**: %s" % o["product"],
        "**金额**: ¥%d" % o["price"],
        "**状态**: %s" % o["status"],
        "**物流**: %s" % (o.get("logistics") or "待发货"),
        "**预计送达**: %s" % o["estimated_delivery"],
    ]
    if o.get("notes"):
        lines.append("**备注**: %s" % o["notes"])
    return "\n".join(lines)


def execute_tools(intent: str, msg: str) -> dict[str, str]:
    results = {}

    if intent == "查订单":
        m = re.search(r'ORD\d+', msg)
        if m:
            o = _find_order(m.group(0))
            results["订单查询"] = _fmt_order(o) if o else "❌ 未找到订单"
        else:
            o = _find_order("kisaragiy")
            results["订单查询"] = _fmt_order(o) if o else "❌ 未找到订单"

    elif intent == "物流查询":
        m = re.search(r'ORD\d+', msg)
        oid = m.group(0) if m else "ORD20260708001"
        o = _find_order(oid)
        results["物流查询"] = _fmt_order(o) if o else "❌ 未找到订单"
        results["配送时效"] = "🚚 预计 **%s** 前送达" % (o["estimated_delivery"] if o else "3-5个工作日")

    elif intent == "退换货":
        m = re.search(r'ORD\d+', msg)
        if m:
            o = _find_order(m.group(0))
            if o:
                results["订单信息"] = _fmt_order(o)
                tid = "TK%s%03d" % (time.strftime("%Y%m%d"), random.randint(1, 999))
                results["工单"] = "✅ 工单 %s 已创建" % tid
        results["退换货政策"] = ("退换货政策:\n• 7天无理由退货\n• 质量问题30天换货\n"
                                "• 需商品完好配件齐全\n• 退款1-3个工作日")

    elif intent == "售前咨询":
        kw = re.sub(r'[推荐介绍看看怎么样好不好对比]', '', msg).strip().rstrip('?？的')
        if kw and len(kw) > 1:
            matched = [p for p in _PRODUCT_CATALOG if kw in p["name"] or kw in p["category"]]
            if matched:
                items = []
                for p in matched:
                    stock = "✅" if p["stock"] else "❌"
                    items.append("• `%s` — ¥%d %s" % (p["name"], p["price"], stock))
                results["商品查询"] = "\n".join(items)
            else:
                results["商品查询"] = "未找到匹配商品"
        else:
            results["热门推荐"] = ("🔥 **热门推荐**\n"
                                  "1. 小米14 Ultra ¥5,999\n2. 索尼WH-1000XM5 ¥2,499\n"
                                  "3. 华硕ROG Ally ¥4,999\n4. 苹果AirPods Pro 2 ¥1,899")
        results["优惠信息"] = "💡 结账可用优惠码: SUMMER2026(8折) / FREESHIP(免运费)"

    elif intent == "优惠查询":
        now = time.strftime("%Y-%m-%d")
        avail = [p for p in _PROMOTIONS if p["valid_until"] >= now]
        lines = ["🎉 **当前可用优惠**", "━━━━━━━━━━━━━━━━━"]
        for p in avail:
            lines.append("\n**%s** (`%s`)" % (p["name"], p["code"]))
            lines.append("└ %s | 有效期至 %s" % (p["discount"], p["valid_until"]))
        results["优惠信息"] = "\n".join(lines)

    elif intent == "地址修改":
        m = re.search(r'ORD\d+', msg)
        if m:
            o = _find_order(m.group(0))
            results["当前订单"] = _fmt_order(o) if o else "❌ 未找到订单"
        results["地址修改指南"] = ("📋 **地址修改说明**\n"
                                "• 仅「待发货」和「待付款」可修改\n"
                                "• 已发货请联系物流公司改址\n"
                                "• 请提供完整新地址")

    elif intent == "投诉":
        results["投诉受理"] = "投诉已记录，24小时专人联系。"
        m = re.search(r'ORD\d+', msg)
        if m:
            o = _find_order(m.group(0))
            if o:
                results["订单信息"] = _fmt_order(o)

    elif intent == "FAQ查询":
        q = msg.lower()
        scored = []
        for faq in _FAQ:
            score = sum(1 for kw in q.split() if kw in faq["q"].lower() or kw in faq["a"].lower())
            if score > 0:
                scored.append((score, faq))
        scored.sort(key=lambda x: -x[0])
        if scored:
            results["FAQ"] = "\n\n".join("**❓ %s**\n%s" % (f["q"], f["a"]) for _, f in scored[:3])
        else:
            results["FAQ"] = "未找到精确匹配，可转人工咨询。"

    elif intent == "人工客服":
        results["转人工"] = "排队中，预计3-5分钟。热线: 400-800-8888"

    return results


def get_quick_replies(intent: str) -> list:
    base = {
        "查订单": ["查物流", "申请退货", "转人工"],
        "退换货": ["查另一个订单", "退货退款多久到账", "转人工"],
        "物流查询": ["配送时效", "查另一个订单", "投诉", "转人工"],
        "投诉": ["转人工", "查订单"],
        "FAQ查询": ["查订单", "转人工", "退换货", "优惠"],
        "售前咨询": ["什么分期方案", "优惠券", "对比", "查订单"],
        "优惠查询": ["夏日大促8折", "手机优惠", "查订单"],
        "地址修改": ["查订单", "配送时效", "转人工"],
        "人工客服": ["查订单", "查物流"],
    }
    return base.get(intent, ["查订单", "退换货", "推荐商品", "转人工"])


def template_fallback(intent: str, tool_data: str) -> str:
    templates = {
        "查订单": "为您查询到以下信息：\n\n%s\n\n💡 如需进一步帮助，请告诉我。" % tool_data,
        "物流查询": "您的物流信息如下：\n\n%s\n\n💡 可复制单号到快递官网查询。" % tool_data,
        "退换货": "关于退换货：\n\n%s\n\n💡 已为您创建工单。" % tool_data,
        "投诉": "非常抱歉给您带来不愉快的体验。\n\n您的投诉已受理，24小时专人联系。\n\n💡 如需加急，请回复「转人工」。",
        "FAQ查询": "为您找到以下信息：\n\n%s\n\n💡 如果未解决，可转人工客服。" % tool_data,
        "售前咨询": "为您推荐以下商品：\n\n%s\n\n💡 告诉我您的预算或需求，帮您进一步筛选。" % tool_data,
        "优惠查询": "为您查询到：\n\n%s\n\n💡 结账时输入优惠码即可使用。" % tool_data,
        "地址修改": "%s\n\n💡 请提供订单号和新的收货地址。" % tool_data,
        "人工客服": "正在转接人工客服...\n排队人数: 2 人\n预计等待: 3-5 分钟\n📞 400-800-8888",
        "闲聊": "您好！有什么可以帮您？\n📦 查订单 🚚 查物流 🛒 推荐商品 💰 优惠 👤 转人工",
    }
    return templates.get(intent, templates["闲聊"])


# ═══════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════

@app.on_event("startup")
def startup():
    # Ensure static dir exists for fallback
    pass


@app.get("/health")
def health():
    return {"status": "ok", "service": "cs-demo", "version": "0.44.0"}


@app.get("/")
def root():
    return {"message": "灵枢智能客服 Demo", "docs": "/docs", "demo": "/cs-demo"}


@app.get("/cs-demo")
async def cs_demo_page():
    """Serve the CS Demo HTML page."""
    html_path = STATIC_DIR / "cs-demo.html"
    if html_path.exists():
        content = html_path.read_text(encoding="utf-8")
        return HTMLResponse(content)
    return HTMLResponse("<h1>CS Demo</h1><p>Page not found.</p>", status_code=404)


@app.post("/v1/cs/chat")
async def cs_chat(request: Request):
    """Non-streaming chat endpoint."""
    body = await request.json()
    message = (body.get("message", "") or "").strip()
    if not message:
        return JSONResponse({"error": "消息不能为空"}, status_code=400)

    intent = classify_intent(message)
    tool_results = execute_tools(intent, message)
    tool_summary = "\n".join(tool_results.values()) if tool_results else "无相关数据"

    reply = template_fallback(intent, tool_summary)

    return {
        "reply": reply,
        "intent": intent,
        "quick_replies": get_quick_replies(intent),
        "tool_results": tool_results,
    }


@app.post("/v1/cs/chat/stream")
async def cs_chat_stream(request: Request):
    """SSE streaming chat endpoint."""
    body = await request.json()
    message = (body.get("message", "") or "").strip()
    if not message:
        return JSONResponse({"error": "消息不能为空"}, status_code=400)

    intent = classify_intent(message)
    tool_results = execute_tools(intent, message)

    async def event_generator():
        # 1. Intent
        yield "data: %s\n\n" % json.dumps({"type": "intent", "intent": intent}, ensure_ascii=False)

        # 2. Tool results
        for name, result in tool_results.items():
            yield "data: %s\n\n" % json.dumps(
                {"type": "tool", "name": name, "result": result[:200]}, ensure_ascii=False)

        # 3. Generate reply (template only in standalone)
        tool_summary = "\n".join(tool_results.values()) if tool_results else "无相关数据"
        reply = template_fallback(intent, tool_summary)

        # Yield as tokens (simulate streaming)
        for token in reply:
            yield "data: %s\n\n" % json.dumps({"type": "token", "content": token}, ensure_ascii=False)

        # 4. Done
        yield "data: %s\n\n" % json.dumps({
            "type": "done",
            "quick_replies": get_quick_replies(intent),
        }, ensure_ascii=False)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# For direct uvicorn launch
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8788, log_level="info")
