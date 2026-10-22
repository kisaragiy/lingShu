"""ENG-39 — 自研 cron 调度的可靠性回归。

覆盖四条验收（清单 L8556 / L3218-3222）：
  ① 30 * * * * 触发后 next_run 推进到下一个 :30，xx:31 不再触发；
  ② 0 9 * * 1-5 周末不触发、工作日触发（POSIX 口径：周日=0/7、周一=1）；
  ③ tick 抛异常会被 logger.error 记录，调度器不停止；
  ④ 重启后 next_run 已过 → 恰好补跑一次，不连环补。

另加：列表语法、坏字段不抛异常、update_task 能写 next_run、新任务不立即触发。
"""

import time

import pytest

from agent_harness.core import agent_cron


@pytest.fixture
def sched(tmp_path, monkeypatch):
    """Isolated task dir + no real agent execution."""
    monkeypatch.setattr(agent_cron, "SCHEDULER_DIR", tmp_path)
    monkeypatch.setattr(agent_cron, "TASKS_DIR", tmp_path / "tasks")
    executed = []
    monkeypatch.setattr(
        agent_cron.AgentScheduler, "_execute",
        lambda self, task_id, prompt: executed.append(task_id),
    )
    scheduler = agent_cron.AgentScheduler()
    scheduler.executed = executed
    return scheduler


def _ts(y, mo, d, h, mi):
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


# ─── 表达式解析 ───

def test_range_syntax_weekdays():
    """0 9 * * 1-5：周一命中、周六不命中（pattern 用 POSIX 口径，周日=0/周一=1）。"""
    parsed = agent_cron._parse_cron("0 9 * * 1-5")
    monday = time.localtime(_ts(2026, 10, 26, 9, 0))   # tm_wday=0 → POSIX 1
    saturday = time.localtime(_ts(2026, 10, 24, 9, 0))  # tm_wday=5 → POSIX 6
    assert monday.tm_wday == 0
    assert saturday.tm_wday == 5
    assert agent_cron._cron_matches(parsed, monday) is True
    assert agent_cron._cron_matches(parsed, saturday) is False


def test_sunday_accepts_zero_and_seven():
    """POSIX 里 0 和 7 都是周日。"""
    sunday = time.localtime(_ts(2026, 10, 25, 9, 0))
    assert sunday.tm_wday == 6
    for expr in ("0 9 * * 0", "0 9 * * 7"):
        assert agent_cron._cron_matches(agent_cron._parse_cron(expr), sunday) is True
    # 周日不在工作日内
    assert agent_cron._cron_matches(agent_cron._parse_cron("0 9 * * 1-5"), sunday) is False


def test_list_syntax():
    parsed = agent_cron._parse_cron("0 9 * * 1,3,5")
    assert agent_cron._cron_matches(parsed, time.localtime(_ts(2026, 10, 26, 9, 0))) is True   # 周一
    assert agent_cron._cron_matches(parsed, time.localtime(_ts(2026, 10, 27, 9, 0))) is False  # 周二


def test_step_syntax_still_works():
    parsed = agent_cron._parse_cron("*/15 * * * *")
    assert agent_cron._cron_matches(parsed, time.localtime(_ts(2026, 10, 22, 10, 45))) is True
    assert agent_cron._cron_matches(parsed, time.localtime(_ts(2026, 10, 22, 10, 46))) is False


def test_malformed_field_does_not_raise():
    """坏字段只让该字段不命中，不能把 tick 炸掉（老代码 int('1-5') 抛 ValueError）。"""
    parsed = agent_cron._parse_cron("0 9 * * 1-5")  # 老代码在这里会 ValueError
    assert agent_cron._parse_field("abc", 9) is False
    assert agent_cron._parse_field("*/x", 9) is False
    assert agent_cron._parse_field("1-", 9) is False
    assert agent_cron._cron_matches(parsed, time.localtime(_ts(2026, 10, 26, 9, 0))) is True


# ─── 下次触发时间 ───

def test_next_run_after_hourly_slot():
    """验收①：10:31 时，30 * * * * 的下一次是 11:30（不是 10:31）。"""
    parsed = agent_cron._parse_cron("30 * * * *")
    nxt = agent_cron._next_run_after(parsed, _ts(2026, 10, 22, 10, 31))
    lt = time.localtime(nxt)
    assert (lt.tm_hour, lt.tm_min) == (11, 30)


def test_next_run_after_interval():
    parsed = agent_cron._parse_cron("every 30m")
    assert agent_cron._next_run_after(parsed, 1_000_000) == 1_001_800


def test_weekday_task_skips_weekend_in_next_run():
    """0 9 * * 1-5 从周五 10:00 之后算，应落到下周一 09:00。"""
    parsed = agent_cron._parse_cron("0 9 * * 1-5")
    friday_10am = _ts(2026, 10, 23, 10, 0)
    nxt = time.localtime(agent_cron._next_run_after(parsed, friday_10am))
    assert nxt.tm_wday == 0          # 周一
    assert (nxt.tm_hour, nxt.tm_min) == (9, 0)


