"""判据隔离（P2-2）—— 让「评测能奖励什么」对被测 agent 不可见，并给「基线跳升」装上告警。

痛点
----
高分离不开「评测本来没打算奖励的打分机会」（reward hacking）：被测 agent 若能看到判据
（``expected_keywords`` / ``expected_tool_used``），就会对着判据刷分，而不是真完成任务；
模型也可能见过测试集（判据即泄漏）。表现是**改了判据之后基线分凭空跳升** —— 那多半不是
改进，是漏洞被喂大了。

落点（小而真，纯标准库、可离线测）
----------------------------------
1. **clean-context evaluator**：把 task 拆成两半 —— ``public_view``（只给被测 agent 看的，
   只含 ``id`` + ``request``）与 ``criteria``（只给打分器看的判据）。判据永不进入 agent 的
   可见上下文。:func:`clean_context_eval` 逐个任务照此跑。
2. **泄漏守卫**：:func:`leak_scan` 扫描**实际下发的 payload**，命中判据字段名 / 判据专属 token
   → 判为泄漏（:func:`isolation_report` 汇总）。判据里本就出现在原始 ``request`` 中的词不算
   泄漏（那是任务输入，必须给）。
3. **基线跳升告警**：:func:`baseline_jump_alarm`。判据指纹（reuse ``versioning.version_stamp``）
   变了 **且** 基线分**上升**超过 ``jump_margin`` → 判为 ``suspect_gaming``（改判据 + 分数跳升），
   而非 ``improved``（判据没变、分数升才是真改进）。

判据指纹可跨进程复现（sha256 稳定序列化，绝不用内置 ``hash()``）。

CLI::

    python -m agent_harness.core.eval_isolation --prev <baseline.json> --cur <current.json>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from agent_harness.core.versioning import version_stamp

# ── 字段分区 ──────────────────────────────────────────────────
# 允许下发给被测 agent 的字段（id 仅作簿记，request 是任务本身）。
PUBLIC_FIELDS: tuple[str, ...] = ("id", "request")

# 判据字段：只给打分器看，**任何**情况下都不得进入 agent 可见上下文。
CRITERIA_FIELDS: tuple[str, ...] = (
    "expected_keywords",
    "expected_tool_used",
    "weight",
    "category",
    "description",
)

DEFAULT_JUMP_MARGIN = 0.05   # 基线分上升超过该幅度 + 判据变 → 疑似 gaming
DEFAULT_PASS = 60.0          # 0..100，默认及格线（与 apps/research/eval 的 rubric 一致）


# ── task 分区 ─────────────────────────────────────────────────

def public_view(task: dict) -> dict:
    """被测 agent 可见的部分（``id`` + ``request``）—— **判据被剥掉**。"""
    return {k: task[k] for k in PUBLIC_FIELDS if k in task}


def criteria_of(task: dict) -> dict:
    """只给打分器看的判据子集（保序、只保留存在字段）。"""
    return {k: task[k] for k in CRITERIA_FIELDS if k in task}


def agent_request(task: dict) -> str:
    """下发给 agent 的实际文本（任务请求）；缺字段返回空串（不抛）。"""
    return str(task.get("request", "") or "")


def criteria_stamp(task_or_criteria: dict) -> str:
    """判据的稳定版本戳（sha256 前 12 位）—— 判据一变，戳就变。

    传整个 task 时只取判据字段；传判据 dict 时直接用。键排序保证无序也能复现。
    """
    if any(k in task_or_criteria for k in CRITERIA_FIELDS):
        crit = criteria_of(task_or_criteria)
    else:
        crit = dict(task_or_criteria or {})
    return version_stamp(crit)


# ── 泄漏守卫 ──────────────────────────────────────────────────

def criteria_tokens(task: dict) -> list[str]:
    """判据里「值得追查」的字符串 token（工具名 + 关键词），已去重、去空白。"""
    out: list[str] = []
    tool = task.get("expected_tool_used")
    if isinstance(tool, str) and tool.strip():
        out.append(tool.strip())
    kws = task.get("expected_keywords")
    if isinstance(kws, (list, tuple)):
        out += [str(k).strip() for k in kws if str(k).strip()]
    # 去重且保序
    seen: set[str] = set()
    uniq: list[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def leak_scan(payload: Any, task: dict) -> list[str]:
    """扫描下发给 agent 的 ``payload``，返回泄漏到的判据 token 列表（空 = 干净）。

    两类命中：
      * **结构性**：payload（dict）里出现任何判据字段名（``expected_keywords`` …）；
      * **token 级**：判据 token 出现在 payload 序列化文本里，且该 token **不在**原始
        ``request`` 中（request 里的词必须给 agent，不算泄漏）。
    """
    leaks: list[str] = []
    if isinstance(payload, dict):
        for k in CRITERIA_FIELDS:
            if k in payload:
                leaks.append(f"field:{k}")

    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    request = agent_request(task)
    for tok in criteria_tokens(task):
        if tok in request:          # 任务输入自带 → 给 agent 是正当的
            continue
        if tok and tok in text:
            leaks.append(f"token:{tok}")
    return leaks


def isolation_report(task: dict, payload: Any = None) -> dict:
    """一条任务的隔离体检。``payload`` 缺省取 :func:`public_view`（期望的干净下发体）。

    返回 ``{task_id, isolated, leaked, criteria_stamp}``。
    """
    payload = public_view(task) if payload is None else payload
    leaked = leak_scan(payload, task)
    return {
        "task_id": task.get("id", ""),
        "isolated": not leaked,
        "leaked": leaked,
        "criteria_stamp": criteria_stamp(task),
    }


# ── 默认打分（自包含 rubric，不依赖 apps）──────────────────────
# 权重与 apps/research/eval/scorer.py 对齐：关键词 40 / 工具 30 / 时延 15 / token 15。
def default_scorer(
    task: dict,
    output: str,
    tool_calls: list[str] | None = None,
    latency_s: float = 0.0,
    total_tokens: int = 0,
) -> float:
    """自包含的 0..100 打分（纯函数）—— 仅供 clean-context eval 兜底，可被注入覆盖。"""
    kws = task.get("expected_keywords") or []
    out = (output or "").lower()
    kw_score = (sum(1 for kw in kws if kw.lower() in out) / len(kws)) * 40 if kws else 40

    tool = task.get("expected_tool_used", "")
    tool_calls = tool_calls or []
    tool_score = (30 if tool in tool_calls else 0) if tool else 30

    latency_score = 15 if latency_s <= 5 else 10 if latency_s <= 15 else 5 if latency_s <= 30 else 0
    cost_score = 15 if total_tokens <= 500 else 10 if total_tokens <= 1500 else 5 if total_tokens <= 5000 else 0
    return float(kw_score + tool_score + latency_score + cost_score)


# ── clean-context evaluator ───────────────────────────────────

def clean_context_eval(
    tasks: list[dict],
    runner_func: Callable[[str], dict],
    *,
    scorer: Callable[..., float] | None = None,
    pass_score: float = DEFAULT_PASS,
) -> dict:
    """判据隔离下的评测：agent 只拿 ``request``，判据只进打分器。

    ``runner_func(request_str) -> {"final_output": str, "tool_calls": [...],
    "elapsed_s": float, "total_tokens": int}``（缺字段兜底，不抛）。
    ``scorer(task, output, tool_calls, latency_s, total_tokens) -> 0..100`` 可注入。

    返回报告：逐任务分 + 隔离体检 + 基线均值（0..1）+ 整套判据戳。
    """
    scorer = scorer or default_scorer
    scores: list[dict] = []
    leaks: list[dict] = []
    suite_criteria: list[str] = []

    for task in tasks:
        rep = isolation_report(task)          # 默认用 public_view 做体检
        suite_criteria.append(rep["criteria_stamp"])
        if rep["leaked"]:
            leaks.append({"task_id": rep["task_id"], "leaked": rep["leaked"]})

        request = agent_request(task)          # ← 只把 request 交给 agent
        result = runner_func(request) or {}
        output = str(result.get("final_output", "") or "")
        tool_calls = list(result.get("tool_calls", []) or [])
        latency_s = float(result.get("elapsed_s", 0.0) or 0.0)
        total_tokens = int(result.get("total_tokens", 0) or 0)

        score = float(scorer(task, output, tool_calls, latency_s, total_tokens))
        scores.append({
            "task_id": task.get("id", ""),
            "score": round(score, 2),
            "passed": score >= pass_score,
            "isolated": rep["isolated"],
        })

    mean = (sum(s["score"] for s in scores) / len(scores)) if scores else None
    return {
        "tasks": len(tasks),
        "isolated": sum(1 for s in scores if s["isolated"]),
        "all_isolated": not leaks,
        "leaks": leaks,
        "scores": scores,
        "baseline_mean": round(mean / 100.0, 4) if mean is not None else None,
        "criteria_stamp": version_stamp(sorted(suite_criteria)),
    }


def baseline_from_report(report: dict) -> dict:
    """从 :func:`clean_context_eval` 报告抽出基线快照（供跳升告警比对）。

    返回 ``{score, criteria_stamp}``；score 为 0..1 基线均值（缺数据 → None）。
    """
    return {
        "score": report.get("baseline_mean"),
        "criteria_stamp": report.get("criteria_stamp"),
    }


# ── 基线跳升告警（防 gaming）─────────────────────────────────

def baseline_jump_alarm(
    prev: dict | None,
    cur: dict,
    *,
    jump_margin: float = DEFAULT_JUMP_MARGIN,
    drop_margin: float = DEFAULT_JUMP_MARGIN,
) -> dict:
    """判据隔离下的基线告警 —— 改判据后基线**跳升**要怀疑 gaming 而非改进。

    ``prev`` / ``cur`` 各为 ``{score: 0..1, criteria_stamp: str}``（见
    :func:`baseline_from_report`）。返回 ``{verdict, score_delta, criteria_changed,
    reasons}``，``verdict`` ∈
    ``{no_baseline, stable, suspect_gaming, improved, regressed}``：

      * 判据变 **且** 分升 ≥ jump_margin → ``suspect_gaming``（判据变松 / 泄漏被喂大）；
      * 判据变 **且** 分掉 ≤ −drop_margin → ``regressed``（判据变严 → 需复查，别当退步）；
      * 判据**没变** 且 分升 ≥ jump_margin → ``improved``（真改进）；
      * 判据没变 且 分掉 ≤ −drop_margin → ``regressed``；
      * 其余 → ``stable``。
    """
    result: dict = {
        "verdict": "no_baseline",
        "score_delta": None,
        "criteria_changed": False,
        "jump_margin": jump_margin,
        "reasons": [],
    }
    if not prev or prev.get("score") is None or cur.get("score") is None:
        result["reasons"].append("缺少基线或当前分 → 不告警")
        return result

    criteria_changed = prev.get("criteria_stamp") != cur.get("criteria_stamp")
    delta = round(float(cur["score"]) - float(prev["score"]), 4)
    result["criteria_changed"] = criteria_changed
    result["score_delta"] = delta

    if criteria_changed and delta >= jump_margin:
        result["verdict"] = "suspect_gaming"
        result["reasons"].append(
            f"判据已变且基线分跳升 {delta:+.3f}（≥ {jump_margin}）—— 疑似 gaming："
            "先排查判据是否变松 / 泄漏是否被喂大，别当改进放行"
        )
    elif criteria_changed and delta <= -drop_margin:
        result["verdict"] = "regressed"
        result["reasons"].append(
            f"判据已变且基线分下降 {delta:+.3f}（≤ −{drop_margin}）—— 判据可能变严，需复查"
        )
    elif delta >= jump_margin:
        result["verdict"] = "improved"
        result["reasons"].append(f"判据未变、基线分上升 {delta:+.3f} —— 判为真改进")
    elif delta <= -drop_margin:
        result["verdict"] = "regressed"
        result["reasons"].append(f"判据未变、基线分下降 {delta:+.3f} —— 判为回归")
    else:
        result["verdict"] = "stable"
        result["reasons"].append(f"基线分变化 {delta:+.3f} 在阈值内 → 稳定")
    return result


# ── CLI ───────────────────────────────────────────────────────

def _load_json(path: str) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="判据隔离 · 基线跳升告警（P2-2）")
    ap.add_argument("--prev", required=True, help="基线报告 JSON（含 score/criteria_stamp）")
    ap.add_argument("--cur", required=True, help="当前报告 JSON")
    ap.add_argument("--jump-margin", type=float, default=DEFAULT_JUMP_MARGIN)
    ap.add_argument("--no-fail", action="store_true", help="疑似 gaming 也不返回非零退码")
    args = ap.parse_args(argv)

    prev = _load_json(args.prev)
    cur = _load_json(args.cur)
    if prev is None or cur is None:
        print("⚠️  报告读不到")
        return 0
    res = baseline_jump_alarm(
        baseline_from_report(prev) if "baseline_mean" in prev else prev,
        baseline_from_report(cur) if "baseline_mean" in cur else cur,
        jump_margin=args.jump_margin,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if res["verdict"] == "suspect_gaming" and not args.no_fail:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
