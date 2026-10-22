"""搜索 BYOK（用户自己的 key）+ DeepSeek 自研搜索接入。

背景：demo 的 provenance 一直拿不到 —— 本机 SearXNG 引擎全被 CAPTCHA / 容器出不了网，
DDG 返回反爬页，Tavily 没 key。DeepSeek 10-17 上线了 `/v1/tools/search`
（每 1000 次成功请求 5 元，返回 rerank_score 0-3 与 authority 0-4）。

本文件锁定三件事：
  1. key 优先级：config.json 的 search.api_key → llm.api_key → 环境变量
  2. 没 key 时明确 skip（不能伪装成「搜到 0 条」）
  3. 有 key 时解析结果并保留权威性/相关性元数据与 URL
"""
import io
import json

import pytest

from agent_harness.core import config as cfg_mod
from agent_harness.core.tools import web


FAKE_RESP = {
    "results": [
        {
            "rerank_score": 2.4,
            "title": "2025 年新能源汽车销量",
            "url": "https://example.com/nev-2025",
            "snippet": "短摘要",
            "summary": "长摘要，给模型读的内容。" * 3,
            "site_name": "Example News",
            "date": "2026-01-01T00:00:00+00:00",
            "authority": 3,
        },
        {
            "rerank_score": 0.9,
            "title": "低质来源",
            "url": "https://low.example/x",
            "snippet": "s",
            "summary": "",
            "authority": -1,
        },
    ]
}


class _FakeHTTPResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_key(monkeypatch, key: str):
    monkeypatch.setattr(cfg_mod, "get_search_config", lambda: {"deepseek_key": key, "tavily_key": ""})


def test_no_key_marks_skip_not_empty(monkeypatch):
    """没 key 必须记 skip —— 否则诊断里看不出来到底是「没配」还是「搜不到」。"""
    _patch_key(monkeypatch, "")
    web._SEARCH_DIAG.clear()
    assert web._search_deepseek("测试") == []
    assert web._SEARCH_DIAG[-1]["engine"] == "DeepSeek"
    assert web._SEARCH_DIAG[-1]["status"] == "skip"


def test_parses_results_and_keeps_authority(monkeypatch):
    _patch_key(monkeypatch, "sk-fake")

    captured = {}

    def fake_build_opener(handler):
        class Opener:
            def open(self, req, timeout=None):
                captured["url"] = req.full_url
                captured["auth"] = req.get_header("Authorization")
                captured["body"] = json.loads(req.data.decode())
                return _FakeHTTPResp(json.dumps(FAKE_RESP).encode())
        captured["proxy_handler"] = handler
        return Opener()

    monkeypatch.setattr("urllib.request.build_opener", fake_build_opener)

    out = web._search_deepseek("新能源汽车销量", 5)

    assert len(out) == 2
    assert "https://example.com/nev-2025" in out[0]
    assert "权威性3/4" in out[0], "authority 必须保留（provenance 门禁要用）"
    assert "相关2.4" in out[0]
    # authority=-1（未知）不该被当成权威性展示
    assert "权威性" not in out[1]
    assert captured["url"].endswith("/v1/tools/search")
    assert captured["auth"] == "Bearer sk-fake"
    assert captured["body"]["count"] == 5


def test_empty_results_marked_empty(monkeypatch):
    _patch_key(monkeypatch, "sk-fake")
    monkeypatch.setattr("urllib.request.build_opener",
                        lambda h: type("O", (), {"open": lambda self, r, timeout=None:
                                                 _FakeHTTPResp(b'{"results": []}')})())
    web._SEARCH_DIAG.clear()
    assert web._search_deepseek("无结果词") == []
    assert web._SEARCH_DIAG[-1]["status"] == "empty"


# ─── key 优先级（get_search_config）───

def test_search_key_priority_config_then_llm_then_env(monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(
        "agent_harness.core.pipeline.config_manager.CONFIG_PATH", cfg_file
    )

    # 1) config.json 的 search.api_key 最优先
    cfg_file.write_text(json.dumps({"search": {"api_key": "sk-from-config"}}), encoding="utf-8")
    monkeypatch.setattr(cfg_mod, "get_llm_config", lambda: {"api_key": "sk-from-llm"})
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-env")
    assert cfg_mod.get_search_config()["deepseek_key"] == "sk-from-config"

    # 2) 没填 search 时回落到 llm.api_key
    cfg_file.write_text(json.dumps({"llm": {"api_key": "sk-from-llm"}}), encoding="utf-8")
    assert cfg_mod.get_search_config()["deepseek_key"] == "sk-from-llm"

    # 3) 都没有才用环境变量
    cfg_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cfg_mod, "get_llm_config", lambda: {})
    assert cfg_mod.get_search_config()["deepseek_key"] == "sk-from-env"


def test_search_key_decrypts_from_keystore(monkeypatch, tmp_path):
    """前端填的 key 以密文落盘（复用 SEC-18 keystore），必须能解回来。"""
    from agent_harness.core.pipeline import keystore

    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr("agent_harness.core.pipeline.config_manager.CONFIG_PATH", cfg_file)
    enc = keystore.encrypt_key("sk-encrypted-secret")
    cfg_file.write_text(json.dumps({"search": {"api_key_enc": enc}}), encoding="utf-8")
    monkeypatch.setattr(cfg_mod, "get_llm_config", lambda: {})
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    assert cfg_mod.get_search_config()["deepseek_key"] == "sk-encrypted-secret"

# ── 回归：provenance 解析不能因为「标题含冒号 / summary 含换行」就整条丢 ──
# 实测故障：DeepSeek 搜索 5 次全部 ok（25 条结果），demo 却 reported provenance_count=0，
# 根因是 _PROV_LINE 的 title 段写死 [^:]+?（标题含冒号即失配）+ summary 原样带换行。


def test_prov_line_parses_title_with_colon():
    from agent_harness.core.graph.graph_multi import _PROV_LINE

    line = "销量榜: 2025 年度盘点: 全年 1662 万辆（权威性3/4，相关2.4） [https://example.com/a]"
    m = _PROV_LINE.match(line)
    assert m is not None, "标题含冒号时 provenance 正则失配"
    assert m.group("url") == "https://example.com/a"


def test_prov_line_rejects_multiline():
    from agent_harness.core.graph.graph_multi import _PROV_LINE

    # 断行的行永远解析不出 URL —— 所以 provider 必须单行化再交付
    assert _PROV_LINE.match("标题: 第一行\n第二行 [https://example.com/a]") is None
