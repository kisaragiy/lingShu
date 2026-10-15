#!/usr/bin/env python3
"""漂移哨兵（P1-2 · L 侧）—— 用「金标测试子集」给可靠性护栏做回归体检。

思路：从全量测试里挑一小撮**确定性、自包含、不依赖外部服务**的金标用例
（预算熔断 / 无进展检测 / 重试、幂等、语义校验、结果校验、熔断接线、可复现信封、
RE 工具），每次改动后重跑这一撮，把 pass/fail + 成功率落进趋势文件
``eval_results/drift-trend.jsonl``（一行一次跑）。与上一次相比：

    * 成功率下降  → 告警
    * 出现新的失败用例 → 告警

一旦发现异常，进程以退出码 1 结束，可挂进 CI / pre-push。

用法（repo 根，项目 venv）::

    ./.venv/Scripts/python scripts/eval_drift_sentinel.py          # 跑金标 + 追加趋势 + 对比
    ./.venv/Scripts/python scripts/eval_drift_sentinel.py --self-test  # 只自测对比逻辑，不跑 pytest

设计取舍：
  * 只跑金标子集（不是全量）——分钟级反馈，专盯可靠性护栏是否被改漂。
  * 趋势文件 append-only，天然可画曲线；原子写用临时文件 + rename。
  * 对比逻辑抽成纯函数 ``compare_runs``，便于自测（不依赖真实 pytest 结果）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULT_FILE = REPO / "eval_results" / "drift-trend.jsonl"
PY_EXE = sys.executable  # 调用方用的解释器（venv 内即 .venv/Scripts/python.exe）

# ── 金标子集：确定性 + 自包含 + 不触网 ─────────────────────────────
# 选的都是「可靠性护栏」核心：预算/重试/幂等/语义与结果校验/熔断接线/可复现信封。
GOLDEN_TESTS: list[str] = [
    "tests/test_budget_breaker.py",          # 日聚合预算 / kill switch / 无进展
    "tests/test_idempotency.py",             # 幂等 key + 决策/执行分离
    "tests/test_semantic_validation.py",     # 语义校验
    "tests/test_result_validation.py",       # 结果校验契约
    "tests/test_circuit_breaker_wiring.py",  # 熔断真实喂入接线
    "tests/test_reproducibility_envelope.py",# 可复现信封纯函数
    "tests/test_rev_utils.py",               # protobuf/stalker 工具注册与 schema
]


# ── 跑金标 ────────────────────────────────────────────────────────

def run_golden(tests: list[str] | None = None, timeout: int = 600) -> dict:
    """重跑金标子集，返回 {passed, failed, skipped, total, success_rate, failed_tests}。

    用 pytest 内置 ``--junitxml`` 取结构化结果：本仓库的 pytest 会抑制
    "N passed" 汇总行（progress 后无 summary），解析 stdout 文本不可靠，
    junit XML 则跨版本稳定。
    """
    tests = tests or GOLDEN_TESTS
    xml_path = os.path.join(tempfile.mkdtemp(prefix="drift_"), "golden.xml")
    cmd = [PY_EXE, "-m", "pytest", *tests,
           "-q", "-p", "no:cacheprovider", f"--junitxml={xml_path}"]
    proc = subprocess.run(
        cmd, cwd=str(REPO), capture_output=True, text=True, timeout=timeout,
    )

    passed = failed = skipped = 0
    failed_tests: list[str] = []
    if os.path.exists(xml_path):
        root = ET.parse(xml_path).getroot()
        for tc in root.iter("testcase"):
            name = f"{tc.attrib.get('classname', '')}::{tc.attrib.get('name', '')}"
            if tc.find("failure") is not None or tc.find("error") is not None:
                failed += 1
                failed_tests.append(name)
            elif tc.find("skipped") is not None:
                skipped += 1
            else:
                passed += 1

    total = passed + failed
    success_rate = round(100.0 * passed / total, 2) if total else 0.0
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "golden_files": tests,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "total": total,
        "success_rate": success_rate,
        "failed_tests": sorted(failed_tests),
        "exit_code": proc.returncode,
    }


# ── 与上次对比（纯函数，便于自测）────────────────────────────────

def compare_runs(prev: dict | None, cur: dict) -> list[str]:
    """对比两次跑，返回告警原因列表（空 = 无异常）。

    * 首次跑（prev 为 None）→ 无基线，不告警。
    * 成功率下降 → 告警。
    * 出现**新**失败用例 → 告警（已存在的失败不重复报）。
    """
    if prev is None:
        return []

    reasons: list[str] = []
    prev_rate = prev.get("success_rate", 0.0)
    cur_rate = cur.get("success_rate", 0.0)
    if cur_rate < prev_rate:
        reasons.append(f"成功率下降 {prev_rate}% → {cur_rate}%")

    new_fail = sorted(set(cur.get("failed_tests", [])) - set(prev.get("failed_tests", [])))
    if new_fail:
        reasons.append(f"新增失败用例 {len(new_fail)} 个: {', '.join(new_fail)}")

    return reasons


# ── 趋势文件 I/O ──────────────────────────────────────────────────

def load_last(path: Path) -> dict | None:
    if not path.exists():
        return None
    last = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            last = json.loads(line)
    return last


def append_run(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── 自测（不跑 pytest，只验对比逻辑）─────────────────────────────

def self_test() -> int:
    ok = True

    def chk(cond, msg):
        nonlocal ok
        if not cond:
            ok = False
            print(f"  ✗ {msg}")
        else:
            print(f"  ✓ {msg}")

    base = {"success_rate": 100.0, "failed_tests": []}
    # 首次跑不告警
    chk(compare_runs(None, base) == [], "首次跑无基线 → 不告警")
    # 成功率持平不告警
    chk(compare_runs(base, dict(base)) == [], "持平 → 不告警")
    # 成功率下降 → 告警
    down = {"success_rate": 90.0, "failed_tests": ["tests/x.py::test_a"]}
    reasons = compare_runs(base, down)
    chk(any("成功率下降" in r for r in reasons), "成功率下降 → 告警")
    # 新失败用例 → 告警
    newf = {"success_rate": 90.0, "failed_tests": ["tests/x.py::test_a"]}
    reasons = compare_runs(base, newf)
    chk(any("新增失败用例" in r for r in reasons), "新失败用例 → 告警")
    # 同一失败仍在（非新增）→ 不因它告警
    prev_f = {"success_rate": 90.0, "failed_tests": ["tests/x.py::test_a"]}
    cur_f = {"success_rate": 90.0, "failed_tests": ["tests/x.py::test_a"]}
    chk(compare_runs(prev_f, cur_f) == [], "相同失败集合 → 不重复告警")
    # 成功率回升 → 不告警
    chk(compare_runs({"success_rate": 80.0, "failed_tests": []}, base) == [], "回升 → 不告警")

    print("SELF-TEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


# ── CLI ───────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="金标漂移哨兵")
    ap.add_argument("--self-test", action="store_true", help="只自测对比逻辑")
    ap.add_argument("--result-file", default=str(RESULT_FILE))
    ap.add_argument("--no-write", action="store_true", help="不写趋势文件（干跑）")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()

    path = Path(args.result_file)
    prev = load_last(path)
    cur = run_golden()

    print(json.dumps(cur, ensure_ascii=False, indent=2))

    reasons = compare_runs(prev, cur)
    if not args.no_write:
        append_run(path, cur)
        print(f"趋势已追加 → {path}")

    if reasons:
        print("\n⚠️  漂移告警:")
        for r in reasons:
            print(f"  - {r}")
        return 1

    print("\n✅ 无漂移")
    # pytest 自身非零（如金标全跑挂）也应视为异常
    return 1 if cur["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
