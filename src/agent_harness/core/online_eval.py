"""在线采样评估（P1-5）—— 把「离线均值 − 生产均值」做成一等公民监控指标。

痛点
----
离线 eval 是**快照**（一批固定任务 + 慢 judge），生产是**河流**（真实流量千奇百怪）。
两者之间必然有落差，但落差本身没人量 —— 只有等离线评测全绿、线上却持续出问题时才
被动发现。修法不是再训一个 judge（慢、贵、不能挂在每次请求上），而是：

  1. **抽样**：从 run 日志（``agent_logs/*.jsonl``，见 ``core.agent_log``）里按比例抽；
  2. **毫秒级轻量分类器**：纯启发式规则（空输出 / 异常 / 重复 / 截断 / 无进展 / 短回复）
     给每条 trace 打质量标签 —— **不调 LLM、不触网**，每次请求都能跑；
  3. **落差指标**：``offline_mean − online_mean``（本模块 :func:`offline_online_gap`）。

设计取舍
--------
* 分类器是纯函数（:func:`classify_output`），只做字符串/字段判据，便于离线单测。
* 采样确定性：同一个 ``seed`` + 同一批日志 → 同一份样本（生产抽样可复现）。
* 缺字段一律兜底、绝不抛异常（观测层故障不得影响主流程，与 metrics/reproducibility 同原则）。
* 归一化到 0..1：离线 eval 的 ``avg_score`` 是 0..100，读入时按需 /100，与在线分可比。

本模块只依赖标准库；可离线用造好的 jsonl 跑（见 ``tests/test_online_eval.py``）。

CLI::

    python -m agent_harness.core.online_eval --log-dir <dir> --offline-eval <report.json>
    python -m agent_harness.core.online_eval --log-dir <dir> --offline-mean 0.82 --json
"""
from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

# ── 质量标签（启发式，非 LLM judge）────────────────────────────
# 顺序即"严重度"由低到高（分数越低越差），仅用于人类阅读；分类判断用 _RULES 顺序。
LABELS: tuple[str, ...] = (
    "ok",          # 正常
    "short",       # 短回复（疑似敷衍 / 被截短）
    "truncated",   # finish_reason == length，被 max_tokens 截断
    "repeated",    # 与上一条输出完全相同（空转 / 复读）
    "no_progress", # run 级：无进展熔断（连续同 tool+同参）
    "error",       # success=False / 带 errors 字段
    "empty",       # 输出为空
)

# 每个标签对应的质量分（0..1）。ok=1，empty=0，可加权求均值。
LABEL_SCORES: dict[str, float] = {
    "ok": 1.0,
    "short": 0.6,
    "truncated": 0.4,
    "repeated": 0.3,
    "no_progress": 0.2,
    "error": 0.1,
    "empty": 0.0,
}

DEFAULT_MIN_LEN = 8        # 短于该长度（去空白后）判为 short
TEXT_FIELDS = (            # 从事件 data 里按序找候选输出文本
    "output", "final_output", "text", "content",
    "response", "answer", "message", "completion",
)
_META_ONLY = object()      # 哨兵：无文本、也无可判断信号 → 不打样本


def score_for(label: str) -> float:
    """标签 → 质量分（未知标签按 0.0 兜底，绝不抛）。"""
    return LABEL_SCORES.get(label, 0.0)


# ── 单条输出分类（纯函数）─────────────────────────────────────

def extract_text(data: dict | None) -> str:
    """从事件 data 里取候选输出文本；取不到返回空串（不抛）。"""
    if not isinstance(data, dict):
        return ""
    for key in TEXT_FIELDS:
        val = data.get(key)
        if isinstance(val, str):
            return val
    return ""


def _errors_of(data: dict) -> list:
    errs = data.get("errors")
    if isinstance(errs, list) and errs:
        return errs
    single = data.get("error")
    if single:
        return [single]
    return []