# ─── tick 行为 ───

def test_add_task_schedules_future_and_does_not_fire(sched):
    task = agent_cron.add_task("t1", "30 * * * *", "简报")
    assert task["next_run"] > time.time()
    sched._tick()
    assert sched.executed == []


def test_tick_does_not_fire_before_next_run(sched):
    agent_cron.add_task("t1", "30 * * * *", "简报")
    agent_cron.update_task("t1", next_run=time.time() + 600)
    sched._tick()
    assert sched.executed == []


def test_tick_fires_once_and_advances_next_run(sched):
    agent_cron.add_task("t1", "30 * * * *", "简报")
    agent_cron.update_task("t1", next_run=time.time() - 1)

    sched._tick()
    assert sched.executed == ["t1"]

    task = agent_cron.get_task("t1")
    assert task["next_run"] > time.time()   # 已推进
    assert task["run_count"] == 1
    assert task["last_run"] is not None

    # 下一分钟再扫：不再触发（老代码在这里会重复跑）
    sched._tick()
    assert sched.executed == ["t1"]
    assert agent_cron.get_task("t1")["run_count"] == 1


def test_no_cross_minute_double_fire(sched):
    """验收①回归：老实现靠 tm_min 比较，30 * * * * 在 xx:31 会再触发一次。"""
    agent_cron.add_task("t1", "30 * * * *", "简报")
    agent_cron.update_task("t1", next_run=time.time() - 1)
    sched._tick()
    first = agent_cron.get_task("t1")
    # 模拟后续两轮 tick
    sched._tick()
    sched._tick()
    assert sched.executed == ["t1"], "同一计划槽只允许触发一次"
    assert agent_cron.get_task("t1")["next_run"] == first["next_run"]


def test_catch_up_runs_exactly_once(sched, caplog):
    """验收④：停机三天后 next_run 早已过 → 只补跑一次，不连环补。"""
    agent_cron.add_task("daily", "0 9 * * *", "简报")
    three_days_ago = time.time() - 3 * 86400
    agent_cron.update_task("daily", next_run=three_days_ago)

    with caplog.at_level("WARNING", logger="agent_harness.core.agent_cron"):
        sched._tick()
    assert sched.executed == ["daily"]
    assert any("补跑" in r.message for r in caplog.records)

    sched._tick()
    sched._tick()
    assert sched.executed == ["daily"], "补跑只允许一次"


def test_legacy_task_without_next_run_is_scheduled_not_fired(sched):
    """旧任务文件（next_run=None）→ 排程，不追溯触发。"""
    import json as _json
    agent_cron.add_task("old", "0 9 * * *", "简报")
    task = agent_cron.get_task("old")
    task["next_run"] = None
    agent_cron.TASKS_DIR.joinpath("old.json").write_text(
        _json.dumps(task, ensure_ascii=False), encoding="utf-8")

    sched._tick()
    assert sched.executed == []
    assert agent_cron.get_task("old")["next_run"] is not None


def test_update_task_writes_next_run(sched):
    """回归：老 update_task 白名单只放行 schedule/prompt/enabled。"""
    agent_cron.add_task("t1", "0 9 * * *", "简报")
    agent_cron.update_task("t1", next_run=12345, last_run=99, run_count=7)
    task = agent_cron.get_task("t1")
    assert (task["next_run"], task["last_run"], task["run_count"]) == (12345, 99, 7)


def test_update_schedule_recomputes_next_run(sched):
    agent_cron.add_task("t1", "0 9 * * *", "简报")
    before = agent_cron.get_task("t1")["next_run"]
    agent_cron.update_task("t1", schedule="every 5m")
    after = agent_cron.get_task("t1")["next_run"]
    assert after != before
    assert after <= time.time() + 5 * 60 + 1


# ─── 异常不静默 ───

def test_loop_logs_tick_failure_and_keeps_running(sched, monkeypatch, caplog):
    """验收③：tick 抛异常 → logger.error，且下一轮仍会 tick（不静默停摆）。"""
    calls = []

    def boom():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("tick 炸了")
        sched._stop_event.set()

    monkeypatch.setattr(sched, "_tick", boom)
    monkeypatch.setattr(agent_cron, "_POLL_INTERVAL", 0)

    with caplog.at_level("ERROR", logger="agent_harness.core.agent_cron"):
        sched._loop()

    assert len(calls) == 2, "第一次抛异常后循环必须继续"
    assert any("tick failed" in r.message for r in caplog.records)
    assert any("tick 炸了" in str(r.args) or "tick 炸了" in str(r.message)
               for r in caplog.records)
