"""可复现信封（P1-1）—— 纯函数层测试。

判据（可复现信封三问）：
  ① 齐全：model/seed/version/prompt_hash/tools_hash 等字段都在，缺了用 None 不报错；
  ② 稳定：哈希用 hashlib+稳定序列化，跨进程/跨键序一致（**绝不用内置 hash()**）；
  ③ 对齐：同时输出旧键与 OTel `gen_ai.*` 键（P1-4），旧读者不破。
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core import reproducibility as repro  # noqa: E402

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


# ── ① 齐全 & 缺字段不报错 ─────────────────────────────────

def test_envelope_has_all_fields_and_none_safe():
    """零参数调用：所有字段存在、值为 None，绝不抛异常（向后兼容）。"""
    env = repro.reproducibility_envelope()
    for field in repro.ENVELOPE_FIELDS:
        assert field in env, f"信封缺字段 {field}"
        assert env[field] is None, f"{field} 缺省应为 None"
    assert set(env) == set(repro.ENVELOPE_FIELDS)


def test_envelope_echoes_provided_values():
    env = repro.reproducibility_envelope(
        model="qwen3", model_version="v2.1", provider="local",
        seed=42, temperature=0.3, system_fingerprint="fp_abc",
    )
    assert env["model"] == "qwen3"
    assert env["model_version"] == "v2.1"
    assert env["provider"] == "local"
    assert env["seed"] == 42
    assert env["temperature"] == 0.3
    assert env["system_fingerprint"] == "fp_abc"


def test_envelope_computes_prompt_and_tools_hash():
    env = repro.reproducibility_envelope(
        messages=[{"role": "user", "content": "hi"}],
        system_prompt="sys", tools=[{"name": "search"}],
    )
    assert _HEX64.match(env["prompt_hash"]), "prompt_hash 应为 sha256 十六进制"
    assert _HEX64.match(env["tools_hash"])


# ── ② 稳定哈希 ────────────────────────────────────────────

def test_stable_hash_is_deterministic_and_key_order_independent():
    a = repro.stable_hash({"b": 1, "a": 2, "c": [3, 4]})
    b = repro.stable_hash({"c": [3, 4], "a": 2, "b": 1})   # 键序打乱
    assert a == b, "稳定序列化应消除 dict 键序差异"
    assert repro.stable_hash({"a": 1}) != repro.stable_hash({"a": 2})


def test_stable_hash_is_sha256_not_python_hash():
    """必须 hashlib.sha256；内置 hash() 受 PYTHONHASHSEED 影响，不可用于复现。"""
    h = repro.stable_hash({"x": 1})
    assert _HEX64.match(h)
    assert h != str(hash("x"))  # 明显不是内置 hash 的形态
    # 已知值（稳定序列化的 sha256），锁定不会悄悄换算法
    import hashlib
    expected = hashlib.sha256(b'{"x":1}').hexdigest()
    assert h == expected


def test_prompt_hash_deterministic_and_sensitive():
    m = [{"role": "user", "content": "hello"}]
    assert repro.hash_prompt(m, "sys") == repro.hash_prompt(m, "sys")
    assert repro.hash_prompt(m, "sys") != repro.hash_prompt(m, "sys2")
    assert repro.hash_prompt(m, "sys") != repro.hash_prompt(
        [{"role": "user", "content": "hello!"}], "sys")


def test_tools_hash_none_equals_empty():
    assert repro.hash_tools(None) == repro.hash_tools([])
    assert repro.hash_tools([{"name": "a"}]) != repro.hash_tools([{"name": "b"}])


def test_stable_hash_survives_unserializable_object():
    """不可 JSON 化对象也不能抛异常（default=str 兜底）。"""
    class _Weird:
        def __str__(self):
            return "weird"

    h = repro.stable_hash({"obj": _Weird()})
    assert _HEX64.match(h)


# ── ③ OTel 对齐（P1-4）────────────────────────────────────

def test_otel_attributes_map_core_fields():
    env = repro.reproducibility_envelope(
        model="m", provider="local", temperature=0.5, seed=7,
    )
    otel = repro.otel_attributes(env)
    assert otel["gen_ai.system"] == "local"
    assert otel["gen_ai.request.model"] == "m"
    assert otel["gen_ai.request.temperature"] == 0.5
    assert otel["gen_ai.request.seed"] == 7


def test_otel_attributes_skips_none_values():
    otel = repro.otel_attributes(repro.reproducibility_envelope())
    assert otel == {}, "空值不应产出 gen_ai.* 键（避免噪声）"


def test_otel_usage_keys():
    otel = repro.otel_usage_attributes({"prompt_tokens": 12, "completion_tokens": 7})
    assert otel["gen_ai.usage.input_tokens"] == 12
    assert otel["gen_ai.usage.output_tokens"] == 7
    assert repro.otel_usage_attributes(None) == {}


def test_envelope_record_has_both_old_and_new_keys():
    """过渡期：新旧键并存，旧读者（model/prompt_tokens）与新读者（gen_ai.*）都满足。"""
    env = repro.reproducibility_envelope(model="m", provider="local")
    rec = repro.envelope_record(env, {"prompt_tokens": 3, "completion_tokens": 4})
    # 旧键
    assert rec["model"] == "m"
    assert rec["prompt_tokens"] == 3
    assert rec["completion_tokens"] == 4
    # 新键
    assert rec["gen_ai.request.model"] == "m"
    assert rec["gen_ai.system"] == "local"
    assert rec["gen_ai.usage.input_tokens"] == 3
    assert rec["gen_ai.usage.output_tokens"] == 4


def test_envelope_record_none_safe():
    rec = repro.envelope_record(None, None)
    for field in repro.ENVELOPE_FIELDS:
        assert rec[field] is None
