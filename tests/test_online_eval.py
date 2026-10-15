"""在线采样评估（P1-5）—— 抽样 + 轻量分类器 + 离线/在线落差，全离线可测。

判据（在线采样三问）：
  ① 分类器**真轻量**：纯启发式规则，不调 LLM、不触网（用造好的 jsonl 即可复现）；
  ② 抽样**确定性**：同 seed + 同日志 → 同一样本（生产抽样可复现）；
  ③ 落差**一等公民**：`offline_mean − online_mean` 直接产出，超阈值告警（缺数据不误报）。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core import online_eval as oe  # noqa: E402


# ── 辅助：造 jsonl 样本 ───────────────────────────────────────

def _write_jsonl(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


def _ev(type_: str, **data) -> dict:
    return {"ts": 1234.5, "type": type_, "data": data}


# ── ① 轻量分类器（纯规则）───────────────────────────────────

def test_classify_ok():
    label, _ = oe.classify_output("这是一段足够长的正常回复内容，描述清楚。")
    assert label == "ok"


def test_classify_error_on_failure():
    assert oe.classify_output("anything", success=False)[0] == "error"
    assert oe.classify_output("anything", errors=["boom"])[0] == "error"


def test_classify_empty():
    assert oe.classify_output("")[0] == "empty"
    assert oe.classify_output("   \n  ")[0] == "empty"


def test_classify_truncated_on_length():
    label, reasons = oe.classify_output("很长的一段但被截断了",
                                        finish_reason="length")
    assert label == "truncated"
    assert any("length" in r for r in reasons)


def test_classify_repeated_uses_prev():
    prev = "完全相同的输出"
    assert oe.classify_output(prev, prev_text=prev)[0] == "repeated"
    # 空白差异不算变化
    assert oe.classify_output("  完全相同的输出 ", prev_text=prev)[0] == "repeated"
    # 不同则不是 repeated
    assert oe.classify_output("不同的输出内容啊", prev_text=prev)[0] != "repeated"


def test_classify_short():
    assert oe.classify_output("嗯", min_len=8)[0] == "short"


def test_label_scores_ordering_and_lookup():
    # ok 最高、empty 最低；未知标签兜底 0.0
    assert oe.score_for("ok") == 1.0
    assert oe.score_for("empty") == 0.0
    assert oe.score_for("不存在") == 0.0
    assert oe.score_for("ok") > oe.score_for("error") > oe.score_for("empty")


def test_extract_text_prefers_known_fields():
    assert oe.extract_text({"output": "a", "text": "b"}) == "a"
    assert oe.extract_text({"text": "b"}) == "b"
    assert oe.extract_text({}) == ""
    assert oe.extract_text(None) == ""


# ── 事件级分类 ────────────────────────────────────────────────

def test_classify_event_skips_metadata_only():
    # 纯控制事件（无输出、无信号）不打样本
    assert oe.classify_event(_ev("worker_start", worker="search")) is None


def test_classify_event_reads_output_text():
    s = oe.classify_event(_ev("worker_end", output="一段正常长度的结果文本"))
    assert s is not None and s.label == "ok"


def test_classify_event_llm_call_zero_tokens_is_empty():
    s = oe.classify_event(_ev("llm_call", completion_tokens=0))
    assert s is not None and s.label == "empty"


def test_classify_event_llm_call_otel_token_key():
    s = oe.classify_event(_ev("llm_call", **{"gen_ai.usage.output_tokens": 0}))
    assert s is not None and s.label == "empty"


# ── run 级分类 + 无进展识别 ──────────────────────────────────

def test_classify_run_aggregates_and_flags_no_progress():
    events = [
        _ev("worker_end", output="第一段正常输出内容"),
        _ev("worker_end", output="第一段正常输出内容"),       # repeated
        _ev("no_progress", worker="search", repeats=3),        # run 级熔断
    ]
    rep = oe.classify_run("s1", events)
    assert rep["labels"].get("repeated") == 1
    assert rep["no_progress"] is True
    assert rep["labels"].get("no_progress") == 1
    # 质量分被复读 + 熔断拉低
    assert rep["score"] < 1.0


def test_classify_run_no_progress_via_tripped_field():
    rep = oe.classify_run("s1", [_ev("finalize", request="x", no_progress_tripped=True)])
    assert rep["no_progress"] is True


# ── ② 抽样确定性 ──────────────────────────────────────────────

def _make_runs(n: int) -> list[tuple[str, list[dict]]]:
    return [(f"s{i}", [_ev("worker_end", output=f"输出内容 {i} 正常长度足够")])
            for i in range(n)]


def test_sample_runs_rate_one_keeps_all():
    runs = _make_runs(5)
    assert len(oe.sample_runs(runs, rate=1.0, seed=0)) == 5


def test_sample_runs_deterministic_by_seed():
    runs = _make_runs(20)
    a = oe.sample_runs(runs, rate=0.5, seed=42)
    b = oe.sample_runs(runs, rate=0.5, seed=42)
    assert [sid for sid, _ in a] == [sid for sid, _ in b]
    # 不同 seed 通常不同（不锁死，只要有区分度即可；这里明确断言至少一种 seed 有差异）
    c = oe.sample_runs(runs, rate=0.5, seed=7)
    assert [sid for sid, _ in a] != [sid for sid, _ in c]


def test_sample_runs_limit():
    runs = _make_runs(10)
    assert len(oe.sample_runs(runs, rate=1.0, limit=3)) == 3


# ── 目录扫描 + evaluate_online ───────────────────────────────

def test_iter_runs_sorted_and_reads(tmp_path):
    _write_jsonl(tmp_path / "b.jsonl", [_ev("worker_end", output="b 输出内容正常")])
    _write_jsonl(tmp_path / "a.jsonl", [_ev("worker_end", output="a 输出内容正常")])
    runs = oe.iter_runs(tmp_path)
    assert [sid for sid, _ in runs] == ["a", "b"]  # 排序 = 抽样可复现前提


def test_iter_runs_missing_dir_is_empty(tmp_path):
    assert oe.iter_runs(tmp_path / "nope") == []


def test_load_jsonl_skips_bad_lines(tmp_path):
    p = tmp_path / "x.jsonl"
    p.write_text('{"type":"a","data":{}}\nnot json\n\n{"type":"b","data":{}}\n',
                 encoding="utf-8")
    events = oe.load_jsonl(p)
    assert len(events) == 2


def test_evaluate_online_end_to_end(tmp_path):
    _write_jsonl(tmp_path / "good.jsonl", [_ev("worker_end", output="一段正常长度的结果内容")])
    _write_jsonl(tmp_path / "bad.jsonl", [_ev("worker_end", output="")])   # empty
    _write_jsonl(tmp_path / "err.jsonl", [_ev("worker_end", success=False, output="x")])
    report = oe.evaluate_online(tmp_path, seed=0)
    assert report["runs_total"] == 3
    assert report["runs_sampled"] == 3
    assert report["samples"] == 3
    assert 0.0 <= report["online_mean"] <= 1.0
    assert report["labels"]["empty"] == 1
    assert report["labels"]["error"] == 1


def test_evaluate_online_empty_dir_no_crash(tmp_path):
    report = oe.evaluate_online(tmp_path)
    assert report["samples"] == 0
    assert report["online_mean"] is None


# ── ③ 落差指标 ────────────────────────────────────────────────

def test_offline_online_gap_regressed():
    gap = oe.offline_online_gap(0.9, 0.6, threshold=0.1)
    assert gap["gap"] == pytest.approx(0.3)
    assert gap["regressed"] is True
    assert gap["improved"] is False


def test_offline_online_gap_improved():
    gap = oe.offline_online_gap(0.6, 0.9, threshold=0.1)
    assert gap["gap"] == pytest.approx(-0.3)
    assert gap["regressed"] is False
    assert gap["improved"] is True


def test_offline_online_gap_within_threshold():
    gap = oe.offline_online_gap(0.8, 0.75, threshold=0.1)
    assert gap["regressed"] is False and gap["improved"] is False


def test_offline_online_gap_none_safe():
    for a, b in ((None, 0.5), (0.5, None), (None, None)):
        gap = oe.offline_online_gap(a, b)
        assert gap["gap"] is None
        assert gap["regressed"] is False


def test_load_offline_mean_normalizes_0_100(tmp_path):
    p = tmp_path / "report.json"
    p.write_text(json.dumps({"avg_score": 82.0}), encoding="utf-8")
    assert oe.load_offline_mean(p) == pytest.approx(0.82)


def test_load_offline_mean_already_unit(tmp_path):
    p = tmp_path / "report.json"
    p.write_text(json.dumps({"mean": 0.7}), encoding="utf-8")
    assert oe.load_offline_mean(p) == pytest.approx(0.7)


def test_load_offline_mean_missing_or_bad(tmp_path):
    assert oe.load_offline_mean(tmp_path / "nope.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert oe.load_offline_mean(bad) is None


def test_to_metrics_keys():
    report = {"runs_sampled": 2, "samples": 5, "online_mean": 0.6}
    gap = oe.offline_online_gap(0.9, 0.6, threshold=0.1)
    m = oe.to_metrics(report, gap)
    assert m["harness_online_eval_quality_mean"] == pytest.approx(0.6)
    assert m["harness_offline_online_gap"] == pytest.approx(0.3)
    assert m["harness_online_eval_samples_total"] == 5.0


# ── CLI ───────────────────────────────────────────────────────

def test_cli_json_reports_gap(tmp_path, capsys):
    _write_jsonl(tmp_path / "good.jsonl", [_ev("worker_end", output="一段正常长度的结果内容")])
    code = oe.main(["--log-dir", str(tmp_path), "--offline-mean", "0.9",
                    "--threshold", "0.05", "--json", "--no-fail"])
    out = json.loads(capsys.readouterr().out)
    assert out["gap"]["offline_mean"] == pytest.approx(0.9)
    assert code == 0


def test_cli_alerts_on_regression(tmp_path, capsys):
    # 全空输出 → online_mean=0 → gap=0.9 → 超阈值 → 退出码 1
    _write_jsonl(tmp_path / "bad.jsonl", [_ev("worker_end", output="")])
    code = oe.main(["--log-dir", str(tmp_path), "--offline-mean", "0.9", "--json"])
    capsys.readouterr()
    assert code == 1


def test_cli_offline_eval_file(tmp_path, capsys):
    _write_jsonl(tmp_path / "good.jsonl", [_ev("worker_end", output="一段正常长度的结果内容")])
    rep = tmp_path / "offline.json"
    rep.write_text(json.dumps({"avg_score": 50.0}), encoding="utf-8")
    code = oe.main(["--log-dir", str(tmp_path), "--offline-eval", str(rep),
                    "--json", "--no-fail"])
    out = json.loads(capsys.readouterr().out)
    assert out["gap"]["offline_mean"] == pytest.approx(0.5)
    assert code == 0
