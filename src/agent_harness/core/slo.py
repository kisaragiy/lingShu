"""SLO 定义 + 可测检查（P2-4 ①）—— 把「延迟目标」变成可断言的判据。

痛点
----
LLM 步骤的 P99 / P50 常达几十倍：一次长 prompt 就能把 P99 顶爆。压测（``locustfile.py``）
只报数字，没有「多少算好」的判据，也就没人会在 P99 越线时红。

落点（小而真、纯标准库、离线可测）
----------------------------------
* :class:`SLOTarget`：一个端点的 SLO（p50/p95/p99、可选 TTFT/ITL、error_rate 上限）。
* :data:`DEFAULT_SLO_TARGETS`：给 ``locustfile.py`` 里那几个端点定好的目标。
* :func:`percentile` / :func:`latency_stats`：确定性分位（线性插值，无随机）。
* :func:`check_percentiles`：拿**已算好的分位**判 SLO —— locust 的 ``StatsEntry`` 直接能喂。
* :func:`check_slo`：拿**原始延迟样本**判 SLO（先算分位）。
* :func:`evaluate_slo_report`：多端点汇总，一次给出所有越线项。

本模块**不 import locust**（locust 是压测期依赖，不能拖进单测）。locustfile 侧只做薄接线。
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass


# ── SLO 目标 ──────────────────────────────────────────────────

@dataclass(frozen=True)
class SLOTarget:
    """一个端点的延迟 SLO。未设的项不检查（``None`` = 不管）。单位毫秒。"""
    name: str
    p50_ms: float | None = None
    p95_ms: float | None = None
    p99_ms: float | None = None
    ttft_ms: float | None = None          # 首 token 时间
    itl_ms: float | None = None           # inter-token latency
    error_rate_max: float | None = None   # 0..1

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


# 给 locustfile 端点定的目标（可被 HARNESS_* 覆盖的只做默认；此处是判据基线）。
DEFAULT_SLO_TARGETS: dict[str, SLOTarget] = {
    "health":   SLOTarget("health", p50_ms=15, p95_ms=50, p99_ms=150, error_rate_max=0.0),
    "metrics":  SLOTarget("metrics", p50_ms=30, p95_ms=150, p99_ms=400, error_rate_max=0.0),
    "cs_chat":  SLOTarget("cs_chat", p50_ms=1500, p95_ms=8000, p99_ms=20000,
                          ttft_ms=1200, itl_ms=120, error_rate_max=0.02),
    "cs_stream": SLOTarget("cs_stream", p50_ms=800, p95_ms=4000, p99_ms=12000,
                           ttft_ms=800, error_rate_max=0.02),
}


# ── 分位计算（确定性）────────────────────────────────────────────

def percentile(samples: list[float], p: float) -> float | None:
    """线性插值分位（p ∈ [0, 100]）。空样本 → ``None``（不抛）。"""
    xs = sorted(float(x) for x in samples)
    if not xs:
        return None
    if p <= 0:
        return xs[0]
    if p >= 100:
        return xs[-1]
    k = (len(xs) - 1) * (p / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return xs[lo]
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def latency_stats(samples: list[float]) -> dict:
    """一次给出 n / p50 / p95 / p99 / min / max / mean（全确定性，四舍五入 2 位）。"""
    xs = [float(x) for x in samples]
    if not xs:
        return {"n": 0, "p50": None, "p95": None, "p99": None,
                "min": None, "max": None, "mean": None}

    def r(v: float | None) -> float | None:
        return None if v is None else round(v, 2)

    return {
        "n": len(xs),
        "p50": r(percentile(xs, 50)),
        "p95": r(percentile(xs, 95)),
        "p99": r(percentile(xs, 99)),
        "min": r(min(xs)),
        "max": r(max(xs)),
        "mean": r(sum(xs) / len(xs)),
    }


# ── SLO 检查 ──────────────────────────────────────────────────

def check_percentiles(
    target: SLOTarget,
    *,
    p50: float | None = None,
    p95: float | None = None,
    p99: float | None = None,
    ttft: float | None = None,
    itl: float | None = None,
    error_rate: float | None = None,
) -> dict:
    """拿**已算好的分位**判 SLO。返回 ``{name, passed, breaches, observed, target}``。

    判据：观测值 **严格大于** 目标即越线；观测缺失（``None``）跳过 —— 缺数据不误报。
    """
    observed = {"p50": p50, "p95": p95, "p99": p99,
                "ttft": ttft, "itl": itl, "error_rate": error_rate}
    limits = {"p50": target.p50_ms, "p95": target.p95_ms, "p99": target.p99_ms,
              "ttft": target.ttft_ms, "itl": target.itl_ms,
              "error_rate": target.error_rate_max}

    breaches: list[dict] = []
    for key, limit in limits.items():
        val = observed.get(key)
        if val is None or limit is None:
            continue
        if val > limit:
            breaches.append({"metric": key, "observed": round(val, 2), "limit": limit})

    return {
        "name": target.name,
        "passed": not breaches,
        "breaches": breaches,
        "observed": {k: (None if v is None else round(v, 2)) for k, v in observed.items()},
        "target": target.to_dict(),
    }


def check_slo(
    samples_ms: list[float],
    target: SLOTarget,
    *,
    ttft_ms: list[float] | None = None,
    itl_ms: list[float] | None = None,
    error_rate: float | None = None,
) -> dict:
    """拿**原始延迟样本**判 SLO（先算分位再判）。"""
    stats = latency_stats(samples_ms)
    ttft = None
    if ttft_ms:
        ttft = percentile([float(x) for x in ttft_ms], 95)   # TTFT 取 p95
    itl = None
    if itl_ms:
        itl = percentile([float(x) for x in itl_ms], 95)
    res = check_percentiles(target, p50=stats["p50"], p95=stats["p95"],
                            p99=stats["p99"], ttft=ttft, itl=itl,
                            error_rate=error_rate)
    res["stats"] = stats
    return res


def evaluate_slo_report(
    endpoints: dict[str, dict],
    targets: dict[str, SLOTarget] | None = None,
) -> dict:
    """多端点汇总。``endpoints`` = ``{name: {"latencies_ms": [...], "ttft_ms": [...],
    "itl_ms": [...], "error_rate": float}}``，缺项的端点跳过。"""
    targets = targets or DEFAULT_SLO_TARGETS
    results: list[dict] = []
    for name, data in endpoints.items():
        target = targets.get(name)
        if target is None:
            continue
        results.append(check_slo(
            data.get("latencies_ms", []), target,
            ttft_ms=data.get("ttft_ms"), itl_ms=data.get("itl_ms"),
            error_rate=data.get("error_rate"),
        ))
    breaches = [{"endpoint": r["name"], **b} for r in results for b in r["breaches"]]
    return {
        "endpoints": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "all_pass": bool(results) and not breaches,
        "breaches": breaches,
        "results": results,
    }


def format_report(report: dict) -> str:
    lines = [f"── SLO 检查：{report.get('passed')}/{report.get('endpoints')} 端点达标 ──"]
    for r in report.get("results", []):
        mark = "✅" if r["passed"] else "❌"
        lines.append(f"  {mark} {r['name']}  p50={r['observed'].get('p50')} "
                     f"p95={r['observed'].get('p95')} p99={r['observed'].get('p99')}")
        for b in r["breaches"]:
            lines.append(f"       ⚠ {b['metric']} {b['observed']} > {b['limit']}")
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SLO 检查（P2-4）")
    ap.add_argument("--samples", help="延迟样本 JSON：{endpoint: {latencies_ms:[...]}}")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not args.samples:
        ap.print_help()
        return 0
    data = json.loads(open(args.samples, encoding="utf-8").read())
    report = evaluate_slo_report(data)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_report(report))
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
