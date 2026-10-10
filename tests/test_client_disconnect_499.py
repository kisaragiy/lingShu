"""ClientDisconnect → 499 回归锁。

客户端在请求体读完前断开（关页面 / 客户端超时）是正常现象，默认会从
`await request.json()` 冒成未捕获异常 → 500，把错误率打脏。
本文件锁住「已注册 ClientDisconnect 异常处理器且返回 499」这一事实。

端到端行为验证（真实 socket 中途断开）见 `scripts/verify_client_disconnect_499.py`。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from starlette.requests import ClientDisconnect


def test_client_disconnect_handler_is_registered():
    """处理器必须注册在真实 app 上（不然异常照样冒成 500）。"""
    from agent_harness.main import app
    assert ClientDisconnect in app.exception_handlers, (
        "未注册 ClientDisconnect 处理器 —— 客户端断开会继续污染 5xx 错误率"
    )


def test_client_disconnect_maps_to_499_not_5xx():
    """返回码必须是 499（client closed request），不能落进 5xx 区间。"""
    import asyncio

    from agent_harness.main import app

    handler = app.exception_handlers[ClientDisconnect]
    resp = asyncio.run(handler(None, ClientDisconnect()))
    assert resp.status_code == 499, f"应为 499，实测 {resp.status_code}"
    assert not (500 <= resp.status_code < 600), "499 不得落在 5xx 区间（会污染错误率告警）"
