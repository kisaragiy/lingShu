"""P2-4：无进展计数属于一次 run，并发 run 之间不得互相清计数。

旧实现把 guards 放模块全局、每次 run 开头 `_no_progress_guards = {}` ——
后启动的 run 会把先启动者的计数清空（空转被掩盖），先启动者的 guard 闭包
被丢弃后计数永久丢失。
"""
import pytest

import agent_harness.core.retry as retry
import agent_harness.core.graph.graph_multi as gm


def test_two_trackers_keep_independent_counts():
    """两个 run 各调一次不该凑成「连续两次」——共享计数时这里会提前熔断。"""
    a = gm.NoProgressTracker(True, max_repeats=2)
    b = gm.NoProgressTracker(True, max_repeats=2)

    a.check("search", "same task")      # a: 1
    b.check("search", "same task")      # b: 1（若共享计数 → 变 2 直接熔断）

    with pytest.raises(retry.NoProgressError):
        a.check("search", "same task")  # a: 2 → 熔断

    # a 熔断不影响 b 的计数：b 的第 2 次才熔断
    with pytest.raises(retry.NoProgressError):
        b.check("search", "same task")


def test_resetting_default_does_not_wipe_running_tracker():
    """`set_no_progress_detection` 只改默认 tracker，不能清掉已启动 run 的计数。"""
    running = gm.NoProgressTracker(True, max_repeats=2)
    running.check("search", "task")     # running: 1

    gm.set_no_progress_detection(True, 2)   # 另一个 run 启动时的配置动作

    with pytest.raises(retry.NoProgressError):
        running.check("search", "task")     # 计数仍在 → 第 2 次熔断


def test_disabled_tracker_never_trips():
    tracker = gm.NoProgressTracker(False, max_repeats=1)
    for _ in range(5):
        tracker.check("search", "same")     # 不抛


def test_per_worker_guards_are_independent():
    tracker = gm.NoProgressTracker(True, max_repeats=2)
    tracker.check("search", "t")
    tracker.check("write", "t")             # 换 worker 不该叠加 search 的计数
    with pytest.raises(retry.NoProgressError):
        tracker.check("search", "t")
