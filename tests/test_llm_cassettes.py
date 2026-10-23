"""P1-5：LLM 真实失败形态的离线回归（cassette 回放）。

背景：这些失败形态（content 空但 reasoning 有值、预算被思考吃光、400 空壳响应）
只在线上出现。以前测试全手写 fake，形状由写测试的人**想象**，于是"思考冒充答案"
这类致命分支从来没被真正跑到过。

现在夹具是 `tests/fixtures/llm_cassettes/*.json`（由 scripts/gen_llm_cassettes.py
生成），每个夹具带 prompt_hash —— 请求改了却忘更新响应，这里当场红。

验收标准（评审 P1-5）：回退 `_extract_openai_content` 的「思考不冒充答案」分支
→ 本文件必须红。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_harness.core.pipeline.llm import _extract_openai_content, extract_answer
from agent_harness.core.reproducibility import hash_prompt

CASSETTE_DIR = Path(__file__).parent / "fixtures" / "llm_cassettes"


def _load_all() -> dict[str, dict]:
    files = sorted(CASSETTE_DIR.glob("*.json"))
    assert files, f"没有夹具：{CASSETTE_DIR}"
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in files}


CASSETTES = _load_all()


class FakeTransport:
    """按 prompt 指纹查表的假传输层。

    真实调用点是 `requests.Session`，这里只回放**响应体**，因为 extract_answer 这一层
    是纯函数 —— 它拿到的就是线上那份 JSON。要测更上层（重试/预算抬升）就把这个类
    塞进那个函数的 transport 参数（P1-7 再做）。
    """

    def __init__(self, cassettes: dict[str, dict]) -> None:
        self._by_hash = {c["prompt_hash"]: c for c in cassettes.values()}

    def lookup(self, messages: list[dict], system_prompt: str | None = None) -> dict:
        key = hash_prompt(messages, system_prompt)
        if key not in self._by_hash:
            raise KeyError(
                f"cassette 未录制该请求（prompt_hash={key[:12]}）——"
                f"先在 scripts/gen_llm_cassettes.py 里加 case"
            )
        return self._by_hash[key]["response"]


@pytest.fixture(scope="module")
def transport() -> FakeTransport:
    return FakeTransport(CASSETTES)


# ─── 夹具自身的一致性 ──────────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(CASSETTES))
def test_cassette_prompt_hash_matches_request(name: str) -> None:
    """请求与响应同源：prompt 改了必须同步更新夹具，否则指纹对不上。"""
    case = CASSETTES[name]
    expected = hash_prompt(case["request"]["messages"], case["request"].get("system_prompt"))
    assert case["prompt_hash"] == expected, f"{name} 的 prompt_hash 已过期，跑 gen_llm_cassettes.py"


@pytest.mark.parametrize("name", sorted(CASSETTES))
def test_cassette_has_reason(name: str) -> None:
    """每份夹具都要写清它模拟的线上形态，避免变成"孤儿数据"。"""
    assert CASSETTES[name].get("why"), f"{name} 缺 why"


# ─── 失败形态回放 ──────────────────────────────────────────────────────────

def test_normal_response_keeps_channels_separate(transport: FakeTransport) -> None:
    data = transport.lookup(
        [{"role": "user", "content": "列出三条测试建议"}], "你是测试助手",
    )
    answer, reasoning, finish, cacheable = _extract_openai_content(data, 2048)
    assert answer.startswith("1. 覆盖失败路径")
    assert "分组再排序" in reasoning          # 思考单独交还
    assert "分组再排序" not in answer          # 绝不混进答案
    assert finish == "stop"
    assert cacheable is True


def test_empty_content_with_length_returns_empty_not_reasoning(transport: FakeTransport) -> None:
    """★ 核心：预算被思考吃光时，答案是空串 —— 不许拿思考轨迹充数。"""
    data = transport.lookup([{"role": "user", "content": "证明根号 2 是无理数"}], "你是数学助手")
    answer, reasoning, finish, cacheable = _extract_openai_content(data, 4096)
    assert answer == "", "思考轨迹冒充了答案 —— 这是本仓最危险的降级路径"
    assert "假设根号2" in reasoning
    assert finish == "length"
    assert cacheable is False, "空答案不能进缓存，否则加预算重试会直接命中空值"


def test_reasoning_over_budget_marked_and_not_cached(transport: FakeTransport) -> None:
    data = transport.lookup([{"role": "user", "content": "写一段 200 字的项目总结"}], "你是写作助手")
    answer, _reasoning, finish, cacheable = _extract_openai_content(data, 100)
    assert answer.startswith("[推理截断输出] ")
    assert finish == "length"
    assert cacheable is False


def test_content_truncated_by_length_still_answerable(transport: FakeTransport) -> None:
    data = transport.lookup([{"role": "user", "content": "解释一下熔断器半开状态"}], "你是后端助手")
    answer, _r, finish, cacheable = _extract_openai_content(data, 2048)
    assert answer.startswith("半开状态指的是")
    assert finish == "length"
    assert cacheable is True


def test_http_400_body_degrades_to_empty(transport: FakeTransport) -> None:
    """无 choices 的错误体不能抛异常把 pipeline 打崩。"""
    data = transport.lookup([{"role": "user", "content": "x" * 9000}], "你是助手")
    assert "error" in data
    assert extract_answer(data) == ""


def test_malformed_empty_choices_degrades_to_empty(transport: FakeTransport) -> None:
    data = transport.lookup([{"role": "user", "content": "ping"}], "你是助手")
    assert data["choices"] == []
    assert extract_answer(data) == ""


def test_ollama_reasoning_field_is_recognized(transport: FakeTransport) -> None:
    """ollama 用 reasoning 字段：字段名不同，通道规则相同。"""
    data = transport.lookup([{"role": "user", "content": "1+1 等于几"}], "你是助手")
    answer, reasoning, _finish, cacheable = _extract_openai_content(data, 2048)
    assert answer == "2"
    assert "最基础的加法" in reasoning
    assert "最基础的加法" not in answer
    assert cacheable is True


def test_unknown_request_fails_loudly(transport: FakeTransport) -> None:
    """未录制的请求必须报错（而不是静默返回空）——否则回放层会掩盖改动。"""
    with pytest.raises(KeyError, match="未录制"):
        transport.lookup([{"role": "user", "content": "这条 prompt 从没录过"}], None)
