"""SLO 定义 + 检查（P2-4 ①）—— 分位、目标、越线判定，全离线可测。

判据（SLO 三问）：
  ① 分位**确定性**（线性插值，无随机；空样本 → None 不抛）；
  ② 越线**可判**（观测严格大于目标即 breach；观测缺失 → 跳过，不误报）；
  ③ 目标**落在真端点**上（DEFAULT_SLO_TARGETS 覆盖 locustfile 的端点）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core import slo  # noqa: E402


# ── ① 分位 ───────────────────────────────────────────────────

def test_percentile_endpoints_and_empty():
    assert slo.percentile([], 50) is None
    xs = [10, 20, 30, 40, 50]
    assert slo.percentile(xs, 0) == 10
    assert slo.percentile(xs, 100) == 50
    assert slo.percentile(xs, 50) == 30
    # 线性插值：p50 of [0,10] = 5
    assert slo.percentile([0, 10], 50) == 5


def test_percentile_unsorted_input():
    assert slo.percentile([50, 10, 30], 50) == 30


def test_latency_stats_keys_and_values():
    st = slo.latency_stats([100, 200, 300, 400, 500])
    assert st["n"] == 5 and st["p50"] == 300 and st["min"] == 100 and st["max"] == 500
    assert st["mean"] == 300


def test_latency_stats_empty():
    st = slo.latency_stats([])
    assert st["n"] == 0 and st["p50"] is None


# ── ② 越线判定 ───────────────────────────────────────────────

def test_check_percentiles_pass():
    t = slo.SLOTarget("x", p95_ms=1000, p99_ms=2000)
    r = slo.check_percentiles(t, p50=100, p95=800, p99=1500)
    assert r["passed"] is True and r["breaches"] == []


def test_check_percentiles_breach():
    t = slo.SLOTarget("x", p95_ms=1000, p99_ms=2000)
    r = slo.check_percentiles(t, p95=1200, p99=2500)
    assert r["passed"] is False
    metrics = {b["metric"] for b in r["breaches"]}
    assert metrics == {"p95", "p99"}


def test_check_percentiles_missing_observed_skipped():
    t = slo.SLOTarget("x", p50_ms=10, p95_ms=100)
    r = slo.check_percentiles(t, p95=50)   # p50 缺失
    assert r["passed"] is True


def test_check_percentiles_error_rate():
    t = slo.SLOTarget("x", error_rate_max=0.02)
    assert slo.check_percentiles(t, error_rate=0.01)["passed"] is True
    assert slo.check_percentiles(t, error_rate=0.05)["passed"] is False


def test_check_slo_from_samples_with_ttft():
    t = slo.SLOTarget("cs", p95_ms=1000, ttft_ms=500)
    r = slo.check_slo([100] * 17 + [5000] * 3, t, ttft_ms=[400, 450, 480])
    assert r["stats"]["n"] == 20
    assert r["passed"] is False                  # p95 被长尾顶爆
    assert any(b["metric"] == "p95" for b in r["breaches"])
    assert r["observed"]["ttft"] is not None


# ── ③ 默认目标 + 多端点汇总 ──────────────────────────────────

def test_default_targets_cover_locust_endpoints():
    for name in ("health", "metrics", "cs_chat", "cs_stream"):
        assert name in slo.DEFAULT_SLO_TARGETS
    assert slo.DEFAULT_SLO_TARGETS["cs_chat"].p99_ms == 20000


def test_evaluate_slo_report_all_pass():
    report = slo.evaluate_slo_report({
        "health": {"latencies_ms": [10, 12, 14]},
        "metrics": {"latencies_ms": [15, 20, 25]},
    })
    assert report["endpoints"] == 2 and report["all_pass"] is True


def test_evaluate_slo_report_breach():
    report = slo.evaluate_slo_report({
        "health": {"latencies_ms": [10, 12, 15, 20]},
        "cs_chat": {"latencies_ms": [1000, 9000, 30000], "error_rate": 0.5},
    })
    assert report["all_pass"] is False
    endpoints = {b["endpoint"] for b in report["breaches"]}
    assert "cs_chat" in endpoints


def test_evaluate_slo_report_unknown_endpoint_skipped():
    report = slo.evaluate_slo_report({"nope": {"latencies_ms": [1, 2, 3]}})
    assert report["endpoints"] == 0 and report["all_pass"] is False


def test_format_report_renders_marks():
    report = slo.evaluate_slo_report({"health": {"latencies_ms": [10, 12, 15]}})
    assert "health" in slo.format_report(report)
