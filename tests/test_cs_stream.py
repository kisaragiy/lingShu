"""CS Demo SSE 流式回复测试。

用法:
  # 先启动服务器
  python run_cs_demo.py --no-open

  # 再运行测试
  python -m pytest tests/test_cs_stream.py -v -s

  # 或手动测试
  python tests/test_cs_stream.py
"""

import json
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests

BASE = "http://127.0.0.1:8788"


def test_health():
    """验证服务器运行中。"""
    r = requests.get(f"{BASE}/health", timeout=5)
    assert r.status_code == 200
    print("✓ 服务器健康检查通过")


def test_cs_stream_sse_structure():
    """验证 SSE 事件结构和顺序。"""
    r = requests.post(
        f"{BASE}/v1/cs/chat/stream",
        json={"message": "查一下我的订单"},
        stream=True,
        timeout=15,
    )
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("text/event-stream")

    events = []
    buffer = ""
    for chunk in r.iter_content(chunk_size=1, decode_unicode=True):
        if chunk:
            buffer += chunk
            if buffer.endswith("\n\n"):
                for line in buffer.strip().split("\n"):
                    if line.startswith("data: "):
                        events.append(json.loads(line[6:]))
                buffer = ""
        if len(events) >= 10:
            break

    assert len(events) >= 3, f"至少有 3 个事件 (intent + tool + token), 实际: {len(events)}"

    # 事件顺序验证
    assert events[0]["type"] == "intent", f"第一个事件应为 intent, 实际: {events[0]['type']}"
    assert "intent" in events[0], f"intent 事件应包含 intent 字段"
    print(f"✓ 意图: {events[0]['intent']}")

    # 工具事件
    tool_events = [e for e in events if e["type"] == "tool"]
    if tool_events:
        for te in tool_events:
            assert "name" in te
            assert "result" in te
        print(f"✓ 工具事件: {', '.join(e['name'] for e in tool_events)}")

    # token / done 事件
    token_or_done = [e for e in events if e["type"] in ("token", "done")]
    if token_or_done:
        print(f"✓ 收到 {sum(1 for e in events if e['type']=='token')} 个 token")
        if any(e["type"] == "done" for e in events):
            done_event = [e for e in events if e["type"] == "done"][0]
            assert "quick_replies" in done_event
            print(f"✓ 完成, 快捷回复: {done_event['quick_replies']}")

    print("✓ SSE 流结构验证通过")


def test_cs_stream_multiple_questions():
    """验证多轮对话的上下文保持。"""
    s = requests.Session()
    session_id = "test_sse_" + os.urandom(4).hex()

    # 第一轮
    r = s.post(
        f"{BASE}/v1/cs/chat/stream",
        json={"message": "查一下我的订单", "session_id": session_id},
        stream=True,
        timeout=15,
    )
    full = collect_sse(r)
    assert any(e["type"] == "done" for e in full), "第一轮应收到 done 事件"
    first_tokens = "".join(e.get("content", "") for e in full if e["type"] == "token")
    assert len(first_tokens) > 20, f"第一轮回复应有内容, 长度: {len(first_tokens)}"
    print(f"✓ 第一轮回复: {first_tokens[:60]}...")

    # 第二轮
    r = s.post(
        f"{BASE}/v1/cs/chat/stream",
        json={"message": "帮我查一下物流", "session_id": session_id},
        stream=True,
        timeout=15,
    )
    full = collect_sse(r)
    assert any(e["type"] == "done" for e in full), "第二轮应收到 done 事件"
    second_tokens = "".join(e.get("content", "") for e in full if e["type"] == "token")
    assert len(second_tokens) > 20, f"第二轮回复应有内容"
    print(f"✓ 第二轮回复: {second_tokens[:60]}...")

    print("✓ 多轮对话测试通过")


def test_intent_presales():
    """验证售前咨询意图识别与商品推荐。"""
    r = requests.post(
        f"{BASE}/v1/cs/chat/stream",
        json={"message": "推荐一款降噪耳机"},
        stream=True,
        timeout=15,
    )
    full = collect_sse(r)
    assert any(e["type"] == "done" for e in full)
    events_by_type = {e["type"]: e for e in full}
    assert events_by_type.get("intent", {}).get("intent") == "售前咨询", \
        f"预期售前咨询, 实际: {events_by_type.get('intent', {})}"
    # Should have tool events
    tools = [e for e in full if e["type"] == "tool"]
    assert any("商品" in e.get("name", "") for e in tools), "应有商品查询工具事件"
    print(f"✓ 售前咨询意图正确, 工具: {[e['name'] for e in tools]}")


def test_intent_coupon():
    """验证优惠查询意图识别。"""
    r = requests.post(
        f"{BASE}/v1/cs/chat/stream",
        json={"message": "有什么优惠活动"},
        stream=True,
        timeout=15,
    )
    full = collect_sse(r)
    assert any(e["type"] == "done" for e in full)
    events_by_type = {e["type"]: e for e in full}
    assert events_by_type.get("intent", {}).get("intent") == "优惠查询", \
        f"预期优惠查询, 实际: {events_by_type.get('intent', {})}"
    tools = [e for e in full if e["type"] == "tool"]
    assert any("优惠" in e.get("name", "") for e in tools), "应有优惠信息工具事件"
    print(f"✓ 优惠查询意图正确, 工具: {[e['name'] for e in tools]}")


def test_intent_address():
    """验证地址修改意图识别。"""
    r = requests.post(
        f"{BASE}/v1/cs/chat/stream",
        json={"message": "我想改收货地址"},
        stream=True,
        timeout=15,
    )
    full = collect_sse(r)
    assert any(e["type"] == "done" for e in full)
    events_by_type = {e["type"]: e for e in full}
    assert events_by_type.get("intent", {}).get("intent") == "地址修改", \
        f"预期地址修改, 实际: {events_by_type.get('intent', {})}"
    print(f"✓ 地址修改意图正确")


def collect_sse(r):
    """Collect all SSE events from a streaming response."""
    events = []
    buffer = ""
    for chunk in r.iter_content(chunk_size=1, decode_unicode=True):
        if chunk:
            buffer += chunk
            if buffer.endswith("\n\n"):
                for line in buffer.strip().split("\n"):
                    if line.startswith("data: "):
                        try:
                            events.append(json.loads(line[6:]))
                        except json.JSONDecodeError:
                            pass
                buffer = ""
    return events


if __name__ == "__main__":
    # 手动测试
    print("=" * 50)
    print("CS Demo SSE 流式回复测试")
    print("请确保服务器运行在", BASE)
    print("=" * 50)

    try:
        test_health()
        test_cs_stream_sse_structure()
        test_cs_stream_multiple_questions()
        test_intent_presales()
        test_intent_coupon()
        test_intent_address()
        print("\n✅ 全部测试通过!")
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
