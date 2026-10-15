"""判据隔离（P2-2）—— clean-context eval + 泄漏守卫 + 基线跳升告警，全离线可测。

判据（隔离三问）：
  ① 判据**不进入** agent 可见上下文（public_view 只留 id+request；runner 只收到 request）；
  ② 泄漏**可检**（payload 里出现判据字段 / 判据专属 token → leak_scan 命中）；
  ③ 基线跳升**是红**（判据变 + 分升 → suspect_gaming，别当 improved）。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core import eval_isolation as ei  # noqa: E402


def _task(tid="t1", request="搜索今天的日期", **crit):
    return {"id": tid, "request": request, **crit}


# ── ① task 分区 + 戳 ─────────────────────────────────────────

def test_public_view_strips_criteria():
    t = _task(expected_keywords=["星期"], expected_tool_used="datetime", weight=2)
    assert ei.public_view(t) == {"id": "t1", "request": "搜索今天的日期"}
    assert "expected_keywords" not in ei.public_view(t)


def test_criteria_of_keeps_only_criteria():
    t = _task(expected_keywords=["a"], expected_tool_used="x", weight=1,
              category="search", description="d")
    c = ei.criteria_of(t)
    assert set(c) == {"expected_keywords", "expected_tool_used", "weight", "category", "description"}
    assert "request" not in c


def test_agent_request_safe():
    assert ei.agent_request(_task(request="hi")) == "hi"
    assert ei.agent_request({"id": "x"}) == ""


def test_criteria_stamp_stable_and_sensitive():
    a = _task(expected_keywords=["x"], expected_tool_used="t")
    b = _task(expected_keywords=["x"], expected_tool_used="t")
    c = _task(expected_keywords=["x", "y"], expected_tool_used="t")
    assert ei.criteria_stamp(a) == ei.criteria_stamp(b)
    assert ei.criteria_stamp(a) != ei.criteria_stamp(c)
    # 只在判据字段上算戳：request 变化不影响判据戳
    d = _task(request="完全不同的请求", expected_keywords=["x"], expected_tool_used="t")
    assert ei.criteria_stamp(a) == ei.criteria_stamp(d)


# ── ② 泄漏守卫 ───────────────────────────────────────────────

def test_leak_scan_clean_public_view():
    t = _task(expected_keywords=["星期"], expected_tool_used="datetime")
    assert ei.leak_scan(ei.public_view(t), t) == []


def test_leak_scan_detects_criteria_field():
    t = _task(expected_keywords=["星期"])
    bad = {"id": "t1", "request": "搜索", "expected_keywords": ["星期"]}
    leaks = ei.leak_scan(bad, t)
    assert any(l.startswith("field:") for l in leaks)


def test_leak_scan_detects_criteria_only_token():
    t = _task(request="帮我做点事", expected_tool_used="code_execute",
              expected_keywords=["456831"])
    bad = "帮我做点事 code_execute 456831"
    leaks = ei.leak_scan(bad, t)
    assert "token:code_execute" in leaks
    assert "token:456831" in leaks


def test_leak_scan_ignores_keyword_already_in_request():
    # 关键词本就出现在原始 request 里 → 给 agent 是正当的，不算泄漏
    t = _task(request="搜索 AI Agent 的最新进展", expected_keywords=["Agent"],
              expected_tool_used="search")
    payload = {"id": "t1", "request": "搜索 AI Agent 的最新进展"}
    assert "token:Agent" not in ei.leak_scan(payload, t)
    # 但判据专属 token（search，不在 request 里）一旦混入 payload 就必须命中
    assert "token:search" in ei.leak_scan("搜索 AI Agent 的最新进展 search", t)


def test_isolation_report():
    t = _task(expected_keywords=["星期"], expected_tool_used="datetime")
    rep = ei.isolation_report(t)
    assert rep["isolated"] is True and rep["leaked"] == []
    assert rep["criteria_stamp"] == ei.criteria_stamp(t)


# ── clean-context evaluator ──────────────────────────────────

def test_clean_context_eval_agent_sees_only_request():
    seen: list[str] = []

    def runner(request):
        seen.append(request)
        return {"final_output": "包含 星期 与 datetime 的正常输出内容", "elapsed_s": 1.0,
                "total_tokens": 100, "tool_calls": ["datetime"]}

    t = _task(expected_keywords=["星期"], expected_tool_used="datetime")
    rep = ei.clean_context_eval([t], runner)
    # runner 只拿到 request —— 判据没进 agent 上下文
    assert seen == ["搜索今天的日期"]
    assert rep["all_isolated"] is True
    assert rep["tasks"] == 1 and rep["isolated"] == 1
    assert 0.0 <= rep["baseline_mean"] <= 1.0
    assert rep["criteria_stamp"]  # 非空


def test_clean_context_eval_missing_result_fields_do_not_crash():
    rep = ei.clean_context_eval([_task()], lambda r: {})
    assert rep["tasks"] == 1
    assert isinstance(rep["baseline_mean"], float)


def test_clean_context_eval_injectable_scorer():
    rep = ei.clean_context_eval([_task()], lambda r: {}, scorer=lambda *a, **k: 100.0)
    assert rep["baseline_mean"] == 1.0
    assert rep["scores"][0]["passed"] is True


def test_default_scorer_rubric():
    t = _task(expected_keywords=["星期", "日期"], expected_tool_used="datetime")
    full = ei.default_scorer(t, "星期 日期 ok", ["datetime"], latency_s=1, total_tokens=100)
    assert full == 100.0
    none = ei.default_scorer(t, "无关内容", [], latency_s=60, total_tokens=9999)
    assert none == 0.0


# ── ③ 基线跳升告警 ───────────────────────────────────────────

def test_baseline_from_report():
    t = _task(expected_keywords=["x"], expected_tool_used="t")
    rep = ei.clean_context_eval([t], lambda r: {"final_output": "x t ok"})
    base = ei.baseline_from_report(rep)
    assert set(base) == {"score", "criteria_stamp"}


def test_alarm_no_baseline():
    assert ei.baseline_jump_alarm(None, {"score": 0.8, "criteria_stamp": "a"})["verdict"] == "no_baseline"
    assert ei.baseline_jump_alarm({"score": None}, {"score": 0.8})["verdict"] == "no_baseline"


def test_alarm_stable():
    r = ei.baseline_jump_alarm({"score": 0.80, "criteria_stamp": "a"},
                               {"score": 0.81, "criteria_stamp": "a"})
    assert r["verdict"] == "stable"


def test_alarm_improved_when_criteria_unchanged():
    r = ei.baseline_jump_alarm({"score": 0.70, "criteria_stamp": "a"},
                               {"score": 0.90, "criteria_stamp": "a"})
    assert r["verdict"] == "improved"
    assert r["criteria_changed"] is False


def test_alarm_suspect_gaming_when_criteria_changed_and_score_jumped():
    r = ei.baseline_jump_alarm({"score": 0.70, "criteria_stamp": "a"},
                               {"score": 0.90, "criteria_stamp": "b"})
    assert r["verdict"] == "suspect_gaming"
    assert r["criteria_changed"] is True
    assert r["score_delta"] > 0


def test_alarm_regressed_when_criteria_changed_and_score_dropped():
    r = ei.baseline_jump_alarm({"score": 0.90, "criteria_stamp": "a"},
                               {"score": 0.70, "criteria_stamp": "b"})
    assert r["verdict"] == "regressed"


def test_alarm_regressed_when_unchanged_and_dropped():
    r = ei.baseline_jump_alarm({"score": 0.90, "criteria_stamp": "a"},
                               {"score": 0.80, "criteria_stamp": "a"})
    assert r["verdict"] == "regressed"


# ── CLI ───────────────────────────────────────────────────────

def test_cli_suspect_gaming_exit_code(tmp_path):
    prev = tmp_path / "prev.json"
    cur = tmp_path / "cur.json"
    prev.write_text(json.dumps({"score": 0.70, "criteria_stamp": "a"}), encoding="utf-8")
    cur.write_text(json.dumps({"score": 0.92, "criteria_stamp": "b"}), encoding="utf-8")
    assert ei.main(["--prev", str(prev), "--cur", str(cur)]) == 1
    assert ei.main(["--prev", str(prev), "--cur", str(cur), "--no-fail"]) == 0
