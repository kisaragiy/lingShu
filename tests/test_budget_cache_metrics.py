"""P0-1 cache 指标 — TokenBudget 缓存命中统计（向后兼容，只加不改）。

覆盖：
- record_cache 累加 cache_hit_tokens / cache_miss_tokens
- cache_hit_rate 计算（零除安全）
- summary() 并入缓存字段
- 未记录缓存时默认值不改变原有行为
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_harness.core.budget import TokenBudget


def test_cache_fields_default_zero():
    b = TokenBudget()
    assert b.cache_hit_tokens == 0
    assert b.cache_miss_tokens == 0
    assert b.cache_hit_rate == 0.0  # 无数据时不做 0/0


def test_record_cache_accumulates():
    b = TokenBudget()
    b.record_cache(hit_tokens=3000, miss_tokens=1000)
    b.record_cache(hit_tokens=2000)
    assert b.cache_hit_tokens == 5000
    assert b.cache_miss_tokens == 1000


def test_cache_hit_rate():
    b = TokenBudget()
    b.record_cache(hit_tokens=750, miss_tokens=250)
    assert b.cache_hit_rate == 0.75


def test_cache_hit_rate_all_hits():
    b = TokenBudget()
    b.record_cache(hit_tokens=100, miss_tokens=0)
    assert b.cache_hit_rate == 1.0


def test_cache_hit_rate_zero_safe():
    b = TokenBudget()
    assert b.cache_hit_rate == 0.0
    b.record_cache(miss_tokens=500)
    assert b.cache_hit_rate == 0.0


def test_summary_includes_cache_fields():
    b = TokenBudget()
    b.record_cache(hit_tokens=800, miss_tokens=200)
    s = b.summary()
    assert s["cache_hit_tokens"] == 800
    assert s["cache_miss_tokens"] == 200
    assert s["cache_hit_rate"] == 0.8


def test_existing_summary_keys_intact():
    """向后兼容：原有 summary 键仍在，缓存字段只是新增。"""
    s = TokenBudget(soft_limit=1000).summary()
    for key in ("used", "limit", "hard_limit", "pct", "cost_usd", "level", "elapsed_s"):
        assert key in s


def test_add_tokens_still_works_with_cache():
    """cache 统计不干扰原有 add()/check() 行为。"""
    b = TokenBudget(soft_limit=1000)
    b.record_cache(hit_tokens=100, miss_tokens=50)
    lvl = b.add(500)
    assert b.used == 500
    assert b.cache_hit_tokens == 100
    assert lvl.value in ("ok", "warn")
