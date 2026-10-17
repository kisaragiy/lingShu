#!/usr/bin/env python3
"""core 模块评测门禁（LWP18 接线）—— 把 ``module_eval`` 探针跑成可执行基线。

``src/agent_harness/core/module_eval.py`` 提供「换实现能不能区分好坏」的探针能力，
但原先没有任何入口调用它（装了没通电）。本脚本是它的**执行入口**：

  1. ``evaluate_all(LingShuModules())`` —— 对灵枢真实四模块（retry / degradation /
     budget / circuit_breaker）跑全部行为探针；
  2. 打印每个模块的 score 与**失败探针名**（可读、可 diff）；
  3. 把全量结果（``report.to_dict()``）落到 ``eval_results/modules-<YYYYMMDD>.json``；
  4. 按返回值契约退出，供 CI / pre-push 直接判定。

返回值契约（项目硬约定 D33）
---------------------------
================  ====================================================
退出码            含义
================  ====================================================
0                 全模块 score == 1.0（OK，静默）
2                 有模块 score < 1.0（ALERT —— **发现问题**）
1                 执行异常（ERROR —— 脚本自己坏了，不是被测代码坏了）
================  ====================================================

**绝不用 1 表示「发现问题」**：1 专指脚本执行失败，把「被测对象有问题」和
「评测器有问题」混成同一个码，等于让门禁自己瞎。异常一律 traceback 到 stderr。

用法（repo 根）::

    python scripts/eval_modules.py

    # CI 里：退出码 0 放行，2 报警（非 0 但也不是崩溃），1 查脚本自身
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# 允许直接 `python scripts/eval_modules.py` 运行（不必安装包 / 不必设 PYTHONPATH）
sys.path.insert(0, str(REPO / "src"))

from agent_harness.core.module_eval import (  # noqa: E402
    EvalReport,
    LingShuModules,
    evaluate_all,
)

OUT_DIR = REPO / "eval_results"

# ── 返回值契约（D33）─────────────────────────────────────────────
EXIT_OK = 0      # 全模块 score == 1.0
EXIT_ERROR = 1   # 执行异常（脚本自身失败）
EXIT_ALERT = 2   # 有模块 score < 1.0（发现质量问题）


def exit_code_for(report: EvalReport) -> int:
    """报告 → 退出码。纯函数，便于单测覆盖 OK / ALERT 两条路径。

    判据用「每模块 score 是否都 == 1.0」而不是 ``report.passed`` —— 后者是
    ``all(probes passed)``，语义等价但这里把 ``score`` 这一硬指标写在契约里，
    与验收标准（全模块 score==1.0）逐字对应。
    """
    if report.modules and all(m.score == 1.0 for m in report.modules):
        return EXIT_OK
    return EXIT_ALERT


def result_path(now: datetime | None = None) -> Path:
    """当日结果文件路径：``eval_results/modules-YYYYMMDD.json``。"""
    day = (now or datetime.now()).strftime("%Y%m%d")
    return OUT_DIR / f"modules-{day}.json"


def build_payload(report: EvalReport, now: datetime | None = None) -> dict:
    """落盘内容：``report.to_dict()`` 全量 + 少量元信息（时间 / 被测 bundle）。"""
    ts = (now or datetime.now()).astimezone()
    payload = {"generated_at": ts.isoformat(timespec="seconds"), "bundle": "LingShuModules"}
    payload.update(report.to_dict())
    return payload


def write_payload(payload: dict, path: Path) -> None:
    """原子写（临时文件 + ``os.replace``），避免半截 JSON 被下游读到。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def print_report(report: EvalReport, path: Path) -> None:
    """人读输出：每模块一行 score + 失败探针名。"""
    print("=== core 模块评测（LWP18 行为探针）===")
    for m in report.modules:
        status = "PASS" if m.score == 1.0 else "FAIL"
        print(f"  {m.module:<16} score={m.score:.2f}  {status}")
        for name in m.failed_probes:
            print(f"      ✗ {name}")
    print(f"  总体 score={report.score:.4f}  passed={report.passed}")
    print(f"  结果已写入 {path}")


def main(argv: list[str] | None = None) -> int:
    now = datetime.now()
    path = result_path(now)
    # 探针**故意**触发失败路径，真实模块会打 warning（retry_sync / degradation_skip）。
    # 那是被测行为的正常回声，不是门禁结论 —— 评测期间关掉日志，保证 stdout 干净可解析。
    prev_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        report = evaluate_all(LingShuModules())
    finally:
        logging.disable(prev_disable)
    write_payload(build_payload(report, now), path)
    print_report(report, path)
    return exit_code_for(report)


if __name__ == "__main__":
    try:
        code = main()
    except Exception:  # noqa: BLE001 — 评测器自身故障：traceback 到 stderr，退出 1
        traceback.print_exc()
        sys.exit(EXIT_ERROR)
    # 成功路径刻意「静默」：只有非 0 才值得人看一眼
    sys.exit(code)
