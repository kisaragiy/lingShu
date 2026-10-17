#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""harness.py — 长任务 harness（WP5）

干什么
------
给「横跨多个会话的长任务」一个**机器可读的进度载体** PROGRESS.json，
替代散文进度（AGENTS.md / daily 日志）——散文一压缩就断片。

三条硬规则（对应 Anthropic《Effective harnesses for long-running agents》的失败模式）
  1. feature list 用 JSON 存（模型/工具更不易乱改），每条标 passes 布尔。
  2. 会话末必须 clean state：PROGRESS.json 落盘（本工具负责），代码由你 git 提交。
  3. **没验证证据不许标完成**：`pass` 必须带 --evidence，否则拒绝。

退出码契约（对齐 scripts/lib/contract.py）
  0 = OK   · 2 = ALERT（发现需人注意的问题/请求被拒）· 1 = ERROR（工具自身故障）
  干净时静默：`check` 型（watchdog）在不变量成立时不打印任何东西。

用法
----
  harness.py [--dir DIR] init --project NAME [--from-template] [--force]
  harness.py [--dir DIR] status [--json]
  harness.py [--dir DIR] next   [--json]
  harness.py [--dir DIR] pass  <feature-id> --evidence "..." [--note "..."]
  harness.py [--dir DIR] unpass <feature-id> [--note "..."]
  harness.py [--dir DIR] add   <feature-id> --desc "..." [--smoke]
  harness.py [--dir DIR] check  [--json]

--dir 默认取环境变量 HARNESS_DIR，其次当前工作目录。
PROGRESS.json 位于 <dir>/PROGRESS.json。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
from datetime import datetime, timezone

OK, ERROR, ALERT = 0, 1, 2

# 优先复用仓库内统一的契约库（scripts/lib/contract.py）；
# 外部环境可用 LINGSHU_HARNESS_LIB 指定；都没有时退回本地等价物。
try:  # pragma: no cover
    _HERE = pathlib.Path(__file__).resolve()
    for _cand in (_HERE.parent.parent / "scripts", os.environ.get("LINGSHU_HARNESS_LIB", "")):
        if not _cand:
            continue
        _p = pathlib.Path(_cand)
        if (_p / "lib" / "contract.py").exists():
            sys.path.insert(0, str(_p))
            from lib.contract import emit as _emit  # type: ignore
            break
    else:
        _emit = None
except Exception:  # noqa: BLE001
    _emit = None


def emit(msg: str) -> None:
    sys.stdout.write(msg.rstrip("\n") + "\n")


