"""
Property-based tests (Hypothesis) — 自动找反例比手写用例猛得多
"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from hypothesis import given, strategies as st, assume


@given(st.text(min_size=5, max_size=100))
def test_normalize_url_idempotent(url_without_utm):
    from agent_harness.core.tools.web import _normalize_url
    assume("://" not in url_without_utm or " " not in url_without_utm)
    once = _normalize_url(url_without_utm)
    twice = _normalize_url(once)
    assert once == twice, f"not idempotent: {url_without_utm!r} → {once!r} → {twice!r}"


@given(st.text(min_size=3, max_size=40, alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_."))
def test_normalize_url_strips_www(domain_part):
    from agent_harness.core.tools.web import _normalize_url
    url = f"https://www.{domain_part}.com/path"
    result = _normalize_url(url)
    # 断言要贴在 host 前缀上：`0www.com` 这类子域天然含 "www." 子串，
    # 原来的 `"www." not in result` 会被 hypothesis 撞成假失败。
    host = result.split("://", 1)[-1].split("/", 1)[0]
    assert not host.startswith("www."), f"www not stripped: {result}"


@given(st.text(min_size=3, max_size=30, alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_."))
def test_normalize_url_removes_tracking_params(param_value):
    from agent_harness.core.tools.web import _normalize_url
    assume(" " not in param_value and param_value not in ("", " "))
    url = f"https://example.com/page?utm_source={param_value}&real=1"
    result = _normalize_url(url)
    assert "utm_source" not in result, f"utm_source still in {result}"
    assert "real=1" in result, f"real param stripped from {result}"


@given(st.lists(st.text(min_size=3, max_size=30, alphabet="abcdefghijklmnopqrstuvwxyz0123456789-./")))
def test_dedup_deduplicates_identical_urls(urls):
    from agent_harness.core.tools.web import _dedup_results, _normalize_url
    import re
    assume(len(urls) >= 2)
    results = [f"title {i}: [{urls[i % len(urls)]}]" for i in range(len(urls) * 2)]
    seen: set[str] = set()
    deduped = _dedup_results(results, seen)
    deduped_urls = [re.search(r'\[([^\]]+)\]$', r).group(1) for r in deduped if re.search(r'\[([^\]]+)\]$', r)]
    normalized = [_normalize_url(u) for u in deduped_urls]
    assert len(normalized) == len(set(normalized)), f"dupes in deduped: {normalized}"


# ─── 去重键的语义不变量（@bughunter 2026-09-10）───
# 幂等只是**必要**条件；真正的不变量是"同页带不带追踪参数必须算出同一个键"。
# 首版实现把分隔符 `?` 与参数一起剥掉、留下 `&id=1`（`re.sub(r"\?&","?")` 修不到这种形状）
# → 搜索页返回的"utm 在前、真参数在后"形状会算出两个键 → 去重/缓存失效。
TRACKING_PARAMS = [
    "utm_source=tw", "utm_medium=social", "utm_campaign=x",
    "fbclid=Zz", "gclid=Yy", "ref=abc",
]


@given(
    path=st.from_regex(r"[a-zA-Z0-9_\-]{1,12}(?:/[a-zA-Z0-9_\-]{1,12}){0,2}", fullmatch=True),
    trackers=st.lists(st.sampled_from(TRACKING_PARAMS), min_size=1, max_size=3, unique=True),
)
def test_tracking_params_do_not_change_dedup_key(path, trackers):
    """等价类断言：`?id=1` 与 `?<追踪参数…>&id=1`（参数在首/尾两种位置）必须归一化成同一个键。"""
    from agent_harness.core.tools.web import _normalize_url

    clean = f"https://example.com/{path}?id=1"
    key_clean = _normalize_url(clean)
    for params in (trackers + ["id=1"], ["id=1"] + trackers):
        dirty = f"https://example.com/{path}?" + "&".join(params)
        key_dirty = _normalize_url(dirty)
        assert key_dirty == key_clean, (
            f"带追踪参数与干净 URL 算出不同去重键：\n  {dirty!r} → {key_dirty!r}\n"
            f"  {clean!r} → {key_clean!r}\n"
            "（幂等是必要条件，这才是去重键的语义不变量）"
        )