def classify_output(
    text: str,
    *,
    finish_reason: str | None = None,
    success: bool | None = None,
    errors: list | None = None,
    completion_tokens: int | None = None,
    prev_text: str | None = None,
    min_len: int = DEFAULT_MIN_LEN,
) -> tuple[str, list[str]]:
    """对**一条输出**打质量标签。返回 ``(label, reasons)``。

    规则（按序，先命中先返回）：
      1. ``success is False`` 或 ``errors`` 非空 → ``error``；
      2. ``text`` 去空白为空，且 ``completion_tokens`` 为 0 或未知 → ``empty``；
      3. ``finish_reason == "length"`` → ``truncated``（被 max_tokens 截断）；
      4. 与 ``prev_text`` 去空白后相同 → ``repeated``（复读 / 空转）；
      5. 去空白后长度 < ``min_len`` → ``short``；
      6. 否则 → ``ok``。

    纯字符串/字段判据，无网络、无 LLM、无全局状态 —— 毫秒级、可复现。
    """
    reasons: list[str] = []

    if success is False or (errors or []):
        reasons.append("success=False/errors 非空")
        return "error", reasons

    stripped = (text or "").strip()

    if not stripped:
        if completion_tokens in (0, None):
            reasons.append("输出为空")
            return "empty", reasons
        reasons.append("输出为空")
        return "empty", reasons

    if finish_reason == "length":
        reasons.append("finish_reason=length（max_tokens 截断）")
        return "truncated", reasons

    if prev_text is not None and stripped == (prev_text or "").strip():
        reasons.append("与上一条输出完全相同")
        return "repeated", reasons

    if len(stripped) < min_len:
        reasons.append(f"回复过短（{len(stripped)} < {min_len}）")
        return "short", reasons

    return "ok", reasons


# ── 单个样本 ──────────────────────────────────────────────────

@dataclass
class Sample:
    """一条打了标签的在线样本。"""
    session_id: str
    event_type: str
    label: str
    score: float
    reasons: list[str] = field(default_factory=list)
    text: str = ""

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "event_type": self.event_type,
            "label": self.label,
            "score": self.score,
            "reasons": list(self.reasons),
            "text_preview": self.text[:200],
        }


# ── 事件 → 样本 ───────────────────────────────────────────────

def classify_event(event: dict, session_id: str = "",
                   prev_text: str | None = None,
                   *, min_len: int = DEFAULT_MIN_LEN) -> Sample | None:
    """把一个日志事件分类成样本；不可判（无文本、无信号）返回 ``None``。

    兼容 ``core.agent_log`` 的事件形状 ``{"ts", "type", "data"}``：
      * ``data`` 里若有输出文本字段（output/final_output/text…）→ 分类文本；
      * ``llm_call`` 只有 usage 元数据时 → 用 ``finish_reason`` /
        ``completion_tokens``（含 OTel ``gen_ai.usage.output_tokens``）判空/截断；
      * ``success`` / ``errors`` 字段 → 判异常；
      * 其余（search / worker_start 等无输出事件）→ 返回 ``None``（不打样本，避免噪声）。
    """
    if not isinstance(event, dict):
        return None
    data = event.get("data")
    data = data if isinstance(data, dict) else {}
    event_type = str(event.get("type") or "")

    text = extract_text(data)
    has_text_field = any(isinstance(data.get(k), str) for k in TEXT_FIELDS)
    errors = _errors_of(data)
    success = data.get("success")
    if success is not None:
        success = bool(success)
    finish_reason = data.get("finish_reason")
    completion_tokens = data.get("completion_tokens",
                                 data.get("gen_ai.usage.output_tokens"))

    # 「存在文本字段」也算信号 —— 空输出（output=""）本身就是最该被抓的质量问题。
    has_signal = has_text_field or bool(errors) or success is False \
        or finish_reason is not None or completion_tokens is not None

    if not has_signal:
        # 纯控制/元数据事件（success 为 True 且无输出、无 token 信号）→ 不打样本，避免噪声。
        return None

    label, reasons = classify_output(
        text, finish_reason=finish_reason, success=success, errors=errors,
        completion_tokens=completion_tokens, prev_text=prev_text, min_len=min_len,
    )
    return Sample(session_id=session_id, event_type=event_type or "event",
                  label=label, score=score_for(label), reasons=reasons, text=text)


def classify_run(session_id: str, events: list[dict],
                 *, min_len: int = DEFAULT_MIN_LEN) -> dict:
    """对一个 run（同一会话的事件序列）打标签，返回 run 级汇总 dict。

    run 级额外识别**无进展熔断**：事件 type 含 ``no_progress``，或 data 里
    ``no_progress_tripped`` 为真 → 追加一条 run 级 ``no_progress`` 样本（分 0.2），
    让空转在在线均值里被真实反映。
    """
    samples: list[Sample] = []
    prev_text: str | None = None
    no_progress = False

    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        data = ev.get("data")
        data = data if isinstance(data, dict) else {}
        etype = str(ev.get("type") or "")
        if "no_progress" in etype or data.get("no_progress_tripped"):
            no_progress = True
        s = classify_event(ev, session_id, prev_text, min_len=min_len)
        if s is not None:
            samples.append(s)
            if s.text.strip():
                prev_text = s.text

    if no_progress:
        samples.append(Sample(
            session_id=session_id, event_type="run", label="no_progress",
            score=score_for("no_progress"),
            reasons=["run 级无进展熔断（连续同 tool+同参）"], text="",
        ))

    labels: dict[str, int] = {}
    for s in samples:
        labels[s.label] = labels.get(s.label, 0) + 1
    run_score = (sum(s.score for s in samples) / len(samples)) if samples else 0.0

    return {
        "session_id": session_id,
        "n_events": len(events or []),
        "n_samples": len(samples),
        "score": round(run_score, 4),
        "labels": labels,
        "no_progress": no_progress,
        "samples": samples,
    }


