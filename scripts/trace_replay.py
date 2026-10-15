#!/usr/bin/env python3
"""trace 回放回归 CLI（P2-1）—— 冻结真实轨迹成回归用例，回放断言新代码行为。

用法（repo 根，项目 venv）::

    # 1) 从 run 日志冻结一条轨迹成固定用例
    ./.venv/Scripts/python scripts/trace_replay.py freeze \\
        --from-log <agent_logs/x.jsonl> --out fixtures/trace_x.json [--name x]

    # 2) 查看轨迹
    ./.venv/Scripts/python scripts/trace_replay.py show --trace fixtures/trace_x.json

    # 3) 用新代码回放（handler 为 "pkg.mod:func"，签名 step->output）
    ./.venv/Scripts/python scripts/trace_replay.py replay \\
        --trace fixtures/trace_x.json [--handler pkg.mod:func] [--cut-point 3]

不带 ``--handler`` 时回放 = 录制输出原样复现（恒匹配，用于确定性自检）。
有差异时退出码为 1，可挂进 CI。
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from agent_harness.core import trace_replay as tr  # noqa: E402

# 同一仓库已有约定：外部程序调用收敛到唯一封装；本 CLI 不调用任何外部程序。


def _load_handler(spec: str | None):
    """把 "pkg.mod:func" 解析成可调用；None → None（纯回放）。"""
    if not spec:
        return None
    if ":" not in spec:
        raise SystemExit(f"--handler 需形如 'pkg.mod:func'，收到 {spec!r}")
    mod_name, fn_name = spec.split(":", 1)
    mod = importlib.import_module(mod_name)
    fn = getattr(mod, fn_name, None)
    if fn is None:
        raise SystemExit(f"{mod_name} 里找不到 {fn_name}")
    return fn


def _cmd_freeze(args) -> int:
    trace = tr.freeze_from_log(args.from_log, name=args.name)
    path = trace.save(args.out)
    print(f"冻结 {len(trace)} 步 → {path}")
    print(json.dumps(trace.to_dict(), ensure_ascii=False, indent=2, default=str))
    return 0


def _cmd_show(args) -> int:
    trace = tr.FrozenTrace.load(args.trace)
    print(f"轨迹 {trace.name}: {len(trace)} 步")
    print(json.dumps(trace.to_dict(), ensure_ascii=False, indent=2, default=str))
    return 0


def _cmd_replay(args) -> int:
    trace = tr.FrozenTrace.load(args.trace)
    handler = _load_handler(args.handler)
    report = tr.replay(trace, handler, cut_point=args.cut_point)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=str))
    if not report.matched_all:
        print(f"\n⚠️  {report.mismatched}/{report.total} 步行为改变")
        return 1
    print(f"\n✅ 全部 {report.total} 步与录制一致")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="trace 回放回归（P2-1）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_freeze = sub.add_parser("freeze", help="从 run 日志冻结轨迹")
    p_freeze.add_argument("--from-log", required=True)
    p_freeze.add_argument("--out", required=True)
    p_freeze.add_argument("--name", default=None)
    p_freeze.set_defaults(func=_cmd_freeze)

    p_show = sub.add_parser("show", help="查看轨迹")
    p_show.add_argument("--trace", required=True)
    p_show.set_defaults(func=_cmd_show)

    p_replay = sub.add_parser("replay", help="回放轨迹（可带 handler 跑新代码）")
    p_replay.add_argument("--trace", required=True)
    p_replay.add_argument("--handler", default=None, help="'pkg.mod:func'，省略=纯回放")
    p_replay.add_argument("--cut-point", type=int, default=None,
                          help="只 live 跑 index>=cut_point 的步")
    p_replay.set_defaults(func=_cmd_replay)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
