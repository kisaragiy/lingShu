"""
CircuitBreaker 单元测试（9 用例）

覆盖：
  1. test_hard_breaker_never_recovers       —— 硬熔断（token 超支）后即使推进冷却时间也不恢复
  2. test_soft_breaker_triggers_by_fingerprint —— 软熔断：按 worker 分组指纹窗口触发
  3. test_cooldown_blocks_during_window    —— 冷却时间内 check 仍返回 tripped
  4. test_half_open_allows_probe           —— 冷却结束后 half-open 放行一次
  5. test_half_open_probe_passes_closes    —— 半开探测通过 → 恢复 closed
  6. test_half_open_probe_fails_reopens    —— 半开探测失败 → 重新 open（冷却重置）
  7. test_reset_clears_state               —— reset() 清空计数与状态
  8. test_concurrent_record_and_check      —— 多线程并发 record_output/check 不崩、状态一致
  9. test_time_fn_injection                —— time_fn 时钟注入（含墙钟超时硬熔断）

运行：
    pytest tests/test_circuit_breaker.py -v
"""

from __future__ import annotations

import threading
import time

from agent_harness.core.pipeline.circuit_breaker import (
    CircuitBreaker,
    COOLDOWN_SOFT_S,
    NO_PROGRESS_WINDOW,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. 硬熔断（token 超支）：触发后即使把 _open_since 推到很久以前，仍 tripped
# ─────────────────────────────────────────────────────────────────────────────
def test_hard_breaker_never_recovers():
    cb = CircuitBreaker(max_tokens=100)
    cb.add_tokens(200)
    st = cb.check()
    assert st["tripped"] is True
    assert cb._state == CircuitBreaker.OPEN
    assert cb._open_hard is True
    assert any("token" in r for r in st["reasons"])

    # 硬熔断：即使冷却时间早已"到期"，仍 tripped
    cb._open_since = time.time() - 9999
    st2 = cb.check()
    assert st2["tripped"] is True
    assert cb._open_hard is True


# ─────────────────────────────────────────────────────────────────────────────
# 2. 软熔断：按 worker 分组指纹，窗口内唯一指纹 <= 2 → 触发
# ─────────────────────────────────────────────────────────────────────────────
def test_soft_breaker_triggers_by_fingerprint():
    cb = CircuitBreaker()
    # 两个 worker 各说一句话来回抖动（wname 前缀不再干扰——wname 是分组 key）
    for _ in range(NO_PROGRESS_WINDOW + 2):
        cb.record_output("searcher", "找到了 3 条结果")
        cb.record_output("analyst", "在分析")
    st = cb.check()
    assert st["tripped"] is True
    assert cb._open_hard is False
    assert cb._state == CircuitBreaker.OPEN
    assert any("无实质变化" in r for r in st["reasons"])

    # 反例：不同 worker 不同输出 → 不触发
    cb2 = CircuitBreaker()
    for i in range(NO_PROGRESS_WINDOW + 2):
        cb2.record_output(f"worker_{i}", f"输出内容 {i}")
    assert cb2.check()["tripped"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 3. 冷却时间内：软熔断刚触发，_open_since 很近 → check 仍 tripped（冷却中）
# ─────────────────────────────────────────────────────────────────────────────
def test_cooldown_blocks_during_window():
    cb = CircuitBreaker()
    for _ in range(NO_PROGRESS_WINDOW + 2):
        cb.record_output("w1", "相同输出")
    assert cb.check()["tripped"] is True
    assert cb._open_hard is False

    # 刚 open 不到 60s → 仍 tripped
    st = cb.check()
    assert st["tripped"] is True
    assert any("冷却中" in r for r in st["reasons"])
    assert cb._state == CircuitBreaker.OPEN


# ─────────────────────────────────────────────────────────────────────────────
# 4. 冷却结束 → half-open 放行一次探测
# ─────────────────────────────────────────────────────────────────────────────
def test_half_open_allows_probe():
    cb = CircuitBreaker()
    for _ in range(NO_PROGRESS_WINDOW + 2):
        cb.record_output("w1", "相同输出")
    assert cb.check()["tripped"] is True

    # 手动把 _open_since 推到冷却结束（不 sleep 真实时间）
    cb._open_since = time.time() - (COOLDOWN_SOFT_S + 1)
    st = cb.check()
    assert st["tripped"] is False          # half-open 放行
    assert cb._state == CircuitBreaker.HALF_OPEN


# ─────────────────────────────────────────────────────────────────────────────
# 5. 半开探测通过（worker 输出相对快照真的变了）→ 恢复 closed
# ─────────────────────────────────────────────────────────────────────────────
def test_half_open_probe_passes_closes():
    cb = CircuitBreaker()
    for _ in range(NO_PROGRESS_WINDOW + 2):
        cb.record_output("w1", "相同输出")
    cb.check()                                       # → OPEN(soft)
    cb._open_since = time.time() - (COOLDOWN_SOFT_S + 1)
    cb.check()                                       # → HALF_OPEN

    # 探测通过：w1 产出全新内容
    cb.record_output("w1", "全新的输出内容，跟之前完全不一样")
    st = cb.check()
    assert st["tripped"] is False
    assert cb._state == CircuitBreaker.CLOSED


# ─────────────────────────────────────────────────────────────────────────────
# 6. 半开探测失败（worker 输出相对快照没变）→ 重新 open，冷却重置
# ─────────────────────────────────────────────────────────────────────────────
def test_half_open_probe_fails_reopens():
    cb = CircuitBreaker()
    for _ in range(NO_PROGRESS_WINDOW + 2):
        cb.record_output("w1", "相同输出")
    cb.check()                                       # → OPEN(soft)
    cb._open_since = time.time() - (COOLDOWN_SOFT_S + 1)
    cb.check()                                       # → HALF_OPEN

    # 探测失败：还是同样的输出
    cb.record_output("w1", "相同输出")
    st = cb.check()
    assert st["tripped"] is True
    assert cb._state == CircuitBreaker.OPEN
    assert cb._open_hard is False
    assert any("半开探测仍无进展" in r for r in st["reasons"])


# ─────────────────────────────────────────────────────────────────────────────
# 7. reset() 清空计数与状态
# ─────────────────────────────────────────────────────────────────────────────
def test_reset_clears_state():
    cb = CircuitBreaker()
    cb.add_tokens(999999)
    cb.record_output("w1", "任意输出")
    cb.check()                                       # 硬熔断 open
    assert cb._state == CircuitBreaker.OPEN

    cb.reset()
    assert cb._tripped is False
    assert cb.tokens_used == 0
    assert cb._state == CircuitBreaker.CLOSED
    assert cb._open_hard is False
    assert cb._worker_fingerprints == {}
    assert cb._recent_fps == []


# ─────────────────────────────────────────────────────────────────────────────
# 8. 并发安全：多线程并发 record_output / add_tokens / check 不崩、状态一致
# ─────────────────────────────────────────────────────────────────────────────
def test_concurrent_record_and_check():
    cb = CircuitBreaker(max_tokens=10_000_000)   # 放大阈值避免误触发

    def worker(wname: str, n: int):
        for i in range(n):
            cb.record_output(wname, f"{wname}-step-{i}")
            cb.add_tokens(1)
            cb.check()

    threads = [threading.Thread(target=worker, args=(f"w{i}", 200)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 不崩、tokens_used 计数正确（无锁则会丢更新）
    assert cb.tokens_used == 8 * 200
    # 每个 worker 都记到了指纹
    assert len(cb._worker_fingerprints) == 8
    # 因为每个 worker 的每步输出都不同，不应触发无进展
    assert cb.check()["tripped"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 9. 时钟注入 time_fn：可固定时钟，覆盖墙钟超时硬熔断
# ─────────────────────────────────────────────────────────────────────────────
def test_time_fn_injection():
    # 用一个可手动拨动的假时钟
    t = {"now": 1000.0}
    cb = CircuitBreaker(max_time_s=10, time_fn=lambda: t["now"])

    # 初始：刚启动
    assert cb.check()["tripped"] is False
    assert cb.start_time == 1000.0

    # 拨到 1005s：仍未超时
    t["now"] = 1005.0
    assert cb.check()["tripped"] is False

    # 拨到 1011s：超时硬熔断
    t["now"] = 1011.0
    st = cb.check()
    assert st["tripped"] is True
    assert cb._open_hard is True
    assert cb._state == CircuitBreaker.OPEN
    assert any("超时" in r for r in st["reasons"])

    # 硬熔断：再拨到很远也不恢复
    t["now"] = 9999.0
    assert cb.check()["tripped"] is True


# ─────────────────────────────────────────────────────────────────────────────
# 10. 窗口长度跟随 max_no_progress（防死参数回归）
# ─────────────────────────────────────────────────────────────────────────────
def test_window_follows_max_no_progress():
    cb = CircuitBreaker(max_no_progress=2)
    cb.record_output("w1", "A")
    cb.record_output("w1", "A")
    assert cb.check()["tripped"] is True        # 窗口 2 一填满即判无进展
