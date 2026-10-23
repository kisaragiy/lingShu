"""mypy 增量门禁：只拦「新增」类型错误，不因历史债假红。

为什么需要它（实测 2026-10-23）：`core/` 74 个文件里 59 个有 strict 错误，共
674 个。直接 `mypy --strict` 全量 blocking 会立刻红，且没人能一次修完 674 个 →
门禁会被删掉，比没有更糟；而「只查改动文件」也不行 —— 改到任何一个脏文件就红，
等于禁止改核心代码。

做法：仓库根放一份 baseline（文件 → 该文件 strict 错误数），CI 只对**改动文件**
跑 strict，错误数 > baseline 即红。新文件 baseline 视为 0（必须干净）。
baseline 只许往下调：修好一处就跑 `--update` 收紧，diff 里能看到收紧幅度。

用法：
    python scripts/mypy_baseline.py --check file1.py file2.py   # CI
    python scripts/mypy_baseline.py --update                    # 全量重算并写 baseline

退出码（沿用 0=OK / 2=ALERT / 1=ERROR 契约）：
    0 无新增错误  2 有新增（CI 红）  1 脚本自身故障（找不到 baseline / 解释器异常）
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "mypy_baseline.json"
SCAN_DIRS = ("src/agent_harness/core",)

MYPY_ARGS = [
    "-m", "mypy", "--strict",
    "--follow-imports=skip",
    "--ignore-missing-imports",
    "--allow-untyped-decorators",
    "--no-error-summary",
    "--no-pretty",
    # 必须有：mypy 增量缓存会让「全量扫一次」与「再扫一次」的报错数不一致
    # （实测 2026-10-23 出现过同一文件 57 → 0 的假象），baseline 比对就失去意义。
    "--no-incremental",
]

# mypy 输出形如：src\x\y.py:12: error: Incompatible return value  [return-value]
_ERROR_LINE = re.compile(r"^(.*?\.py):\d+: error:")


def _norm(path: str) -> str:
    return Path(path.replace("\\", "/")).as_posix()


def _scan_files() -> list[str]:
    """扫描待检文件，返回**相对 ROOT** 的 posix 路径。

    必须是相对路径：``--check`` 的 restrict 来自命令行（相对），若这里返回绝对路径，
    过滤后 targets 为空 → 报「N → 0 个错误」的假绿（2026-10-23 实测踩过）。
    """
    scanned: list[str] = []
    for directory in SCAN_DIRS:
        scanned.extend(sorted(p.relative_to(ROOT).as_posix()
                              for p in (ROOT / directory).rglob("*.py")))
    return scanned


def _run_mypy(files: list[str], restrict: set[str] | None = None) -> dict[str, int]:
    """逐文件跑 mypy，返回 {相对路径: strict 错误数}。

    为什么逐文件而不是一次全量：实测（2026-10-23）把 core/ 的 74 个文件一次性交给
    mypy --strict 时，它只吐出 1 条错误、退出码 2（stderr 空）——进程中途崩了，
    于是"全量结果"只是部分结果，baseline 直接变成垃圾（同一文件一会儿 57、一会儿 0）。
    逐文件调用慢一些（每文件 ~0.8s），但每次的上下文完全一致，计数可复现。

    ``restrict``：只跑这些文件（check 用），但语义与 update 完全相同。
    """
    targets = [f for f in files if restrict is None or _norm(f) in restrict]
    counts: dict[str, int] = {}
    for target in targets:
        proc = subprocess.run(
            [sys.executable, *MYPY_ARGS, target],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        # mypy 的退出码语义：0 无错、1 有类型错误、2 内部/命令行错误。实测会返回 2
        # 且 stderr 为空、stdout 却有完整 error 行 —— 判据是**有没有 error 输出**。
        if proc.returncode not in (0, 1) and "error:" not in proc.stdout:
            print(f"[mypy-gate] mypy 自身失败（{target}, exit {proc.returncode}）：\n"
                  f"{proc.stderr[-800:]}")
            raise SystemExit(1)
        for line in proc.stdout.splitlines():
            match = _ERROR_LINE.match(line.strip())
            if match:
                key = _norm(match.group(1))
                counts[key] = counts.get(key, 0) + 1
    return counts


def _load() -> dict[str, int]:
    if not BASELINE.exists():
        print(f"[mypy-gate] baseline 缺失：{BASELINE}（先跑 --update 并提交）")
        raise SystemExit(1)
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def cmd_check(files: list[str]) -> int:
    baseline = _load()
    rel = set(_norm(f) for f in files)
    # 全量跑（与 baseline 同上下文），只取检查目标的结果
    current = _run_mypy(_scan_files(), restrict=rel)

    regressions = []
    for path, count in sorted(current.items()):
        allowed = int(baseline.get(path, 0))
        if count > allowed:
            regressions.append((path, allowed, count))

    if regressions:
        for path, allowed, count in regressions:
            print(f"[mypy-gate] ✗ {path}: {count} 个 strict 错误 > baseline {allowed}"
                  f"（新增 {count - allowed}）")
        print("[mypy-gate] 修掉新增错误。若这些错误本来就在（baseline 过期），"
              "跑 --update 并 review baseline diff——只允许往下收紧。")
        return 2

    fixed = [
        (p, baseline[p], current.get(p, 0))
        for p in baseline
        if p in rel and current.get(p, 0) < baseline[p]
    ]
    for path, before, now in fixed:
        print(f"[mypy-gate] ↑ {path}: {before} → {now} 个错误，可跑 --update 收紧 baseline")

    print(f"[mypy-gate] OK：检查 {len(rel)} 个文件，无新增 strict 错误")
    return 0


def cmd_update() -> int:
    scanned = _scan_files()
    if not scanned:
        print("[mypy-gate] 没扫到任何文件，检查 SCAN_DIRS")
        return 1

    counts = _run_mypy(scanned)
    payload = dict(sorted(counts.items()))
    BASELINE.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[mypy-gate] baseline 已写：{len(payload)} 个文件有错，"
          f"共 {sum(payload.values())} 个 strict 错误 → {BASELINE}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="mypy 增量门禁（只拦新增 strict 错误）")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", nargs="*", metavar="FILE", help="检查这些文件是否有新增错误")
    group.add_argument("--update", action="store_true", help="全量重算并写 baseline")
    args = parser.parse_args(argv)

    if args.update:
        return cmd_update()
    return cmd_check(args.check or [])


if __name__ == "__main__":
    sys.exit(main())
