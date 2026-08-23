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
    assert "www." not in result, f"www not stripped: {result}"


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


@given(st.integers(min_value=1, max_value=15), st.integers(min_value=0, max_value=200))
def test_protobuf_varint_roundtrip(field_num, value):
    from agent_harness.core.tools.rev_utils import _infer_protobuf_schema
    key = (field_num << 3) | 0
    v = value
    val_bytes = []
    while v > 0x7F:
        val_bytes.append((v & 0x7F) | 0x80)
        v >>= 7
    val_bytes.append(v & 0x7F)
    hex_str = (bytes([key]) + bytes(val_bytes)).hex()
    result = _infer_protobuf_schema(hex_str)
    assert result["ok"]
    assert any(f["field_number"] == field_num for f in result["fields"]), \
        f"field {field_num} not found in {result['fields']}"


@given(st.text(min_size=1, max_size=20, alphabet="abcdefghijklmnopqrstuvwxyz"))
def test_protobuf_string_roundtrip(text):
    from agent_harness.core.tools.rev_utils import _infer_protobuf_schema
    assume(text.isprintable())
    data = text.encode("utf-8")
    key = (1 << 3) | 2
    v = len(data)
    len_bytes = []
    while v > 0x7F:
        len_bytes.append((v & 0x7F) | 0x80)
        v >>= 7
    len_bytes.append(v & 0x7F)
    hex_str = (bytes([key]) + bytes(len_bytes) + data).hex()
    result = _infer_protobuf_schema(hex_str)
    assert result["ok"]
    assert any(
        isinstance(f.get("sample"), str) and text[:10] in f["sample"]
        for f in result["fields"]
    ), f"string '{text}' not recovered in {result}"