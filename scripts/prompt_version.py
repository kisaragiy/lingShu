#!/usr/bin/env python3
"""prompt/config 版本戳 CLI（P1-7 · L 侧 门禁入口）。

把 ``core.versioning`` 的能力接到命令行，用于把 prompt 模板 / system prompt /
关键 config 的**内容哈希版本**存档并当作变更门禁：

    stamp   : 计算当前版本清单，写入 manifest JSON（建立/更新基线）
    check   : 与基线比对；有 added/removed/changed → 打印差异并以退出码 1 结束
    show    : 只打印当前版本清单（不落盘）

用法（repo 根，项目 venv）::

    ./.venv/Scripts/python scripts/prompt_version.py stamp
    ./.venv/Scripts/python scripts/prompt_version.py check
    ./.venv/Scripts/python scripts/prompt_version.py --self-test

门禁怎么用：CI / pre-push 上跑 ``check``。版本戳变了说明**行为输入**（prompt/config）
发生了实质变更 —— 此时应连同金标集（``scripts/eval_drift_sentinel.py``）一起重跑：
版本戳变 + 金标成功率掉 = 高置信度的行为回归；版本戳变但金标全绿 = 变更可控、可放行。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from agent_harness.core import versioning  # noqa: E402

MANIFEST = REPO / "eval_results" / "prompt-versions.json"


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def cmd_stamp(path: Path) -> int:
    cur = versioning.collect_versions()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {len(cur)} 条版本戳 → {path}")
    for name, meta in cur.items():
        print(f"  {meta.get('stamp', '-'):14} {name}")
    return 0


def cmd_check(path: Path) -> int:
    prev = _load(path)
    if prev is None:
        print(f"⚠️  无基线 {path} —— 先跑 `stamp`")
        return 0
    cur = versioning.collect_versions()
    d = versioning.diff_manifests(prev, cur)
    if not (d["added"] or d["removed"] or d["changed"]):
        print("✅ 版本戳无变化")
        return 0
    print("⚠️  版本戳变更:")
    for kind in ("added", "removed", "changed"):
        for name in d[kind]:
            print(f"  [{kind}] {name}")
    print("\n提示：行为输入已变 → 请连同金标集重跑 "
          "`scripts/eval_drift_sentinel.py` 确认无漂移。")
    return 1


def cmd_show() -> int:
    cur = versioning.collect_versions()
    for name, meta in cur.items():
        print(f"  {meta.get('stamp', '-'):14} {name}")
    return 0


def self_test() -> int:
    ok = True

    def chk(cond, msg):
        nonlocal ok
        ok = ok and cond
        print(f"  {'✓' if cond else '✗'} {msg}")

    # 版本戳稳定且敏感
    chk(versioning.version_stamp("abc") == versioning.version_stamp("abc"), "同内容 → 同戳")
    chk(versioning.version_stamp("abc") != versioning.version_stamp("abd"), "异内容 → 异戳")
    chk(len(versioning.version_stamp("x")) == versioning.STAMP_LEN, "戳长度固定")

    # collect_versions 能收齐 prompt + config，且每个都有 sha256
    cur = versioning.collect_versions()
    n_prompt = sum(1 for k in cur if k.startswith("prompt:"))
    n_config = sum(1 for k in cur if k.startswith("config:"))
    chk(n_prompt == len(versioning.PROMPT_SOURCES), f"收纳全部 prompt 源 ({n_prompt})")
    chk(n_config == len(versioning.CONFIG_FIELDS), f"收纳全部 config 字段 ({n_config})")
    chk(all("sha256" in m for m in cur.values()), "每项都有 sha256（无缺项）")

    # diff：无变化 / 变化 / 新增 / 删除
    base = {"a": {"sha256": "1"}, "b": {"sha256": "2"}}
    chk(versioning.diff_manifests(base, dict(base)) == {"added": [], "removed": [], "changed": []},
        "相同清单 → 无差异")
    d = versioning.diff_manifests(base, {"a": {"sha256": "9"}, "b": {"sha256": "2"}})
    chk(d["changed"] == ["a"], "内容变更 → changed")
    d = versioning.diff_manifests(base, {"a": {"sha256": "1"}, "b": {"sha256": "2"}, "c": {"sha256": "3"}})
    chk(d["added"] == ["c"], "新增项 → added")
    d = versioning.diff_manifests(base, {"a": {"sha256": "1"}})
    chk(d["removed"] == ["b"], "移除项 → removed")

    print("SELF-TEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="prompt/config 版本戳")
    ap.add_argument("cmd", nargs="?", choices=["stamp", "check", "show"], default="show")
    ap.add_argument("--manifest", default=str(MANIFEST))
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.cmd == "stamp":
        return cmd_stamp(Path(args.manifest))
    if args.cmd == "check":
        return cmd_check(Path(args.manifest))
    return cmd_show()


if __name__ == "__main__":
    raise SystemExit(main())
