#!/usr/bin/env python3
"""Risk-aware LOCAL test selection; release/CI gates remain exhaustive.

By default inspect staged paths (git diff --cached). Unknown and high-risk
changes fall back to the complete offline suite. No test result is synthesized.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]

# Deliberately small, audited allowlist. Extend only with evidence that
# no cross-module integration tests are missed by a quick local check.
FAST_PATHS: dict[str, tuple[str, ...]] = {
    "src/agent_harness/core/slo.py": ("tests/test_slo.py",),
    "src/agent_harness/core/failure_attribution.py": (
        "tests/test_failure_attribution.py",
        "tests/test_module_eval_gate.py",
    ),
    "src/agent_harness/core/judge_registry.py": (
        "tests/test_judge_registry.py",
        "tests/test_eval_isolation.py",
    ),
    "src/agent_harness/core/skill_lifecycle.py": (
        "tests/test_skill_lifecycle.py",
        "tests/test_skill_loader.py",
    ),
    "locustfile.py": ("tests/test_slo.py",),
}
FAST_PREFIXES: dict[str, tuple[str, ...]] = {
    "src/agent_harness/apps/research/static/": (
        "tests/test_frontend_contract.py",
        "tests/test_packaging_assets.py",
    ),
    "src/agent_harness/static/admin/": (
        "tests/test_frontend_contract.py",
        "tests/test_packaging_assets.py",
    ),
}


def _is_documentation(path: str) -> bool:
    return (
        path.endswith(".md")
        or path.startswith("docs/") and path.endswith((".txt", ".png", ".jpg", ".svg"))
        or path in {"LICENSE", "mkdocs.yml"}
    )


def staged_paths() -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMRTD"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return sorted(set(p.decode("utf-8", errors="surrogateescape").replace("\\", "/")
                      for p in result.stdout.split(b"\0") if p))


def select_tests(
    paths: Iterable[str],
    exists: Callable[[str], bool] | None = None,
) -> tuple[str, list[str], str]:
    """Return (mode: skip|selected|full, test files, reason).

    A local selected run is NOT equivalent to passing full release gates.
    """
    exists = exists or (lambda p: (ROOT / p).is_file())
    changed = sorted(set(p.replace("\\", "/") for p in paths if p))
    if not changed:
        return ("skip", [], "没有 staged 文件")
    chosen: set[str] = set()
    for path in changed:
        if _is_documentation(path):
            continue
        if path in {"tests/conftest.py", "tests/__init__.py"} or path.startswith("tests/fixtures/"):
            return ("full", [], f"测试基础设施变更: {path}")
        if path.startswith("tests/") and path.endswith(".py") and Path(path).name.startswith("test_"):
            if "integration" in Path(path).name:
                return ("full", [], f"集成测试文件变更: {path}（在线集成仍由 CI 单独执行）")
            chosen.add(path)
            continue
        if path in FAST_PATHS:
            chosen.update(FAST_PATHS[path])
            continue
        matched = next((tests for prefix, tests in FAST_PREFIXES.items() if path.startswith(prefix)), None)
        if matched is not None:
            chosen.update(matched)
            continue
        # Source, dependency, auth, safety, graph, config, hook, workflow or
        # unknown changes: never guess their impact. Full offline tests.
        return ("full", [], f"无法证明仅影响局部，执行完整离线回归: {path}")
    if not chosen:
        return ("skip", [], "仅文档变更，不运行 pytest（版本一致性仍由 hook 检查）")
    missing = sorted(p for p in chosen if not exists(p))
    if missing:
        return ("full", [], f"目标测试缺失或删除，回退全量: {', '.join(missing)}")
    return ("selected", sorted(chosen), "已审计的局部影响范围")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="强制完整离线回归")
    parser.add_argument("--execute", action="store_true", help="实际运行 pytest；默认仅展示选择")
    parser.add_argument("--paths", nargs="*", default=None, help="手动指定变更文件；不传则读取 staged")
    args = parser.parse_args(argv)
    try:
        paths = args.paths if args.paths is not None else staged_paths()
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"[test-selection] 无法读取 staged 变更: {exc}", file=sys.stderr)
        return 2
    mode, targets, reason = select_tests(paths)
    if args.full:
        mode, targets, reason = ("full", [], "显式 --full")
    print(f"[test-selection] mode={mode}; reason={reason}", flush=True)
    if mode == "selected":
        print(f"[test-selection] targets={', '.join(targets)}", flush=True)
    if not args.execute or mode == "skip":
        return 0
    command = [sys.executable, "-m", "pytest", "-x", "-q"]
    command.extend(targets if mode == "selected" else ["tests/"])
    started = time.perf_counter()
    try:
        result = subprocess.run(command, cwd=ROOT, check=False)
    except OSError as exc:
        print(f"[test-selection] 无法启动 pytest: {exc}", file=sys.stderr)
        return 2
    elapsed = time.perf_counter() - started
    print(f"[test-selection] mode={mode}; duration_s={elapsed:.2f}; exit={result.returncode}", flush=True)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
