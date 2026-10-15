"""trace 回放回归（P2-1）—— 冻结真实轨迹成用例，回放断言新代码行为，全离线可测。

判据（回放回归三问）：
  ① 冻结物**确定性**：同一 run → 同一 fixture（不含 ts 等易变量，可 diff/入库）；
  ② cut-point **只 live 跑变化段**：cut_point 之前的步用录制输出回放；
  ③ **拿旧轨迹断言新代码**：`assert_trace_matches` 不匹配即抛，差异可归因。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core import trace_replay as tr  # noqa: E402


def _ev(type_: str, **data) -> dict:
    return {"ts": 999.0, "type": type_, "data": data}


# ── ① 冻结：确定性 + 标准化 ──────────────────────────────────

def test_freeze_from_events_normalizes_and_is_order_stable():
    events = [
        _ev("worker_end", worker="search", task="找资料", output="结果一", success=True),
        _ev("llm_call", prompt_hash="abc", output="模型输出", completion_tokens=5),
    ]
    trace = tr.freeze_from_events(events, name="t1")
    assert len(trace) == 2
    assert trace.steps[0].kind == "worker_end"
    assert trace.steps[0].input["worker"] == "search"
    assert trace.steps[0].output["output"] == "结果一"
    assert trace.steps[1].output["output"] == "模型输出"
    assert trace.steps[1].input["prompt_hash"] == "abc"
    assert trace.steps[1].output["completion_tokens"] == 5


def test_freeze_drops_volatile_ts():
    trace = tr.freeze_from_events([_ev("worker_end", output="x")], name="t")
    blob = json.dumps(trace.to_dict(), ensure_ascii=False)
    assert "999.0" not in blob and '"ts"' not in blob


def test_freeze_save_load_roundtrip(tmp_path):
    trace = tr.freeze_from_events(
        [_ev("worker_end", output="结果"), _ev("finalize", final_output="终稿")],
        name="rt", meta={"rounds": 2})
    p = trace.save(tmp_path / "sub" / "trace.json")
    loaded = tr.FrozenTrace.load(p)
    assert loaded.to_dict() == trace.to_dict()


def test_freeze_from_log(tmp_path):
    p = tmp_path / "sess.jsonl"
    p.write_text(
        json.dumps(_ev("worker_end", output="a")) + "\n"
        + json.dumps(_ev("finalize", final_output="b")) + "\n",
        encoding="utf-8")
    trace = tr.freeze_from_log(p)
    assert trace.name == "sess"
    assert len(trace) == 2


def test_freeze_from_run_result_workers_sorted_plus_finalize():
    result = {
        "request": "调研 x",
        "worker_results": {
            "search": {"success": True, "output": "搜索结论", "errors": [], "elapsed_s": 1.2},
            "analyze": {"success": False, "output": "", "errors": ["boom"], "elapsed_s": 0.1},
        },
        "worker_tasks": {"search": "找资料", "analyze": "分析"},
        "final_output": "报告正文",
        "rounds": 2,
        "trace_id": "tid",
        "no_progress_tripped": False,
    }
    trace = tr.freeze_from_run_result(result, name="run1")
    kinds = [s.kind for s in trace.steps]
    assert kinds == ["worker", "worker", "finalize"]
    # worker 按名字排序 → analyze 在前
    assert trace.steps[0].input["worker"] == "analyze"
    assert trace.steps[1].input["worker"] == "search"
    assert trace.steps[1].input["task"] == "找资料"
    assert trace.steps[-1].output["final_output"] == "报告正文"
    assert trace.meta["rounds"] == 2


# ── ② + ③ 回放：回放 / cut-point / 断言 ──────────────────────

def _trace():
    return tr.freeze_from_events([
        _ev("worker_end", worker="a", output="A"),
        _ev("worker_end", worker="b", output="B"),
        _ev("finalize", final_output="F"),
    ], name="t")


def test_replay_without_handler_is_pure_replay():
    trace = _trace()
    report = tr.replay(trace, None)
    assert report.total == 3
    assert report.matched_all
    assert all(not r.live for r in report.results)


def test_replay_identity_handler_matches():
    trace = _trace()
    report = tr.replay(trace, lambda step: step.output)
    assert report.matched_all
    assert all(r.live for r in report.results)


def test_replay_detects_behaviour_change():
    trace = _trace()
    # handler 只改 finalize 输出 → 1 处不匹配
    def handler(step):
        out = dict(step.output or {})
        if step.kind == "finalize":
            out["final_output"] = "改过了"
        return out

    report = tr.replay(trace, handler)
    assert report.mismatched == 1
    assert report.mismatches[0].kind == "finalize"


def test_replay_cut_point_only_live_runs_suffix():
    trace = _trace()
    seen: list[int] = []

    def handler(step):
        seen.append(step.index)
        return step.output  # 不改变 → 全匹配

    report = tr.replay(trace, handler, cut_point=2)
    # 只有 index>=2 的步 live
    assert [r.live for r in report.results] == [False, False, True]
    assert seen == [2]
    assert report.matched_all


def test_assert_trace_matches_passes_when_unchanged():
    trace = _trace()
    rep = tr.assert_trace_matches(trace, lambda step: step.output)
    assert rep.matched_all


def test_assert_trace_matches_raises_on_regression():
    trace = _trace()

    def handler(step):
        out = dict(step.output or {})
        out["touched"] = True
        return out

    with pytest.raises(AssertionError) as ei:
        tr.assert_trace_matches(trace, handler)
    assert "轨迹回放回归" in str(ei.value)


def test_assert_trace_matches_ignore_kinds():
    trace = _trace()

    def handler(step):
        out = dict(step.output or {})
        if step.kind == "finalize":
            out["final_output"] = "变了"
        return out

    # 忽略 finalize → 不再抛
    rep = tr.assert_trace_matches(trace, handler, ignore_kinds={"finalize"})
    assert rep.mismatched == 1  # report 仍如实记录，只是不断言


def test_regression_report_shape():
    trace = _trace()
    rep = tr.regression_report(trace, lambda step: {"x": 1})
    assert rep["total"] == 3
    assert rep["mismatched"] == 3
    assert rep["matched_all"] is False
    assert len(rep["mismatches"]) == 3


def test_frozen_trace_is_json_serialisable_and_stable():
    trace = _trace()
    a = json.dumps(trace.to_dict(), sort_keys=True, ensure_ascii=False)
    b = json.dumps(_trace().to_dict(), sort_keys=True, ensure_ascii=False)
    assert a == b  # 同一输入 → 同一冻结物
