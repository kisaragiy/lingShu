"""`validate_result` 的结果有效性判定 —— 工具结果层指标的数据源。

为什么单独测：`harness_tool_results_total{tool,verdict}` 的 verdict 直接取自这里。
判错 = **监控在说谎**（真实案例：LLM 后端不可用时 Worker 输出
"[搜索] 由于 LLM 后端不可用，搜索结果不可用" —— 关键词表有"失败"没有"不可用"，
被判成 severity=ok，于是 /metrics 里 search 一路显示"结果有效"）。

判据：无效结果必须判 error/warning，正常结果不得被误杀。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core.tools.registry import validate_result  # noqa: E402


def _ok(data):
    return {"success": True, "error": None, "data": data}


# 真实采集来的降级输出（LLM 后端不可用时 Worker 的 fallback 模板）
DEGRADED_OUTPUTS = [
    "[搜索] 由于 LLM 后端不可用，搜索结果不可用。\n请配置 HARNESS_DEEPSEEK_API 或 HARNESS_LLAMA_API。\n原始查询: xxx",
    "[分析] LLM 后端不可用，无法生成分析。\n原始任务: xxx",
    "[执行] LLM 后端不可用。\n原始任务: xxx",
    "[搜索失败] 所有搜索引擎不可用",
    "未找到相关文件",
    "该功能未安装",
    "服务未运行",
]


import pytest  # noqa: E402


@pytest.mark.parametrize("data", DEGRADED_OUTPUTS)
def test_degraded_output_is_not_ok(data):
    """降级/失败文本一律不得判为 ok。"""
    r = validate_result("search", _ok(data))
    assert not r["passed"], f"无效结果被判为有效：{data[:50]!r} → {r}"
    assert r["severity"] in ("error", "warning")


def test_llm_unavailable_template_flags_error():
    """LLM 不可用模板必须判 error（这是本轮实测踩到的漏判）。"""
    data = "[搜索] 由于 LLM 后端不可用，搜索结果不可用。\n请配置 HARNESS_DEEPSEEK_API。"
    r = validate_result("search", _ok(data))
    assert r["severity"] == "error", f"应判 error，实测 {r}"


def test_normal_result_still_ok():
    """反向控制：正常结果不得被误杀（防一刀切）。"""
    good = [
        "LangGraph 是一个用于构建有状态多智能体应用的框架，支持循环与持久化。",
        "[搜索结果] 1. LangGraph 官方文档 - langchain-ai.github.io\n2. 多智能体编排实践 - 知乎",
    ]
    for g in good:
        r = validate_result("search", _ok(g))
        assert r["passed"], f"正常结果被误杀：{g[:40]!r} → {r}"


def test_failed_call_is_error():
    """工具调用本身失败 → error。"""
    r = validate_result("search", {"success": False, "error": "Connection refused", "data": None})
    assert r["passed"] is False and r["severity"] == "error"


def test_empty_and_short_output():
    """空/过短输出 → warning（不是 error，属于可疑但非故障）。"""
    assert validate_result("t", _ok(None))["severity"] == "warning"
    assert validate_result("t", _ok("ab"))["severity"] == "warning"
    assert validate_result("t", _ok([]))["severity"] == "warning"
