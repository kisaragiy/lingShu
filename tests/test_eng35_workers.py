"""ENG-35 — 多 worker 守卫。

README 早写明 HARNESS_WORKERS 保持 1，但 main() 曾把它原样传给 uvicorn：
会话存储/指标/取消注册表/LLM 缓存都是进程内状态，且每个 worker 各起一个
cron 调度线程 → cron 任务按 worker 数成倍触发。守卫必须落在代码里，不能只写在文档里。
"""

import pytest


@pytest.fixture(scope="module")
def resolve():
    from agent_harness.main import _resolve_workers
    return _resolve_workers


def test_multi_worker_is_clamped_to_one(resolve):
    assert resolve("4") == 1
    assert resolve("16") == 1


def test_single_worker_passes_through(resolve):
    assert resolve("1") == 1
    assert resolve(None) == 1
    assert resolve("") == 1


def test_invalid_or_zero_falls_back_to_one(resolve):
    assert resolve("abc") == 1
    assert resolve("0") == 1
    assert resolve("-3") == 1


def test_clamp_is_announced(resolve, capsys):
    """降级不能静默——用户设了 4 却只跑 1 个进程，必须看到原因。"""
    resolve("4")
    out = capsys.readouterr().out
    assert "HARNESS_WORKERS=4" in out
    assert "降为 1" in out
