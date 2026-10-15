"""judge 注册 / 冻结（P2-3）—— 别让量尺自己漂。

痛点
----
同一 judge（打分 prompt / 目标 / 权重），判分却随 judge 模型更新而变 —— **量尺自己在漂**。
今天 80 分、明天同一份输出 65 分，你根本分不清是产品退步还是量尺挪位；单 judge 还带偏置。

落点（小而真，纯标准库、可离线测；灵枢暂无 LLM judge，就做「版本化的打分类 + 版本戳」）
----------------------------------------------------------------------------------------
1. **JudgeSpec**：名字 + 版本 + 模型标识 + 判据（criteria），导出**稳定戳**
   （reuse ``versioning.version_stamp``，sha256 稳定序列化）。判据 / 模型任一变化 → 戳变。
2. **JudgeRegistry**：注册多版本、按名解析（默认取最新）；:meth:`freeze` 把当前所有 judge 的
   戳固化成锁文件 manifest（"冻结"），:meth:`detect_drift` 与之比对 —— 变了哪个 judge 一目了然，
   并给出 ``needs_recheck``（受影响的 judge）。
3. **多 judge 共识**：:func:`consensus` 聚合多个 judge 的分数（默认中位数）+ 离散度；
   离散度超 ``max_spread`` → ``agree=False``（单 judge 偏置，别信一个数）。
4. **变更重测**：:func:`recheck_samples` 对历史错点样本用「新 judge」重跑，报告翻面情况
   —— 大量「原判错、新判对」= 新 judge 变松（疑似放水），而不是模型进步。

本模块不 import ``apps``（架构方向：core ← apps），也不触网、不写全局态。
"""
from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agent_harness.core.versioning import version_stamp


# ── judge 规范 ────────────────────────────────────────────────

@dataclass(frozen=True)
class JudgeSpec:
    """一个 judge 的**可版本化**规范。内容一变，``stamp`` 就变。

    * ``name``：judge 名（同一名可有多个 ``version``）；
    * ``version``：判据/目标的版本号（字符串，便于 "2024-06" 这类）；
    * ``model``：judge 使用的模型标识（如 ``"deepseek-flash"``；纯规则 judge 留空）；
    * ``criteria``：判据本身（prompt 片段 / 权重 / 规则参数），参与戳计算。
    """
    name: str
    version: str
    model: str = ""
    criteria: dict = field(default_factory=dict)
    notes: str = ""

    def _canonical(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "model": self.model,
            "criteria": self.criteria,
        }

    @property
    def stamp(self) -> str:
        """稳定版本戳（sha256 前 12 位）—— 判据 / 模型任一变化即变。"""
        return version_stamp(self._canonical())

    def to_dict(self) -> dict:
        return {**self._canonical(), "stamp": self.stamp, "notes": self.notes}


def build_spec(name: str, version: str, *, model: str = "",
               criteria: dict | None = None, notes: str = "") -> JudgeSpec:
    """构造 :class:`JudgeSpec` 的便捷入口。"""
    return JudgeSpec(name=name, version=version, model=model,
                     criteria=dict(criteria or {}), notes=notes)


# ── 注册表 + 冻结 ─────────────────────────────────────────────

class JudgeRegistry:
    """judge 注册表：注册多版本、解析、冻结成 manifest、比对漂移。

    冻结语义：:meth:`freeze` 产出 ``{judge 名: stamp}`` 锁文件；此后 :meth:`detect_drift`
    与它比对，任何 judge 的判据 / 模型变化都会在 ``changed`` 里现形。
    """

    def __init__(self) -> None:
        self._by_name: dict[str, list[JudgeSpec]] = {}
        self._manifest: dict[str, str] | None = None

    def register(self, spec: JudgeSpec) -> JudgeSpec:
        """注册（或覆盖同 name+version）一个 judge。"""
        versions = self._by_name.setdefault(spec.name, [])
        for i, s in enumerate(versions):
            if s.version == spec.version:
                versions[i] = spec
                break
        else:
            versions.append(spec)
        return spec

    def names(self) -> list[str]:
        return sorted(self._by_name)

    def resolve(self, name: str, version: str | None = None) -> JudgeSpec | None:
        """取指定 judge；``version=None`` → 最新注册的那版（冻结前的浮动指针）。"""
        versions = self._by_name.get(name)
        if not versions:
            return None
        if version is None:
            return versions[-1]
        for s in versions:
            if s.version == version:
                return s
        return None

    def freeze(self) -> dict[str, str]:
        """把当前所有 judge 的**最新版**戳固化成 manifest（并记为基线）。"""
        self._manifest = {name: self.resolve(name).stamp for name in self.names()}
        return dict(self._manifest)

    @property
    def manifest(self) -> dict[str, str]:
        return dict(self._manifest) if self._manifest is not None else {}

    def detect_drift(self, prev: dict | None = None) -> dict:
        """与冻结基线（或传入 ``prev``）比对，返回漂移。

        ``{added, removed, changed, needs_recheck}``；``needs_recheck`` = 变化/新增的 judge 名
        —— 这些 judge 的判分口径变了，历史结论需重测。
        """
        prev = self.manifest if prev is None else prev
        prev = prev or {}
        cur = {name: self.resolve(name).stamp for name in self.names()}
        added = sorted(set(cur) - set(prev))
        removed = sorted(set(prev) - set(cur))
        changed = sorted(k for k in set(prev) & set(cur) if prev[k] != cur[k])
        return {
            "added": added,
            "removed": removed,
            "changed": changed,
            "needs_recheck": sorted(set(added) | set(changed)),
        }