def emit_json(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")


def err(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
PLACEHOLDER = {"done", "ok", "yes", "fixed", "n/a", "na", "none", "-", "todo"}
# 长度已达标但零信息的「糊弄话」整串（只做整串匹配：写成
# "pytest -q => 全绿 41 passed" 不会被误伤，那是合格的输出摘要）。
# 存在理由：PLACEHOLDER 里全是 <8 字符，会被长度门抢先拦下，
# 于是「占位符」这条守卫实际只对短词生效，长糊弄话曾是合法证据。
FAKE_EVIDENCE = {
    "no evidence", "see logs", "verified", "already verified", "test pass",
    "tests pass", "lgtm", "all good", "全绿", "已完成", "测试通过", "没问题",
}

TEMPLATE_FEATURES = [
    {"id": "f1", "desc": "替换为一条可验收的行为（做什么 + 怎么算成功）", "passes": False,
     "evidence": None, "verified_at": None, "notes": ""},
]


class ProgressError(Exception):
    """PROGRESS.json 缺失/损坏。"""


def progress_path(d: str) -> pathlib.Path:
    return pathlib.Path(d).resolve() / "PROGRESS.json"


def load(d: str) -> dict:
    p = progress_path(d)
    if not p.exists():
        raise ProgressError(f"未找到 {p}（先跑 harness.py init）")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProgressError(f"{p} 不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict) or "features" not in data:
        raise ProgressError(f"{p} 结构不对：缺 features")
    return data


def save(d: str, data: dict) -> None:
    data["updated"] = now_iso()
    p = progress_path(d)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)  # 原子替换：避免半截文件


def next_feature(data: dict):
    for f in data.get("features", []):
        if not f.get("passes"):
            return f
    return None


# ---------------------------------------------------------------- integrity
def invariants(data: dict) -> list[str]:
    """返回违规列表（空 = 干净）。check 型 watchdog 靠它决定是否出声。"""
    problems = []
    seen = set()
    feats = data.get("features")
    if not isinstance(feats, list) or not feats:
        problems.append("features 为空或不是数组")
        return problems
    for i, f in enumerate(feats):
        fid = f.get("id", f"<#{i}>")
        if not ID_RE.match(str(fid)):
            problems.append(f"feature id 非法：{fid!r}")
        if fid in seen:
            problems.append(f"feature id 重复：{fid}")
        seen.add(fid)
        if f.get("passes") and not (f.get("evidence") or "").strip():
            problems.append(f"{fid}: passes=true 但没有 evidence（违反「没证据不许标完成」）")
        if not str(f.get("desc", "")).strip():
            problems.append(f"{fid}: desc 为空")
    return problems


def require_evidence(ev: str | None) -> str | None:
    """返回错误字符串；None 表示证据合格。"""
    if ev is None:
        return "拒绝：`pass` 必须带 --evidence \"<命令或输出摘要>\"（没验证证据不许标完成）"
    s = ev.strip()
    norm = " ".join(s.split()).lower()          # 折叠空白 + 统一小写，再判整串
    if norm in PLACEHOLDER or norm in FAKE_EVIDENCE:
        return (f"拒绝：证据 {s!r} 是占位符/糊弄话，不算验证。"
                f"写成命令或输出摘要，例如 --evidence \"python t.py => 42\"")
    if len(s) < 8:
        return f"拒绝：证据太短（{len(s)} 字符，至少 8）。写成命令或输出摘要，例如 --evidence \"python t.py => 42\""
    return None


# ---------------------------------------------------------------- commands
def cmd_init(args) -> int:
    p = progress_path(args.dir)
    if p.exists() and not args.force:
        emit(f"拒绝：{p} 已存在（要覆盖加 --force）")
        return ALERT
    feats = json.loads(json.dumps(TEMPLATE_FEATURES))
    if args.feature:
        feats = []
        for spec in args.feature:  # "id:desc"
            fid, _, desc = spec.partition(":")
            feats.append({"id": fid.strip(), "desc": desc.strip() or "（待补充）",
                          "passes": False, "evidence": None, "verified_at": None, "notes": ""})
    data = {
        "schema_version": 1,
        "project": args.project or p.parent.name,
        "created": now_iso(),
        "updated": now_iso(),
        "smoke_cmd": args.smoke or "",
        "notes": "由 harness.py init 生成。会话末请 commit 此文件。",
        "features": feats,
    }
    bad = invariants(data)
    if bad:
        emit("拒绝：初始 feature 不合法：\n  - " + "\n  - ".join(bad))
        return ALERT
    p.parent.mkdir(parents=True, exist_ok=True)
    save(args.dir, data)
    emit(f"已创建 {p}（{len(feats)} 个 feature）")
    return OK


def cmd_status(args) -> int:
    data = load(args.dir)
    feats = data["features"]
    pending = [f for f in feats if not f.get("passes")]
    nx = next_feature(data)
    done_n = len(feats) - len(pending)
    if args.json:
        emit_json({
            "project": data.get("project"),
            "total": len(feats), "done": done_n, "pending": len(pending),
            "next": ({"id": nx["id"], "desc": nx["desc"]} if nx else None),
            "pending_features": [{"id": f["id"], "desc": f.get("desc", "")} for f in pending],
            "smoke_cmd": data.get("smoke_cmd", ""),
        })
    else:
        emit(f"project: {data.get('project')}   [{done_n}/{len(feats)} done]")
        if not pending:
            emit("全部 feature 已完成 ✅")
        else:
            emit(f"未完成 {len(pending)} 个：")
            for f in pending:
                mark = "→ next" if nx and f["id"] == nx["id"] else "       "
                emit(f"  {mark}  {f['id']}: {f.get('desc','')}")
            emit(f"下一步：{nx['id']}")
    return OK


def cmd_next(args) -> int:
    data = load(args.dir)
    nx = next_feature(data)
    if args.json:
        emit_json({"next": ({"id": nx["id"], "desc": nx["desc"]} if nx else None)})
    elif nx:
        emit(f"{nx['id']}: {nx['desc']}")
    else:
        emit("全部 feature 已完成 ✅")
    return OK


def cmd_pass(args) -> int:
    data = load(args.dir)
    problem = require_evidence(args.evidence)
    if problem:
        emit(problem)
        return ALERT
    target = None
    for f in data["features"]:
        if f.get("id") == args.feature_id:
            target = f
            break
    if target is None:
        emit(f"拒绝：找不到 feature {args.feature_id!r}")
        return ALERT
    if target.get("passes"):
        emit(f"拒绝：{args.feature_id} 已经 passes=true（要重开先 unpass）")
        return ALERT
    target["passes"] = True
    target["evidence"] = args.evidence.strip()
    target["verified_at"] = now_iso()
    if args.note is not None:
        target["notes"] = args.note
    save(args.dir, data)
    nx = next_feature(data)
    emit(f"✔ {args.feature_id} 标记完成（证据已记录）")
    emit(f"下一步：{nx['id']}" if nx else "下一步：无（全部完成）")
    return OK


def cmd_unpass(args) -> int:
    data = load(args.dir)
    for f in data["features"]:
        if f.get("id") == args.feature_id:
            f["passes"] = False
            f["evidence"] = None
            f["verified_at"] = None
            if args.note is not None:
                f["notes"] = args.note
            save(args.dir, data)
            emit(f"↺ {args.feature_id} 已重开")
            return OK
    emit(f"拒绝：找不到 feature {args.feature_id!r}")
    return ALERT


def cmd_add(args) -> int:
    data = load(args.dir)
    if any(f.get("id") == args.feature_id for f in data["features"]):
        emit(f"拒绝：feature {args.feature_id!r} 已存在")
        return ALERT
    if not ID_RE.match(args.feature_id):
        emit(f"拒绝：id 非法 {args.feature_id!r}")
        return ALERT
    data["features"].append({"id": args.feature_id, "desc": args.desc, "passes": False,
                             "evidence": None, "verified_at": None, "notes": ""})
    if args.smoke:
        data["smoke_cmd"] = args.smoke
    save(args.dir, data)
    emit(f"已追加 {args.feature_id}")
    return OK


def cmd_check(args) -> int:
    """watchdog 型：不变量成立时静默退出 0；否则 ALERT。"""
    try:
        data = load(args.dir)
    except ProgressError as exc:
        if args.json:
            emit_json({"status": "alert", "name": "harness", "message": str(exc)})
        else:
            emit(f"[harness] {exc}")
        return ALERT
    bad = invariants(data)
    if not bad:
        if args.json:
            emit_json({"status": "ok", "project": data.get("project")})
        return OK  # 干净 → 静默
    msg = "PROGRESS.json 不变量被破坏：\n  - " + "\n  - ".join(bad)
    if args.json:
        emit_json({"status": "alert", "name": "harness", "message": msg})
    else:
        emit(f"[harness] {msg}")
    return ALERT


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="harness.py", description="长任务 harness（PROGRESS.json）")
    ap.add_argument("--dir", default=os.environ.get("HARNESS_DIR") or os.getcwd(),
                    help="PROGRESS.json 所在目录（默认 $HARNESS_DIR 或 cwd）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="创建 PROGRESS.json")
    p.add_argument("--project", default=None)
    p.add_argument("--smoke", default=None, help="冒烟命令（写进 smoke_cmd）")
    p.add_argument("--feature", action="append", default=None,
                   help="重复：--feature id:desc")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("status", help="未完成 feature + 下一个")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("next", help="只打印下一个该做的")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_next)

    p = sub.add_parser("pass", help="标记完成（必须带证据）")
    p.add_argument("feature_id")
    p.add_argument("--evidence", default=None, required=False,
                   help="命令或输出摘要；缺证据直接拒绝")
    p.add_argument("--note", default=None)
    p.set_defaults(func=cmd_pass)

    p = sub.add_parser("unpass", help="重开一个 feature")
    p.add_argument("feature_id")
    p.add_argument("--note", default=None)
    p.set_defaults(func=cmd_unpass)

    p = sub.add_parser("add", help="追加一个 feature")
    p.add_argument("feature_id")
    p.add_argument("--desc", required=True)
    p.add_argument("--smoke", default=None)
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("check", help="watchdog：不变量被破坏才出声")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_check)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ProgressError as exc:
        emit(f"[harness] {exc}")
        return ALERT
    except KeyboardInterrupt:
        err("中断")
        return ERROR
    except BaseException as exc:  # noqa: BLE001 — 契约：绝不静默
        import traceback
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
        err(f"harness.py: 未捕获异常 {type(exc).__name__}: {exc}")
        return ERROR


if __name__ == "__main__":
    sys.exit(main())
