"""L2 P1 批次：llm content 提取（ENG-23）+ 报告标题（ENG-18）+ 表格表头（ENG-26）。

三条都是「静默产出错误」类缺陷：不抛异常、测试全绿，但用户拿到的东西是错的。
"""
import pytest

from agent_harness.core.pipeline import llm
import agent_harness.apps.research.pipeline.report_formatter as rf


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


@pytest.fixture
def llm_env(monkeypatch):
    """关缓存 + 屏蔽账本/指标/信封副作用。"""
    monkeypatch.setattr(llm, "_llm_cache_key", lambda *a, **k: None)
    monkeypatch.setattr(llm, "ledger_bump", lambda *a, **k: None)
    monkeypatch.setattr(llm, "_record_llm_metrics", lambda *a, **k: None)
    monkeypatch.setattr(llm, "_emit_llm_envelope", lambda *a, **k: None)


def _chat_payload(content="", reasoning="", usage=42):
    msg = {"content": content}
    if reasoning:
        msg["reasoning_content"] = reasoning
    return {"choices": [{"message": msg}], "usage": {"total_tokens": usage}}


# ─── ENG-23：reasoning 尾段不能冒充答案 ───

def test_empty_content_returns_empty_not_reasoning_tail(llm_env, monkeypatch):
    """Qwen3 系 thinking 未关：content 空、reasoning 有内容 → 必须返回空串，不能当答案。"""
    monkeypatch.setattr(llm, "get_llm_config", lambda: {"api_url": "http://stub", "model": "qwen3"})
    monkeypatch.setattr(llm, "_http_post",
                        lambda *a, **k: _Resp(_chat_payload("", "这是思考过程：先分析用户意图，然后……")))

    text, tokens = llm.call_llama([{"role": "user", "content": "你好"}], max_tokens=2048)
    assert text == "", f"把推理尾段当答案返回了: {text!r}"
    assert "这是思考过程" not in text
    assert tokens == 42


def test_long_reasoning_marked_as_truncated_output(llm_env, monkeypatch):
    """reasoning 长度 > max_tokens*0.8 → 视为被截断，返回且显式标注（不静默）。"""
    monkeypatch.setattr(llm, "get_llm_config", lambda: {"api_url": "http://stub", "model": "r1"})
    long_reasoning = "推理" * 1000          # 2000 字 > 100*0.8
    monkeypatch.setattr(llm, "_http_post",
                        lambda *a, **k: _Resp(_chat_payload("", long_reasoning)))

    text, _ = llm.call_llama([{"role": "user", "content": "你好"}], max_tokens=100)
    assert text.startswith("[推理截断输出]"), text[:40]


def test_normal_content_untouched(llm_env, monkeypatch):
    """正常 content 完全不受影响（防止把主路径改坏）。"""
    monkeypatch.setattr(llm, "get_llm_config", lambda: {"api_url": "http://stub", "model": "qwen3"})
    monkeypatch.setattr(llm, "_http_post",
                        lambda *a, **k: _Resp(_chat_payload("这是正式答案", "内部推理……")))
    text, _ = llm.call_llama([{"role": "user", "content": "你好"}])
    assert text == "这是正式答案"


def test_post_cloud_disables_thinking(llm_env, monkeypatch):
    """云端 Qwen3 系默认开思考 → payload 必须显式 thinking disabled。"""
    captured = {}

    def _fake_post(session, url, payload, headers=None, timeout=None):
        captured.update(payload)
        return _Resp(_chat_payload("云端答案"))

    monkeypatch.setattr(llm, "get_llm_config",
                        lambda: {"api_url": "https://api.stub", "api_key": "k", "model": "qwen3-max"})
    monkeypatch.setattr(llm, "_http_post", _fake_post)

    text, _ = llm._post_cloud([{"role": "user", "content": "hi"}])
    assert text == "云端答案"
    assert captured.get("thinking") == {"type": "disabled"}, captured.keys()


# ─── ENG-18：报告标题不被 URL 尾部覆盖 ───

def test_report_title_survives_source_map():
    html = rf.generate_report_html("我的尽调报告", "结论见[https://example.com/articles/abc]")
    assert "<h1>我的尽调报告</h1>" in html
    assert "<title>我的尽调报告</title>" in html
    assert "<h1>abc</h1>" not in html, "标题被 URL 路径尾部覆盖了"


def test_report_title_html_escaped():
    html = rf.generate_report_html("<script>alert(1)</script>", "正文无引用")
    assert "<h1><script>alert(1)</script></h1>" not in html
    assert "&lt;script&gt;" in html


# ─── ENG-26：markdown 表格表头 ───

def test_table_header_rendered_as_th():
    md = "| 指标 | 2024 |\n| --- | --- |\n| 营收 | 987 亿 |"
    html = rf._markdown_to_html(md)
    assert "<th>指标</th><th>2024</th>" in html
    assert html.count("<th>") == 2
    assert "<td>指标</td>" not in html, "表头被当数据行的老 bug 复现"


def test_table_without_separator_all_td():
    """没有分隔行的表格不能把第一行误判成表头。"""
    md = "| 1 | 2 |\n| 3 | 4 |"
    html = rf._markdown_to_html(md)
    assert "<th>" not in html
    assert html.count("<td>") == 4


def test_table_closed_before_paragraph():
    md = "| A | B |\n| --- | --- |\n| 1 | 2 |\n\n正文段落"
    html = rf._markdown_to_html(md)
    assert html.index("</table>") < html.index("正文段落")


def test_table_at_end_of_text_flushed():
    """文末无换行的表格最后一行不能被吞掉。"""
    md = "| A | B |\n| --- | --- |\n| 1 | 2 |"
    html = rf._markdown_to_html(md)
    assert "<td>1</td><td>2</td>" in html
    assert html.rstrip().endswith("</table>")
