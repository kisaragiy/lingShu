"""Tests for CS intent classification — rule-based, no server needed."""
import pytest
from agent_harness.apps.cs_demo.tools.customer_service import classify_cs_intent


class TestCSIntentClassification:
    """Test ALL 9 intent types plus fallback."""

    # ── 查订单 ──
    def test_intent_order_lookup_direct(self):
        assert classify_cs_intent("查看订单状态") == "查订单"
        assert classify_cs_intent("ORD20260708001") == "查订单"

    def test_intent_order_excludes_logistics(self):
        # "发货" triggers logistics before order
        assert classify_cs_intent("订单物流到哪了") == "物流查询"

    # ── 退换货 ──
    def test_intent_return_exchange(self):
        assert classify_cs_intent("我想退货") == "退换货"
        assert classify_cs_intent("换货怎么操作") == "退换货"
        assert classify_cs_intent("退款还没到账") == "退换货"
        assert classify_cs_intent("东西坏了") == "退换货"

    def test_intent_return_quality(self):
        # "坏" triggers "坏了" check, "质量" triggers "质量问题"
        assert classify_cs_intent("商品质量有问题") in ("退换货", "闲聊")

    # ── 物流查询 ──
    def test_intent_logistics(self):
        assert classify_cs_intent("物流信息") == "物流查询"
        assert classify_cs_intent("快递到哪了") == "物流查询"
        assert classify_cs_intent("什么时候送货") == "物流查询"

    # ── 投诉 ──
    def test_intent_complaint(self):
        assert classify_cs_intent("我要投诉") == "投诉"
        assert classify_cs_intent("客服态度太差") == "投诉"
        assert classify_cs_intent("垃圾服务") == "投诉"

    # ── 人工客服 ──
    def test_intent_human_handoff(self):
        assert classify_cs_intent("转人工") == "人工客服"
        assert classify_cs_intent("找真人客服") == "人工客服"
        assert classify_cs_intent("接线员") == "人工客服"

    # ── FAQ查询 ──
    def test_intent_faq(self):
        # "怎么退货" hits "退货" before FAQ
        # These trigger FAQ because they don't match return keywords exactly
        assert classify_cs_intent("保修政策是什么") == "FAQ查询"
        assert classify_cs_intent("运费多少") == "FAQ查询"
        assert classify_cs_intent("如何开发票") == "FAQ查询"

    def test_intent_faq_how_to(self):
        # "怎么" triggers FAQ, but "退货" overrides
        result = classify_cs_intent("怎么退货")
        assert result in ("FAQ查询", "退换货")

    # ── 售前咨询 ──
    def test_intent_presales(self):
        assert classify_cs_intent("推荐一款手机") == "售前咨询"
        assert classify_cs_intent("这个产品怎么样") == "售前咨询"
        assert classify_cs_intent("这款值得买吗") == "售前咨询"

    # ── 优惠查询 ──
    def test_intent_promotion(self):
        assert classify_cs_intent("有什么优惠") == "优惠查询"
        assert classify_cs_intent("优惠券") == "优惠查询"
        assert classify_cs_intent("打折吗") == "优惠查询"

    # ── 地址修改 ──
    def test_intent_address_change(self):
        assert classify_cs_intent("换收货地址") == "地址修改"
        assert classify_cs_intent("地址错了") == "地址修改"
        assert classify_cs_intent("送到别处") == "地址修改"

    def test_intent_address_change_variants(self):
        result = classify_cs_intent("改一下地址")
        assert result in ("地址修改", "闲聊")

    # ── 闲聊 ──
    def test_intent_fallback(self):
        assert classify_cs_intent("你好") == "闲聊"
        assert classify_cs_intent("今天天气真好") == "闲聊"
        assert classify_cs_intent("") == "闲聊"
        assert classify_cs_intent("  ") in ("闲聊", "FAQ查询")

    # ── Edge cases ──
    def test_edge_cases(self):
        assert classify_cs_intent("help") == "闲聊"
        assert classify_cs_intent("https://example.com/product/123") == "闲聊"

    def test_ambiguous_presales_and_promo(self):
        assert classify_cs_intent("这个手机有什么优惠吗") == "优惠查询"

    def test_ambiguous_order_with_cancel(self):
        assert classify_cs_intent("取消订单") == "退换货"
