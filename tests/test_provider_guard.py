"""P1-7：provider 级熔断（进程内）—— 半开路径必须真的可达。

背景缺陷：`CircuitBreaker` 是任务级进度熔断，一次 run 一个实例、tripped 即 finalize，
它保护不了任何下游 endpoint；`[HALF_OPEN 只在它自己的 check() 里出现]`，而生产链路上
一旦 tripped 就走到终点，半开永远走不到。本文件的 guard 是另一层：按 endpoint host
记健康度，冷却后放行**一次**探测。

验收要点（都是"回退实现就会红"的硬断言）：
  - 熔断后调用**不发请求**（用假 poster 的调用计数证明）
  - 半开只放行一个探测（并发时其余快速失败）
  - 探测成功 → 关闭；探测失败 → 重新打开且冷却翻倍
  - 4xx 不计入 provider 失败（参数错不该封死健康端点）
"""
from __future__ import annotations

import pytest

from agent_harness.core.pipeline import provider_guard as pg

URL = "http://127.0.0.1:1/v1/chat/completions"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Resp:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def guard(clock: FakeClock) -> pg.ProviderGuard:
    return pg.ProviderGuard(failure_threshold=3, cooldown=60.0, clock=clock)


# ─── 状态机 ────────────────────────────────────────────────────────────────

def test_success_keeps_closed(guard: pg.ProviderGuard) -> None:
    for _ in range(5):
        guard.record(URL, ok=True)
    assert guard.state_of(URL) == "closed"
    guard.before(URL)          # 不抛


def test_three_failures_open_the_circuit(guard: pg.ProviderGuard) -> None:
    for _ in range(3):
        guard.record(URL, ok=False)
    assert guard.state_of(URL) == "open"
    with pytest.raises(pg.ProviderUnavailable) as exc:
        guard.before(URL)
    assert exc.value.retry_after > 0


def test_recovery_within_cooldown_does_not_reopen(guard: pg.ProviderGuard, clock: FakeClock) -> None:
    for _ in range(3):
        guard.record(URL, ok=False)
    clock.advance(59)
    with pytest.raises(pg.ProviderUnavailable):
        guard.before(URL)
    assert guard.state_of(URL) == "open"


def test_half_open_allows_exactly_one_probe(guard: pg.ProviderGuard, clock: FakeClock) -> None:
    """★ 半开的核心：冷却结束后只放一个探测进来，其余快速失败（防雪崩）。"""
    for _ in range(3):
        guard.record(URL, ok=False)
    clock.advance(61)
    assert guard.state_of(URL) == "half_open"

    guard.before(URL)          # 这一条就是探测，放行
    with pytest.raises(pg.ProviderUnavailable):
        guard.before(URL)      # 第二个并发调用者被挡住


def test_probe_success_closes_circuit(guard: pg.ProviderGuard, clock: FakeClock) -> None:
    for _ in range(3):
        guard.record(URL, ok=False)
    clock.advance(61)
    guard.before(URL)
    guard.record(URL, ok=True)
    assert guard.state_of(URL) == "closed"
    guard.before(URL)


def test_probe_failure_reopens_with_doubled_cooldown(guard: pg.ProviderGuard, clock: FakeClock) -> None:
    for _ in range(3):
        guard.record(URL, ok=False)
    clock.advance(61)
    guard.before(URL)
    guard.record(URL, ok=False)          # 探测失败
    assert guard.state_of(URL) == "open"

    clock.advance(61)                    # 旧冷却已过，但新冷却翻倍到 120s
    assert guard.state_of(URL) == "open"
    clock.advance(60)
    assert guard.state_of(URL) == "half_open"


def test_hosts_are_isolated(guard: pg.ProviderGuard) -> None:
    for _ in range(3):
        guard.record(URL, ok=False)
    other = "http://127.0.0.1:2/v1/chat/completions"
    assert guard.state_of(other) == "closed"
    guard.before(other)


# ─── HTTP 语义 ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status,counts", [
    (500, True), (503, True), (429, True),
    (400, False), (401, False), (404, False), (200, False),
])
def test_status_classification(status: int, counts: bool) -> None:
    """5xx/429 算 provider 故障；4xx 是调用方的错，不该把端点封死。"""
    assert pg.is_provider_failure(status) is counts


# ─── guarded_post：熔断中真的不发请求 ──────────────────────────────────────

def test_guarded_post_does_not_send_while_open(monkeypatch, guard: pg.ProviderGuard) -> None:
    import requests

    calls = {"n": 0}

    def fake_post(url, **kw):
        calls["n"] += 1
        return _Resp(500)

    monkeypatch.setattr(requests, "post", fake_post)

    for _ in range(3):
        assert pg.guarded_post(URL, guard=guard, json={}).status_code == 500
    assert calls["n"] == 3

    # 第 4 次：熔断中 —— 必须**一个请求都不发**
    with pytest.raises(pg.ProviderUnavailable):
        pg.guarded_post(URL, guard=guard, json={})
    assert calls["n"] == 3, "熔断中仍然把请求打出去了 —— 熔断等于没做"


def test_guarded_post_records_network_error_and_reraises(monkeypatch, guard: pg.ProviderGuard) -> None:
    import requests

    def boom(url, **kw):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests, "post", boom)
    with pytest.raises(requests.ConnectionError):
        pg.guarded_post(URL, guard=guard, json={})
    # 异常不能被吞掉：吞掉会让上层以为"调用成功但内容为空"
    assert guard.snapshot()["127.0.0.1:1"]["failures"] == 1


def test_guarded_post_4xx_keeps_closed(monkeypatch, guard: pg.ProviderGuard) -> None:
    import requests

    monkeypatch.setattr(requests, "post", lambda url, **kw: _Resp(400))
    for _ in range(5):
        pg.guarded_post(URL, guard=guard, json={})
    assert guard.state_of(URL) == "closed"