# ── 日志读取 + 抽样 ───────────────────────────────────────────

def load_jsonl(path: str | Path) -> list[dict]:
    """读一个 jsonl 文件为事件列表；坏行/缺文件一律跳过（不抛）。"""
    events: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        return []
    return events


def iter_runs(log_dir: str | Path, *, pattern: str = "*.jsonl") -> list[tuple[str, list[dict]]]:
    """扫描日志目录下所有 run（每个 jsonl = 一个 run），按文件名排序返回。

    排序是**抽样可复现**的前提：同一目录 + 同 seed → 同一份样本。
    """
    runs: list[tuple[str, list[dict]]] = []
    base = Path(log_dir)
    if not base.is_dir():
        return runs
    for f in sorted(base.glob(pattern)):
        events = load_jsonl(f)
        if events:
            runs.append((f.stem, events))
    return runs


def sample_runs(runs: list[tuple[str, list[dict]]], *, rate: float = 1.0,
                limit: int | None = None, seed: int = 0) -> list[tuple[str, list[dict]]]:
    """确定性抽样 run。

    * ``rate >= 1`` → 全取；否则每个 run 用 ``random.Random(seed)`` 决定去留；
    * ``limit`` → 取前 N 个（在抽样之后）。

    同一 ``(runs, rate, seed)`` 恒得同一结果（run 顺序已排序）。
    """
    if rate >= 1:
        kept = list(runs)
    else:
        rng = random.Random(seed)
        kept = [r for r in runs if rng.random() < rate]
    if limit is not None:
        kept = kept[:max(0, int(limit))]
    return kept


# ── 在线评估 + 落差指标 ───────────────────────────────────────

def evaluate_online(log_dir: str | Path, *, rate: float = 1.0,
                    limit: int | None = None, seed: int = 0,
                    min_len: int = DEFAULT_MIN_LEN) -> dict:
    """抽样 + 分类，产出在线质量摘要。返回 dict（见键名，全部可 JSON 化）。

    关键输出：
      * ``online_mean`` —— 样本质量分均值（0..1），本模块的"生产均值"；
      * ``labels`` —— 标签计数；
      * ``runs`` —— 每个 run 的摘要（含 sample 明细）。
    """
    all_runs = iter_runs(log_dir)
    runs = sample_runs(all_runs, rate=rate, limit=limit, seed=seed)

    run_reports: list[dict] = []
    samples: list[Sample] = []
    labels: dict[str, int] = {}
    for sid, events in runs:
        rep = classify_run(sid, events, min_len=min_len)
        run_reports.append(rep)
        for s in rep["samples"]:
            samples.append(s)
            labels[s.label] = labels.get(s.label, 0) + 1

    online_mean = (sum(s.score for s in samples) / len(samples)) if samples else None

    return {
        "runs_total": len(all_runs),
        "runs_sampled": len(runs),
        "samples": len(samples),
        "online_mean": round(online_mean, 4) if online_mean is not None else None,
        "labels": dict(sorted(labels.items())),
        "runs": [
            {k: v for k, v in r.items() if k != "samples"}
            for r in run_reports
        ],
        "sample_details": [s.to_dict() for s in samples],
    }


def offline_online_gap(offline_mean: float | None, online_mean: float | None,
                       *, threshold: float = 0.1) -> dict:
    """离线均值 − 在线均值（**一等公民指标**）。

    ``gap > 0`` 表示生产比离线差（离线高估）；``gap > threshold`` 记为 ``regressed``
    （离线评测"漂绿"了 —— 该告警）。任一均值缺失 → ``gap=None``、``regressed=False``
    （缺数据不误报）。
    """
    result: dict = {
        "offline_mean": offline_mean,
        "online_mean": online_mean,
        "gap": None,
        "threshold": threshold,
        "regressed": False,
        "improved": False,
    }
    if offline_mean is None or online_mean is None:
        return result
    gap = round(offline_mean - online_mean, 4)
    result["gap"] = gap
    result["regressed"] = gap > threshold
    result["improved"] = gap < -threshold
    return result


