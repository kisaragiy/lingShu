#!/usr/bin/env python
"""guardian.py 防复现拦截器 — 最小测试
价值：测边界（无匹配/多命中/缺失规则文件），防止"看起来能拦"实则崩。
运行：cd docs/knowledge-pipeline && python -m pytest test_guardian.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guardian  # noqa: E402


def test_no_match_returns_ok():
    """无匹配动作 → 应返回 ok=True 且无警告，不崩"""
    r = guardian.guard("今天天气不错，随便聊聊")
    assert r["ok"] is True
    assert r["warnings"] == []
    assert "无匹配" in r["match"]


def test_single_match_venv():
    """启动 venv python → 命中 R-01，给出 lesson + block_hint"""
    r = guardian.guard("启动 venv python 跑 ComfyUI")
    assert r["ok"] is False
    rids = [w["rule"] for w in r["warnings"]]
    assert "R-01" in rids
    w = next(x for x in r["warnings"] if x["rule"] == "R-01")
    assert w["lesson"] and w["block_hint"]


def test_multiple_match_no_dupes():
    """一句话同时命中多个坑 → 规则去重，不重复返回同一 rule"""
    r = guardian.guard("创建 cron 任务，再用一个没验证过的 skill")
    rids = [w["rule"] for w in r["warnings"]]
    assert "R-02" in rids and "R-03" in rids
    assert len(rids) == len(set(rids)), "不应有重复规则"


def test_action_missing_rules_file():
    """规则文件被删时 → 不应崩，应优雅返回（guard 不抛裸异常）"""
    orig = guardian.RULES_FILE
    guardian.RULES_FILE = "/nonexistent/rules.md"
    try:
        r = guardian.guard("启动 venv python")
        # load_rules 里文件不存在 → 空 dict，warnings 里 lesson/hint 为空但不崩
        assert isinstance(r, dict)
        assert r["ok"] is False  # 关键词仍命中
        for w in r["warnings"]:
            assert "lesson" in w and "block_hint" in w
    finally:
        guardian.RULES_FILE = orig


def test_load_rules_parses_all_known():
    """规则文件应能解析出全部 v1+v2 规则（数据完整性，防止改动漏）"""
    rules = guardian.load_rules()
    expected = {"R-01", "R-02", "R-03", "R-04", "R-05",
                "R-06", "R-07", "R-08", "R-09", "R-10",
                "R-11", "R-12", "R-13", "R-14", "R-15",
                "R-16", "R-17", "R-18", "R-19", "R-20", "R-21"}
    missing = expected - set(rules.keys())
    assert not missing, "缺少规则: %s" % missing


def test_v2_gpu_parallel():
    """v2: 并行跑生成+VLM → 命中 R-11"""
    r = guardian.guard("并行跑 ComfyUI 生成和 VLM 评分")
    assert "R-11" in [w["rule"] for w in r["warnings"]]


def test_v2_queue_zombie():
    """v2: 队列拥堵 → 命中 R-13"""
    r = guardian.guard("ComfyUI 队列堵了有僵尸任务")
    assert "R-13" in [w["rule"] for w in r["warnings"]]


def test_v2_silent_failure():
    """v2: 静默失败管线 → 命中 R-14"""
    r = guardian.guard("这个管线会不会静默失败")
    assert "R-14" in [w["rule"] for w in r["warnings"]]


def test_v2_doc_code_drift():
    """v2: 文档说完成但代码没实现 → 命中 R-16"""
    r = guardian.guard("文档说完成了但代码没实现")
    assert "R-16" in [w["rule"] for w in r["warnings"]]


def test_v2_readfile_binary():
    """v2: read_file 误报 binary → 命中 R-18"""
    r = guardian.guard("read_file 报 binary")
    assert "R-18" in [w["rule"] for w in r["warnings"]]


def test_no_key_conflict_lost():
    """k 关键：同一 key 不应因 v2 覆盖而丢失 v1 规则（如 gpu 应同时有 R-07/R-11/R-12）"""
    r = guardian.guard("gpu 显存不够")
    rids = [w["rule"] for w in r["warnings"]]
    assert "R-07" in rids, "v1 的 R-07 被覆盖丢了"
    assert "R-11" in rids and "R-12" in rids
