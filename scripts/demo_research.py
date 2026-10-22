#!/usr/bin/env python3
"""一条命令跑出「可核对来源」的调研报告 —— 灵枢的证据链 demo。

为什么有这个脚本
----------------
README 首屏承诺的是「每条结论都带可回溯的来源（工具名 + 原始 URL + 抓取时间），
核不到的显式标注未找到强相关依据」。这句话必须能被一条命令当场验证，否则就是营销词。

本脚本就是那条命令：跑一次真实的多 Agent 调研 → 直接产出带「事实核验说明」三态块的
HTML 报告，把证据链（工具名 / URL / 抓取时间 / 核验结论）摊在报告里。

用法（仓库根，项目 venv）::

    ./.venv/Scripts/python scripts/demo_research.py "2025 年中国新能源汽车销量"
    ./.venv/Scripts/python scripts/demo_research.py "问题" --out demo.html
    ./.venv/Scripts/python scripts/demo_research.py "问题" --json   # 机器可读，便于断言

退出码（契约）
--------------
    0  成功，且有 provenance（证据链完整）
    2  成功，但零 provenance —— 报告没有可核对来源，属于「看起来能用但不可信」，告警
    1  失败（LLM 不可用 / 报告为空 / 异常）

设计取舍
--------
* 不内置假数据、不内置 fixture 冒充真实运行：跑不出就退出码 1 并说明原因。
* 不做「离线演示模式」——那会让 demo 与真实能力脱节，是自欺。
* 报告落盘到 ``outputs/``（gitignore），脚本本身可被 CI 断言（--json + 退出码）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_ALERT = 2


def _dedupe_sources(provenance: list[dict]) -> list[dict]:
    """provenance → 引用列表（按 URL 去重，保留首次出现的标题）。"""
    seen: set[str] = set()
    out: list[dict] = []
    for p in provenance:
        url = (p.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append({"url": url, "title": (p.get("title") or "")[:120]})
    return out


def _summary(result: dict) -> dict:
    """把 run_multi_agent 的结果压成一段可打印的证据摘要（不伪造任何字段）。"""
    provenance = result.get("provenance") or []
    factcheck = result.get("factcheck") or {}
    urls = {p.get("url") for p in provenance if p.get("url")}
    tools = sorted({p.get("tool") or "?" for p in provenance})
    not_found = factcheck.get("not_found") or factcheck.get("unverified") or []
    return {
        "provenance_count": len(provenance),
        "unique_urls": len(urls),
        "tools": tools,
        "factcheck_checked": bool(factcheck.get("checked")),
        "factcheck_skipped": bool(factcheck.get("skipped")),
        "factcheck_reason": factcheck.get("reason", ""),
        "not_found": len(not_found),
        "elapsed_s": result.get("elapsed_s"),
        "rounds": result.get("rounds"),
        "workers": list((result.get("worker_results") or {}).keys()),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="灵枢证据链 demo：一条命令产出带溯源的调研报告")
    ap.add_argument("request", help="调研问题（自然语言）")
    ap.add_argument("--out", default="", help="HTML 输出路径（默认 outputs/demo-report-<时间戳>.html）")
    ap.add_argument("--title", default="", help="报告标题（默认用问题本身）")
    ap.add_argument("--json", action="store_true", help="同时把结构化摘要打到 stdout（便于 CI 断言）")
    args = ap.parse_args(argv)

    from agent_harness.apps.research.pipeline.report_formatter import generate_report_html
    from agent_harness.core.graph.graph_multi import run_multi_agent

    t0 = time.time()
    try:
        result = run_multi_agent(args.request)
    except Exception as e:  # noqa: BLE001 —— CLI 边界：任何异常都要变成可读报错 + 退出码
        print(f"[demo] 运行失败：{type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_ERROR

    content = (result.get("final_output") or "").strip()
    if not content or content.startswith("[无法生成回复]"):
        print(
            "[demo] 报告为空或 LLM 不可用。请先确认模型已配置（本地 ollama 或云端 key），"
            "本次不生成任何文件。",
            file=sys.stderr,
        )
        return EXIT_ERROR

    provenance = result.get("provenance") or []
    factcheck = result.get("factcheck") or {}
    sources = _dedupe_sources(provenance)

    title = args.title or args.request[:60]
    html = generate_report_html(
        title, content, sources=sources, provenance=provenance or None, factcheck=factcheck or None,
    )

    out_path = Path(args.out) if args.out else (
        REPO / "outputs" / f"demo-report-{time.strftime('%Y%m%d-%H%M%S')}.html"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")

    info = _summary(result)
    print(f"[demo] 报告：{out_path}  ({len(html):,} 字符, 总耗时 {time.time() - t0:.1f}s)")
    print(f"[demo] 一手来源：{info['provenance_count']} 条 / 去重 URL {info['unique_urls']} 个"
          + (f" / 工具 {', '.join(info['tools'])}" if info["tools"] else ""))
    if info["factcheck_checked"]:
        print(f"[demo] 数字核验：已核对来源正文，未找到依据 {info['not_found']} 项")
    elif info["factcheck_skipped"]:
        print(f"[demo] 数字核验：未执行（{info['factcheck_reason'] or '无可用来源正文'}）")
    else:
        print("[demo] 数字核验：无结构化结果")

    if args.json:
        print(json.dumps({"out": str(out_path), "summary": info}, ensure_ascii=False))

    if not provenance:
        # 报告生成成功但没有一手来源 —— 可读，但不可核对。按契约告警而不是静默成功。
        print(
            "[demo] ⚠️ 零 provenance：这份报告没有任何可追溯来源，无法核对结论。"
            "按契约以退出码 2 告警。",
            file=sys.stderr,
        )
        return EXIT_ALERT
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