def load_offline_mean(path: str | Path) -> float | None:
    """从离线 eval 报告 JSON 里读均值并归一化到 0..1。

    兼容 ``apps/research/eval`` 的 ``avg_score``（0..100）、以及 ``mean`` /
    ``offline_mean`` / ``score`` 等常见键。值 > 1 且 <= 100 时按 /100 归一。
    读不到/解析失败返回 ``None``（不抛）。
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    for key in ("avg_score", "offline_mean", "mean", "score", "quality_mean"):
        val = data.get(key)
        if isinstance(val, (int, float)):
            val = float(val)
            return round(val / 100.0, 4) if 1.0 < val <= 100.0 else round(val, 4)
    return None


def to_metrics(report: dict, gap: dict) -> dict:
    """把报告 + 落差翻译成监控用的 gauge（Prometheus 风格键名）。"""
    m: dict[str, float] = {
        "harness_online_eval_runs_total": float(report.get("runs_sampled", 0)),
        "harness_online_eval_samples_total": float(report.get("samples", 0)),
    }
    if report.get("online_mean") is not None:
        m["harness_online_eval_quality_mean"] = float(report["online_mean"])
    if gap.get("offline_mean") is not None:
        m["harness_online_eval_offline_mean"] = float(gap["offline_mean"])
    if gap.get("gap") is not None:
        m["harness_offline_online_gap"] = float(gap["gap"])
    return m


# ── 渲染 ──────────────────────────────────────────────────────

def format_report(report: dict, gap: dict) -> str:
    lines = [
        "── 在线采样评估（P1-5）──",
        f"run 抽样: {report.get('runs_sampled')}/{report.get('runs_total')}",
        f"样本数: {report.get('samples')}",
        f"在线质量均值: {report.get('online_mean')}",
        f"标签分布: {report.get('labels')}",
        "── 离线 − 在线 落差 ──",
        f"离线均值: {gap.get('offline_mean')}  在线均值: {gap.get('online_mean')}",
        f"落差(offline−online): {gap.get('gap')}  (阈值 {gap.get('threshold')})",
    ]
    if gap.get("regressed"):
        lines.append("⚠️  离线高估生产质量（gap 超阈值）—— 离线评测可能已漂绿")
    elif gap.get("improved"):
        lines.append("ℹ️  生产优于离线（gap 为负且超阈值）")
    else:
        lines.append("✅ 落差在阈值内")
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────

def _default_log_dir() -> str:
    """默认日志目录 = ``core.agent_log`` 的运行期数据根（懒导入，避免 import 期绑定）。"""
    from . import agent_log
    return str(agent_log.LOG_DIR)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="在线采样评估（P1-5）")
    ap.add_argument("--log-dir", default=None, help="agent_logs 目录（默认取运行期数据根）")
    ap.add_argument("--rate", type=float, default=1.0, help="run 抽样比例（默认全取）")
    ap.add_argument("--limit", type=int, default=None, help="最多取 N 个 run")
    ap.add_argument("--seed", type=int, default=0, help="抽样随机种子（确定性）")
    ap.add_argument("--min-len", type=int, default=DEFAULT_MIN_LEN, help="短回复阈值")
    ap.add_argument("--offline-mean", type=float, default=None, help="离线均值（0..1）")
    ap.add_argument("--offline-eval", default=None, help="离线 eval 报告 JSON 路径")
    ap.add_argument("--threshold", type=float, default=0.1, help="落差告警阈值")
    ap.add_argument("--json", action="store_true", help="以 JSON 打印")
    ap.add_argument("--no-fail", action="store_true", help="有落差也不返回非零退出码")
    args = ap.parse_args(argv)

    log_dir = args.log_dir or _default_log_dir()
    report = evaluate_online(log_dir, rate=args.rate, limit=args.limit,
                             seed=args.seed, min_len=args.min_len)

    offline_mean = args.offline_mean
    if offline_mean is None and args.offline_eval:
        offline_mean = load_offline_mean(args.offline_eval)
    gap = offline_online_gap(offline_mean, report["online_mean"], threshold=args.threshold)

    if args.json:
        payload = {"report": report, "gap": gap, "metrics": to_metrics(report, gap)}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_report(report, gap))

    if gap.get("regressed") and not args.no_fail:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