# ── 多 judge 共识 ─────────────────────────────────────────────

def consensus(
    scores: list[float],
    *,
    method: str = "median",
    max_spread: float = 0.15,
) -> dict:
    """聚合多个 judge 的分数，并给出**离散度**以暴露单 judge 偏置。

    * ``method`` ∈ ``{"median", "mean"}``（默认中位数，对离群 judge 更稳健）；
    * ``spread = max − min``；``agree = spread <= max_spread``。

    空输入 → ``{score: None, n: 0, spread: None, agree: False}``（缺数据不误报「共识」）。
    """
    vals = [float(x) for x in scores]
    if not vals:
        return {"score": None, "n": 0, "spread": None, "agree": False,
                "method": method, "max_spread": max_spread}
    if method == "mean":
        agg = sum(vals) / len(vals)
    else:
        agg = statistics.median(vals)
    spread = max(vals) - min(vals)
    return {
        "score": round(agg, 4),
        "n": len(vals),
        "spread": round(spread, 4),
        "agree": spread <= max_spread,
        "method": method,
        "max_spread": max_spread,
    }


# ── judge 变更 → 重测历史错点样本 ─────────────────────────────

def recheck_samples(
    samples: list[dict],
    judge_fn: Callable[[dict], bool],
    *,
    loosening_rate: float = 0.5,
) -> dict:
    """用「新 judge」重跑历史样本，报告翻面。

    ``samples`` 每项形如 ``{"id": ..., "output": ..., "judge_passed": bool}``，
    ``judge_fn(sample) -> bool`` 是新 judge 的判定。

    返回 ``{n, loosened, tightened, changed, fix_rate, loosening_suspected}``：
      * ``loosened``：原判错、新判对（**历史错点被翻成对** → 新 judge 变松）；
      * ``tightened``：原判对、新判错（新 judge 变严）；
      * ``fix_rate``：loosened / 原判错数；超 ``loosening_rate`` → ``loosening_suspected``
        （别当模型进步，先查 judge 是不是放水了）。
    """
    n = 0
    loosened = 0
    tightened = 0
    changed: list[str] = []
    was_wrong = 0

    for s in samples or []:
        n += 1
        old = bool(s.get("judge_passed"))
        if not old:
            was_wrong += 1
        try:
            new = bool(judge_fn(s))
        except Exception:       # judge 异常 → 视为未通过，留痕不崩
            new = False
        if old != new:
            changed.append(str(s.get("id", "")))
            if new and not old:
                loosened += 1
            elif old and not new:
                tightened += 1

    fix_rate = round(loosened / was_wrong, 4) if was_wrong else 0.0
    return {
        "n": n,
        "loosened": loosened,
        "tightened": tightened,
        "changed": changed,
        "fix_rate": fix_rate,
        "loosening_suspected": was_wrong > 0 and fix_rate > loosening_rate,
    }


# ── CLI（self-test：不触网，只验纯逻辑）────────────────────────

def self_test() -> int:
    ok = True

    def chk(cond: bool, msg: str) -> None:
        nonlocal ok
        ok = ok and cond
        print(f"  {'✓' if cond else '✗'} {msg}")

    a = build_spec("rubric", "v1", model="deepseek-flash", criteria={"kw": 40})
    a2 = build_spec("rubric", "v1", model="deepseek-flash", criteria={"kw": 40})
    b = build_spec("rubric", "v1", model="deepseek-flash", criteria={"kw": 50})
    chk(a.stamp == a2.stamp, "同规范 → 同戳")
    chk(a.stamp != b.stamp, "判据变 → 戳变")

    reg = JudgeRegistry()
    reg.register(a)
    reg.register(build_spec("safety", "v1"))
    snap = reg.freeze()
    chk(snap["rubric"] == a.stamp, "freeze 记录当前戳")
    chk(reg.detect_drift()["changed"] == [], "未动 → 无漂移")
    reg.register(b)
    drift = reg.detect_drift()
    chk(drift["changed"] == ["rubric"], "改了 rubric → changed")
    chk(drift["needs_recheck"] == ["rubric"], "变化 judge → needs_recheck")

    c = consensus([0.8, 0.82, 0.79])
    chk(c["agree"] and c["n"] == 3, "相近 → 达成共识")
    c2 = consensus([0.2, 0.9])
    chk(not c2["agree"], "离散大 → 不共识")

    rc = recheck_samples(
        [{"id": "s1", "judge_passed": False}, {"id": "s2", "judge_passed": False}],
        lambda s: s["id"] == "s1",
    )
    chk(rc["loosened"] == 1 and rc["fix_rate"] == 0.5, "错点翻面计入 loosened/fix_rate")

    print("SELF-TEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="judge 注册 / 冻结（P2-3）")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
