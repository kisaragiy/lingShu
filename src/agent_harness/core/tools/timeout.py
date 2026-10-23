"""Tool timeout policy — dsh P2 映射：超时做成可替换 seam，而非内嵌硬编码。

分离（对齐 dsh "tool-call-timeout-policy 是 execute 的 wrapper 插件"）:
- Registry 只负责工具定义 + 分发（Definition/Consumer）。
- 本模块拥有 **超时策略（seam）**: 每工具默认秒数 + 可替换的 lookup 钩子
  + daemon 线程强制执⾏。核心不硬编码任何具体超时值。

设计:
- get_timeout(tool): 解析某工具的允许秒数。0/None = 不超时(同步，零开销)。
- set_timeout_policy(fn): 替换 lookup 的 seam 钩子 —— 即"超时做成插件"的可替换点。
- call_with_timeout(func, kwargs, timeout): daemon 线程强制，超时抛 TimeoutError，
  线程 daemon 化 → 超时后不阻塞进程关闭。
"""

from __future__ import annotations

import queue
import threading

# 每工具默认允许秒数（0 = 不超时，走同步路径，零线程开销）。
# 网络/外部/长耗时工具设正数；本地即时小工具设 0。
DEFAULT_TIMEOUTS: dict[str, float] = {
    "search": 30.0,
    "fetch": 30.0,
    "web_scrape": 30.0,
    "web_browse": 60.0,
    "browser_automation": 60.0,
    "desktop_gui": 30.0,
    "app_launch": 20.0,
    "comfyui_text2img": 180.0,
    "comfyui_img2img": 180.0,
    "code_execute": 30.0,
    "rag_query": 20.0,
    "summarize": 30.0,
    "chat_send": 20.0,
    "qq_send": 20.0,
    # 本地即时小工具 → 0，避免无谓线程开销
    "think": 0.0,
    "datetime": 0.0,
    "file_read": 0.0,
    "file_write": 0.0,
}

DEFAULT_TIMEOUT = 60.0

# 可替换的 lookup 钩子（None 时用默认策略）
_timeout_lookup = None


def set_timeout_policy(policy) -> None:
    """替换超时 lookup 的 seam。传给一个可调用 `fn(tool) -> float`。

    这就是 dsh 的"超时做成插件"：核心不依赖任何固定值，
    只要换一个 policy 即可全局注入不同超时策略。
    """
    global _timeout_lookup
    _timeout_lookup = policy


def get_timeout(tool: str) -> float:
    """解析某工具允许的秒数。受设置的自定义 policy seam 影响。"""
    if _timeout_lookup is not None:
        try:
            return float(_timeout_lookup(tool))
        except Exception:
            return float(DEFAULT_TIMEOUT)
    return float(DEFAULT_TIMEOUTS.get(tool, DEFAULT_TIMEOUT))


def set_default_timeout(tool: str, seconds: float) -> None:
    """覆盖某工具的默认超时（写入策略字典）。0 = 不超时。"""
    DEFAULT_TIMEOUTS[tool] = float(seconds)


def call_with_timeout(func, kwargs: dict, timeout: float, side_effect: bool = False):
    """以超时执行 func(**kwargs)。

    - timeout<=0 → 直接同步执行（零线程开销）。
    - side_effect=True → **也同步执行**：daemon 线程超时只放弃「等待」，不会停止
      底层工作。对有副作用的工具（写盘/发消息/执行代码），超时后调用方以为失败
      会重试，而底层仍在跑 → 副作用重复。宁可等它跑完，也不制造重复副作用。
    - 否则用 daemon 线程强制：超时抛 TimeoutError，底层线程 daemon 化，
      超时后不会阻塞进程退出。

    语义边界（重要）：TimeoutError 只保证 **调用方不再等待**，不保证底层已停止。
    文案已写明，调用方不得据 TimeoutError 判定「没有发生」，重试前必须过幂等 key。
    """
    if side_effect:
        return func(**kwargs)

    if not timeout or timeout <= 0:
        return func(**kwargs)

    q: queue.Queue = queue.Queue(maxsize=1)

    def runner():
        try:
            q.put((True, func(**kwargs)))
        except Exception as e:  # noqa: BLE001 — 兜底透传给调用方
            q.put((False, e))

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    try:
        ok, val = q.get(timeout=timeout)
    except queue.Empty:
        raise TimeoutError(
            f"工具执行超时(>{timeout:g}s)：已放弃等待（底层可能仍在执行，勿盲目重试）"
        ) from None
    if ok:
        return val
    raise val
