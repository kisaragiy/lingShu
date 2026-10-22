"""state schema 契约：节点返回的 key 必须已在 SupervisorState 里声明。

LangGraph 会**静默丢弃** schema 之外的 state key —— 不报错、不告警。
实测故障：finalize 节点算出 15 条 provenance 并 return，但 `provenance` /
`factcheck` 未在 SupervisorState 声明 → 全部被框架吞掉 → 调用方拿到空列表，
表现成"搜索明明有结果，报告却零来源"。计算没错，是传输层丢的。

这条测试用 AST 静态比对，机械堵住同类问题。
"""
from __future__ import annotations

import ast
import pathlib

GRAPH_MULTI = pathlib.Path(__file__).resolve().parents[1] / "src" / "agent_harness" / "core" / "graph" / "graph_multi.py"


def _finalize_return_keys() -> set[str]:
    """取 graph_multi 里返回 final_output 的那个 return 字典的 key 集合。"""
    tree = ast.parse(GRAPH_MULTI.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            keys = {k.value for k in node.value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if "final_output" in keys:
                return keys
    raise AssertionError("graph_multi 里没找到返回 final_output 的 return")


def test_finalize_keys_all_declared_in_supervisor_state():
    from agent_harness.core.pipeline.state import SupervisorState

    declared = set(SupervisorState.__annotations__)
    returned = _finalize_return_keys()
    undeclared = returned - declared
    assert not undeclared, (
        f"这些 key 会被 LangGraph 静默丢弃（未在 SupervisorState 声明）：{sorted(undeclared)}"
    )


def test_provenance_and_factcheck_declared():
    from agent_harness.core.pipeline.state import SupervisorState

    ann = SupervisorState.__annotations__
    assert "provenance" in ann
    assert "factcheck" in ann
