"""生成 LLM cassette 夹具（P1-5）。

cassette = 一份**真实形态**的 OpenAI 兼容响应体 + 其请求指纹。测试按 request 查表
回放，于是「content 空但 reasoning 有值」「预算被思考吃光」这类只有线上才会出现的
失败形态，变得可以离线回归。

用法：
    python scripts/gen_llm_cassettes.py            # 重新生成全部夹具
    python scripts/gen_llm_cassettes.py --check    # 校验磁盘上的夹具与代码一致

为什么指纹要进夹具：prompt 改了却忘了更新预期响应时，测试会当场红（而不是悄悄
拿旧响应喂新请求）；这也让 reproducibility.hash_prompt 这个原语第一次被真实消费。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_harness.core.reproducibility import hash_prompt  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "llm_cassettes"


def _msg(content: str, **extra) -> dict:
    msg = {"role": "assistant", "content": content}
    msg.update(extra)
    return msg


# ─── 夹具定义 ──────────────────────────────────────────────────────────────
# 每个 case 都是线上真实出现过的形态（见 core/pipeline/llm.py 的注释与教训）。
CASES: list[dict] = [
    {
        "name": "normal_content_with_reasoning",
        "why": "思考与答案并存：答案走 content，思考单独交还，两条通道永不合并",
        "request": {
            "messages": [{"role": "user", "content": "列出三条测试建议"}],
            "system_prompt": "你是测试助手",
        },
        "response": {
            "id": "chatcmpl-c1",
            "model": "deepseek-chat",
            "choices": [{
                "index": 0,
                "finish_reason": "stop",
                "message": _msg(
                    "1. 覆盖失败路径\n2. 断言可复现\n3. 夹具与代码同源",
                    reasoning_content="用户要三条建议，先分组再排序……",
                ),
            }],
            "usage": {"prompt_tokens": 18, "completion_tokens": 26, "total_tokens": 44},
        },
    },
    {
        "name": "content_empty_finish_length",
        "why": "预算被思考吃光、答案还没开始写 —— 必须返回空串让调用方加预算重试，"
               "绝不能把思考轨迹当答案交出去",
        "request": {
            "messages": [{"role": "user", "content": "证明根号 2 是无理数"}],
            "system_prompt": "你是数学助手",
        },
        "response": {
            "id": "chatcmpl-c2",
            "model": "deepseek-reasoner",
            "choices": [{
                "index": 0,
                "finish_reason": "length",
                "message": _msg("", reasoning_content="假设根号2=p/q，p、q互质，则p²=2q²，故p为偶数……"),
            }],
            "usage": {"prompt_tokens": 22, "completion_tokens": 4096, "total_tokens": 4118},
        },
    },
    {
        "name": "reasoning_only_over_budget",
        "why": "R1 系模型把答案写在推理里且吃满预算：标 [推理截断输出]，并且不缓存"
               "（缓存会让下一个调用者拿到截断品）",
        "request": {
            "messages": [{"role": "user", "content": "写一段 200 字的项目总结"}],
            "system_prompt": "你是写作助手",
        },
        "response": {
            "id": "chatcmpl-c3",
            "model": "deepseek-reasoner",
            "choices": [{
                "index": 0,
                "finish_reason": "length",
                "message": _msg(
                    "",
                    # 长度 > max_tokens * 0.8（测试里传 max_tokens=100）
                    reasoning_content="本项目" + "围绕可复现性展开，" * 12,
                ),
            }],
            "usage": {"prompt_tokens": 15, "completion_tokens": 100, "total_tokens": 115},
        },
    },
    {
        "name": "content_truncated_by_length",
        "why": "答案写了一半被 max_tokens 截断：content 照常返回，仍是可缓存结果",
        "request": {
            "messages": [{"role": "user", "content": "解释一下熔断器半开状态"}],
            "system_prompt": "你是后端助手",
        },
        "response": {
            "id": "chatcmpl-c4",
            "model": "deepseek-chat",
            "choices": [{
                "index": 0,
                "finish_reason": "length",
                "message": _msg("半开状态指的是熔断器在冷却期后放行少量请求"),
            }],
            "usage": {"prompt_tokens": 20, "completion_tokens": 40, "total_tokens": 60},
        },
    },
    {
        "name": "http_400_error_body",
        "why": "400 响应体没有 choices —— 取答案要返回空串而不是抛 KeyError，"
               "否则一次参数错就把整条 pipeline 打崩",
        "request": {
            "messages": [{"role": "user", "content": "x" * 9000}],
            "system_prompt": "你是助手",
        },
        "response": {
            "error": {
                "message": "This model's maximum context length is 8192 tokens.",
                "type": "invalid_request_error",
                "code": "context_length_exceeded",
            }
        },
    },
    {
        "name": "malformed_empty_choices",
        "why": "网关/代理返回 choices: [] 的空壳响应（实测遇到过）—— 同样必须降级成空串",
        "request": {
            "messages": [{"role": "user", "content": "ping"}],
            "system_prompt": "你是助手",
        },
        "response": {"id": "chatcmpl-c6", "model": "qwen3.5:9b", "choices": []},
    },
    {
        "name": "ollama_reasoning_field",
        "why": "ollama /v1 用 reasoning 字段而非 reasoning_content —— 字段名差异不能让"
               "思考轨迹漏进答案通道",
        "request": {
            "messages": [{"role": "user", "content": "1+1 等于几"}],
            "system_prompt": "你是助手",
        },
        "response": {
            "id": "chatcmpl-c7",
            "model": "qwen3.5:9b",
            "choices": [{
                "index": 0,
                "finish_reason": "stop",
                "message": _msg("2", reasoning="这是最基础的加法……"),
            }],
        },
    },
]


def build() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for case in CASES:
        req = case["request"]
        record = dict(case)
        record["prompt_hash"] = hash_prompt(req["messages"], req.get("system_prompt"))
        out[case["name"]] = record
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只校验，不写盘")
    args = parser.parse_args()

    records = build()
    OUT.mkdir(parents=True, exist_ok=True)

    stale: list[str] = []
    for name, record in records.items():
        path = OUT / f"{name}.json"
        payload = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != payload:
                stale.append(name)
        else:
            path.write_text(payload, encoding="utf-8")

    if args.check:
        if stale:
            print(f"[cassettes] 过期或缺失：{', '.join(stale)}\n"
                  f"跑 python scripts/gen_llm_cassettes.py 重新生成")
            return 2
        print(f"[cassettes] OK：{len(records)} 份夹具与代码一致")
        return 0

    print(f"[cassettes] 已写 {len(records)} 份 → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
