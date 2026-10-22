"""证据链 demo 脚本的回归锁（scripts/demo_research.py）。

为什么要测一个 CLI 脚本：README 首屏承诺「每条结论都带可回溯的来源（工具名 + URL +
抓取时间），核不到的显式标注」——这句话的可信度全押在这条命令上。脚本的三种退出码
是契约的一部分（0 有证据 / 2 无证据告警 / 1 失败），回归时不能让「无证据」悄悄变成「成功」。
"""
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load_demo():
    spec = importlib.util.spec_from_file_location("demo_research", REPO / "scripts" / "demo_research.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def demo():
    return _load_demo()


def _fake_result(with_provenance=True, content="营收 987 亿元 [1]"):
    provenance = []
    if with_provenance:
        provenance = [
            {"url": "https://stats.gov.cn/a", "tool": "search", "fetched_at": "2026-10-22T19:00:00",
             "title": "统计局公告", "snippet": "营收 987 亿元", "source_worker": "searcher", "status": "unknown"},
            {"url": "https://stats.gov.cn/a", "tool": "search", "fetched_at": "2026-10-22T19:00:05",
             "title": "统计局公告（重复 URL）", "snippet": "重复", "source_worker": "searcher", "status": "unknown"},
            {"url": "https://example.com/b", "tool": "web_scrape", "fetched_at": "2026-10-22T19:00:09",
             "title": "示例", "snippet": "s", "source_worker": "analyst", "status": "unknown"},
        ]
    return {
        "final_output": content,
        "provenance": provenance,
        "factcheck": {"checked": True, "not_found": [], "provenance_count": len(provenance)},
        "elapsed_s": 12.5,
        "rounds": 2,
        "worker_results": {"searcher": {}, "analyst": {}},
    }


def _patch_agent(monkeypatch, demo, result):
    from agent_harness.core.graph import graph_multi
    monkeypatch.setattr(graph_multi, "run_multi_agent", lambda req: result)


def test_exit_zero_and_report_contains_sources(demo, monkeypatch, tmp_path):
    """有 provenance → 退出码 0，HTML 落盘且真的带来源 URL 与核验块。"""
    _patch_agent(monkeypatch, demo, _fake_result())
    out = tmp_path / "r.html"
    assert demo.main(["测试问题", "--out", str(out)]) == 0
    html = out.read_text(encoding="utf-8")
    assert "stats.gov.cn" in html
    assert "事实核验" in html, "报告里没有核验说明块 —— 证据链没渲染出来"


def test_dedupes_sources_by_url(demo, monkeypatch, tmp_path):
    _patch_agent(monkeypatch, demo, _fake_result())
    out = tmp_path / "r.html"
    demo.main(["测试问题", "--out", str(out)])
    html = out.read_text(encoding="utf-8")
    # 同一 URL 出现两次（provenance 里有两条）→ 引用列表只应列一次
    assert _load_demo()._dedupe_sources(_fake_result()["provenance"])[0]["url"] == "https://stats.gov.cn/a"
    assert len(_load_demo()._dedupe_sources(_fake_result()["provenance"])) == 2


def test_zero_provenance_alerts_not_succeeds(demo, monkeypatch, tmp_path):
    """无来源不能算成功 —— 报告可读但不可核对，按契约退出码 2。"""
    _patch_agent(monkeypatch, demo, _fake_result(with_provenance=False))
    out = tmp_path / "r.html"
    assert demo.main(["测试问题", "--out", str(out)]) == 2
    assert out.exists(), "即使告警，报告仍应落盘供人工判断"


def test_empty_report_is_error_and_writes_nothing(demo, monkeypatch, tmp_path):
    """LLM 不可用/空报告 → 退出码 1，且不生成任何文件（不落半截垃圾）。"""
    _patch_agent(monkeypatch, demo, {"final_output": "", "provenance": [], "factcheck": {}})
    out = tmp_path / "r.html"
    assert demo.main(["测试问题", "--out", str(out)]) == 1
    assert not out.exists()


def test_llm_unreachable_placeholder_is_error(demo, monkeypatch, tmp_path):
    _patch_agent(monkeypatch, demo, {"final_output": "[无法生成回复] 搜索到了一些信息但未能整理成回复，请重试。",
                                     "provenance": [], "factcheck": {}})
    out = tmp_path / "r2.html"
    assert demo.main(["q", "--out", str(out)]) == 1
    assert not out.exists()


def test_exception_becomes_exit_code_1(demo, monkeypatch, tmp_path):
    from agent_harness.core.graph import graph_multi

    def _boom(req):
        raise RuntimeError("backend down")

    monkeypatch.setattr(graph_multi, "run_multi_agent", _boom)
    assert demo.main(["q", "--out", str(tmp_path / "x.html")]) == 1


def test_json_mode_emits_machine_readable_summary(demo, monkeypatch, tmp_path, capsys):
    _patch_agent(monkeypatch, demo, _fake_result())
    demo.main(["测试问题", "--out", str(tmp_path / "r.html"), "--json"])
    payload = [l for l in capsys.readouterr().out.splitlines() if l.startswith("{")]
    assert payload, "没有输出 JSON 摘要"
    data = json.loads(payload[-1])
    assert data["summary"]["unique_urls"] == 2
    assert data["summary"]["factcheck_checked"] is True


def test_finalize_passes_factcheck_and_provenance_upstream():
    """graph_multi.supervisor_finalize 必须把结构化核验结果与 provenance 交出去。

    这条是 demo 能显示证据的前提：此前只把「未核验条数」塞进 trace_steps，
    原始 provenance 被丢弃 → 报告溯源块永远空。用源码级断言锁住返回值字段，
    避免有人把它改回「只留 trace」。
    """
    src = (REPO / "src/agent_harness/core/graph/graph_multi.py").read_text(encoding="utf-8")
    head = src.split("def supervisor_finalize", 1)[1].split("# ─── Build multi-agent graph", 1)[0]
    assert '"factcheck": fact_info' in head, "finalize 没有返回结构化 factcheck"
    assert '"provenance": _collect_provenance(state)' in head, "finalize 没有返回 provenance"
    assert '"factcheck": final.get("factcheck"' in src, "run_multi_agent 没有透传 factcheck"
    assert '"provenance": final.get("provenance"' in src, "run_multi_agent 没有透传 provenance"
