"""种子 ⑫：`query_decompose` 的静默退化（门禁第一次真跑抓到的 F821，@testgate 移交）。

`core/tools/web.py` 原状：函数内 `import re as _re`，却写了 `re.DOTALL` → `NameError`
→ 被同函数 `except Exception: pass` 吞掉 → **永远返回单元素数组**。它长这样最危险的地方是
"返回值形状完全合法"：LLM 调了、JSON 回了、格式对，外部**没有任何可观测差别**。
该函数经 `register_tool("query_decompose", …)`（web.py:425）注册，是 agent 可达的活工具。

判据：LLM 回多段时返回段数必须 > 1 —— 修复前必红（实测返回 1 段）。
"""
import json
import re
from pathlib import Path

import agent_harness.core.tools.web as web

WEB_PY = Path(__file__).resolve().parent.parent / "src" / "agent_harness" / "core" / "tools" / "web.py"

MULTILINE = '[\n  "智能手表 市场规模 2026",\n  "smartwatch market share",\n  "竞品 定价 对比"\n]'


def test_decompose_parses_multiline_json_array(monkeypatch):
    """多行 JSON 数组必须解析出来（`re.DOTALL` 就是为它存在的）。"""
    monkeypatch.setattr(web, "call_llama", lambda *a, **k: (MULTILINE, None))
    out = json.loads(web._tool_query_decompose("一个足够长的多角度问题，涉及市场规模与竞品定价"))
    assert len(out) > 1, (
        f"多段查询只返回 {len(out)} 段 = 退化成「不分解」。"
        "症状是异常被静默吞掉、返回值形状却合法，外部看不出来。"
    )
    assert out == ["智能手表 市场规模 2026", "smartwatch market share", "竞品 定价 对比"]


def test_short_query_and_garbage_llm_reply_degrade_to_valid_shape(monkeypatch):
    """降级允许（短查询 / LLM 回垃圾），但形状必须永远是合法 JSON 数组——
    这正是"降级返回值长得太正常"之所以危险：可降级 ≠ 可静默。"""
    monkeypatch.setattr(web, "call_llama", lambda *a, **k: ("完全不是 JSON", None))
    assert json.loads(web._tool_query_decompose("短")) == ["短"]
    assert json.loads(web._tool_query_decompose("这个查询够长但 LLM 给的是垃圾")) == ["这个查询够长但 LLM 给的是垃圾"]


def test_no_function_local_re_alias_remains():
    """回归锁：函数内 `import re as _re` 的重复形态已统一为模块级 `import re`。

    保留它就是保留"每加一个函数就多一次 alias、写错一次就静默退化"的土壤。
    """
    src = WEB_PY.read_text(encoding="utf-8")
    locals_ = re.findall(r"^[ \t]+import re as _re\s*$", src, flags=re.M)
    assert not locals_, f"仍有 {len(locals_)} 处函数内 re alias，应统一到模块级 import re"
    assert re.search(r"^import re$", src, flags=re.M), "模块级 import re 缺失"


# ─── 语义层：形状合法 ≠ 值正确（@bughunter 2026-09-10 实测形状）───
#
# 上面两条只锁"形状+段数"，锁不住**语义**：`re.search(r'\[.*?\]')` 取的是**第一个**方括号片段，
# 校验又只有 `isinstance(queries, list)`，于是以下 payload 会返回"长得完全合法的垃圾"——
# 模型会把它当成功结果用，这比降级更坏（降级至少看得出来）。
GARBAGE_PAYLOADS = {
    "解释里的引用编号": '抱歉，我先看[1]这段：\n["a b","c d","e f"]',   # -> [1]
    "纯数字数组": "[1, 2, 3]",                                        # -> [1, 2, 3]
    "空字符串数组": '["", ""]',                                        # -> ["", ""]
    "数字/字符串混合": '[1, "真实查询", 2]',                            # -> [1, "真实查询", 2]
}

LONG_Q = "一个足够长的多角度问题，涉及市场规模、竞品定价与渠道结构"


def test_output_items_are_nonempty_strings_never_successful_garbage(monkeypatch):
    """不变量：无论 LLM 回什么，返回数组的**每一项必须是非空字符串**。

    类型校验（`isinstance(list)`）挡不住 `[1]` 这种"成功垃圾"；工具输出契约是
    类型 ∪ 语义两层，只校验类型的解析都是半成品。
    """
    bad = {}
    for name, raw in GARBAGE_PAYLOADS.items():
        monkeypatch.setattr(web, "call_llama", lambda *a, **k: (raw, None))
        arr = json.loads(web._tool_query_decompose(LONG_Q))
        offenders = [x for x in arr if not isinstance(x, str) or not x.strip()]
        if offenders:
            bad[name] = (raw, arr, offenders)
    assert not bad, (
        "返回了长得合法的垃圾（模型会当成功结果用）：\n"
        + "\n".join(f"  [{n}] input={r!r} -> 返回 {a}，非法项 {o}" for n, (r, a, o) in bad.items())
        + "\n修法：取最后一个/最长的数组片段，并收紧为「list 且每项是非空 str」；不符合就走"
          "**可观测降级**返回 [原查询]，绝不返回 [1]。"
    )


def test_explanatory_prose_before_array_still_yields_real_queries(monkeypatch):
    """LLM 习惯先写解释再给 JSON（prompt 里已经说"不加解释"，但它不总是听话）。

    当前实现取**第一个**方括号片段 → 命中解释里的 `[1]` → 返回 `[1]`。正确行为是
    拿到真正的那个数组（3 段），而不是"合法但无用"的 `[1]`、也不该白降级成单段。
    """
    raw = '抱歉，我先看[1]这段：\n["智能手表 市场规模", "smartwatch share", "竞品 定价"]'
    monkeypatch.setattr(web, "call_llama", lambda *a, **k: (raw, None))
    out = json.loads(web._tool_query_decompose(LONG_Q))
    assert out == ["智能手表 市场规模", "smartwatch share", "竞品 定价"], (
        f"解释+数组的 payload 应解析出真正的数组，实测 {out}"
        "（取第一个方括号片段会命中解释里的 [1]，产出「成功垃圾」）"
    )


def test_registered_description_count_matches_actual_cap(monkeypatch):
    """工具 description 是**已发布给模型的契约**（`web.py:419` 写"拆成 3 条"），
    实现是 `queries[:5]` —— 契约与实现分叉，模型按 description 决策、拿到的是另一个数。

    这里锁"两处必须一致"；正解是让它们同源（一个 `_QUERY_DECOMPOSE_MAX` 常量同时生成
    description 文本与切片），改契约（3→5）要走成本决策、单独提交。
    """
    src = WEB_PY.read_text(encoding="utf-8")
    block = src[src.index('register_tool("query_decompose"'):]
    block = block[: block.index(")")] if ")" in block else block
    m = re.search(r"description\D*?(\d+)\s*条", block)
    assert m, f"没能从 query_decompose 的注册描述里解析出条数声明，block={block[:200]!r}"
    claimed = int(m.group(1))

    seven = json.dumps([f"查询角度 {i}" for i in range(1, 8)], ensure_ascii=False)
    monkeypatch.setattr(web, "call_llama", lambda *a, **k: (seven, None))
    got = len(json.loads(web._tool_query_decompose(LONG_Q)))
    assert got == claimed, (
        f"注册描述承诺 {claimed} 条，实测返回 {got} 条 —— description 与实现分叉。"
        "修法：抽 `_QUERY_DECOMPOSE_MAX` 同时驱动两处（改契约要单独提交）。"
    )

