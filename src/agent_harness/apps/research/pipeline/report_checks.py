"""Conservative report diagnostics. These flags never certify a claim as true."""
from __future__ import annotations

import calendar
import re
from datetime import date


_QUARTER = re.compile(
    r"(20\d{2})\s*年?\s*(?:Q([1-4])|第?([一二三四1-4])季度)", re.IGNORECASE)
_FORECAST = re.compile(r"预计|预期|预测|将|恐|forecast|projected|expected", re.IGNORECASE)
_HISTORICAL = re.compile(r"当时|曾预计|曾预测|历史预测|原预测|当年的预测")


def review_warnings(content: str, as_of: date) -> list[dict[str, str]]:
    """Flag potentially stale forecasts and mixed market scopes for human review.

    An absence of flags is not a pass: implicit dates, paraphrases and entity/metric
    equivalence are deliberately left unverified.
    """
    warnings: list[dict[str, str]] = []
    seen: set[tuple[int, int]] = set()
    for sentence in re.split(r"[。！？\n]", content):
        if not _FORECAST.search(sentence) or _HISTORICAL.search(sentence):
            continue
        for match in _QUARTER.finditer(sentence):
            year = int(match.group(1))
            raw_q = match.group(2) or match.group(3)
            quarter = int(raw_q) if raw_q.isdigit() else "一二三四".index(raw_q) + 1
            month = quarter * 3
            end = date(year, month, calendar.monthrange(year, month)[1])
            if end < as_of and (year, quarter) not in seen:
                seen.add((year, quarter))
                warnings.append({
                    "code": "past_forecast_period",
                    "message": f"预测期 {year}Q{quarter} 已在报告日期 {as_of.isoformat()} 前结束；"
                               "请补充预测发布日期、历史预测标记及后续实绩，勿作为未来风险直接使用。",
                })
    scopes = [label for label, pattern in (
        ("模组", r"模组|内存条"), ("DIMM", r"DIMM"),
        ("DRAM颗粒", r"DRAM.{0,4}颗粒|DRAM.{0,4}原厂"), ("HBM", r"HBM"),
    ) if re.search(pattern, content, re.IGNORECASE)]
    if len(scopes) > 1 and re.search(r"%|％|美元|亿元|billion|million", content, re.IGNORECASE):
        warnings.append({"code": "mixed_market_scopes",
                         "message": "正文同时涉及" + "、".join(scopes)
                                    + "；请逐项确认市场范围、统计期、币种及份额分母，不能直接合并。"})
    return warnings
