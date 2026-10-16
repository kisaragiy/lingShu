"""溯源验证 agent 的契约测试（生成/评判分离）。

四类必测场景：
  1. 支撑      —— claim 被某源支撑 → supported
  2. 不支撑    —— 无源支撑        → unsupported
  3. 部分支撑  —— 只被覆盖一部分  → partial
  4. 引用错配  —— claim 引用的 [N] 并不支持它（真正支持它的是另一个源，或没有）

判定器是规则版（关键词/数字/实体重叠度），只做**近似**判定；本测试锁的是
"接口 + 判定分级 + 错配可检出"这条契约，不宣称语义级正确性。
"""

from agent_harness.core.safety.claim_verifier import (
    PARTIAL,
    SUPPORTED,
    UNSUPPORTED,
    LLMJudge,
    RuleBasedJudge,
    Verdict,
    extract_citations,
    rule_judge_spec,
    sources_from_citation_map,
    verify_claims,
)


def _src(sid: int, text: str, url: str = "") -> dict:
    return {"id": sid, "url": url or f"https://example.com/{sid}", "text": text}


# ── 场景 1：支撑 ──────────────────────────────────────────────

class TestSupported:
    def test_claim_supported_by_source(self):
        claims = ["灵枢的规则库共有 35 条规则，覆盖求职与显存两类教训。"]
        sources = [_src(1, "规则库 35 条规则，覆盖求职与显存两类教训，来自 v1/v2/v4 三次沉淀。")]
        verdicts = verify_claims(claims, sources)

        assert len(verdicts) == 1
        v = verdicts[0]
        assert v.status == SUPPORTED
        assert v.evidence, "支撑判定必须给出证据（命中的源 id + 片段）"
        assert v.evidence[0].source_id == 1
        assert v.evidence[0].snippet
        assert v.method

    def test_cited_source_supports_it(self):
        """claim 带 [1] 且源 1 确实支撑 → 不是错配。"""
        claims = ["规则库共 35 条规则 [1]"]
        sources = [_src(1, "规则库共 35 条规则，另有检索层。")]
        v = verify_claims(claims, sources)[0]
        assert v.status == SUPPORTED
        assert v.citation_mismatch is False
        assert v.cited == [1]


# ── 场景 2：不支撑 ────────────────────────────────────────────

class TestUnsupported:
    def test_claim_with_no_support(self):
        claims = ["灵枢支持 128 种编程语言的自动重构。"]
        sources = [_src(1, "灵枢提供多引擎搜索与网页抓取能力。")]
        v = verify_claims(claims, sources)[0]
        assert v.status == UNSUPPORTED
        assert v.evidence == []

    def test_missing_number_never_earns_supported(self):
        """文本词面高度重叠但关键数字（50）在源里找不到 —— 绝不升级为 supported。

        规则版只做词面近似，数字是它唯一能抓住的"可证伪锚点"；锚点对不上时
        下压到 partial 并说明原因，而不是替模型断言"有源"。
        """
        claims = ["熔断阈值为 50 次连续失败后打开。"]
        sources = [_src(1, "熔断阈值支持配置，连续失败后打开熔断器。")]
        v = verify_claims(claims, sources)[0]
        assert v.status == PARTIAL
        assert "数字" in v.notes

    def test_no_sources_at_all(self):
        assert verify_claims(["任何一句话"], [])[0].status == UNSUPPORTED


# ── 场景 3：部分支撑 ──────────────────────────────────────────

class TestPartial:
    def test_partial_overlap(self):
        """claim 的多数要点在源里找不到 → partial（不是 supported，也不是全否）。"""
        claims = ["灵枢使用 FastAPI 提供 OpenAI 兼容接口，并在 8788 端口暴露 Prometheus 指标。"]
        sources = [_src(1, "灵枢使用 FastAPI 提供 OpenAI 兼容接口。")]
        v = verify_claims(claims, sources)[0]
        assert v.status == PARTIAL
        assert v.evidence and v.evidence[0].source_id == 1
        assert 0 < v.evidence[0].score < 1


# ── 场景 4：引用错配 ──────────────────────────────────────────

class TestCitationMismatch:
    def test_cited_source_does_not_support_but_other_does(self):
        """claim 引 [1]，但支撑它的是源 2 —— 引用错配，即使内容确实有源。"""
        claims = ["规则库共 35 条规则 [1]"]
        sources = [
            _src(1, "灵枢提供多引擎搜索与网页抓取。"),
            _src(2, "规则库共 35 条规则，含中文 2-gram 检索层。"),
        ]
        v = verify_claims(claims, sources)[0]
        assert v.citation_mismatch is True
        assert v.cited == [1]
        assert v.status == SUPPORTED, "内容本身有源（源 2），但引用编号指错了"
        assert v.evidence[0].source_id == 2

    def test_cited_source_does_not_support_and_nobody_does(self):
        claims = ["规则库共 35 条规则 [1]"]
        sources = [_src(1, "灵枢提供多引擎搜索与网页抓取。")]
        v = verify_claims(claims, sources)[0]
        assert v.status == UNSUPPORTED
        assert v.citation_mismatch is True
        assert v.cited == [1]

    def test_cited_source_missing_entirely(self):
        """引用了不存在的源编号 [9] → 错配（且不抛异常）。"""
        claims = ["规则库共 35 条规则 [9]"]
        sources = [_src(1, "规则库共 35 条规则。")]
        v = verify_claims(claims, sources)[0]
        assert v.citation_mismatch is True
        assert v.cited == [9]

    def test_correct_citation_of_multiple_sources(self):
        claims = ["搜索走 SearXNG [1]，抓取有 Playwright 兜底 [2]"]
        sources = [
            _src(1, "多引擎搜索：SearXNG 优先。"),
            _src(2, "网页抓取，Playwright 兜底。"),
        ]
        v = verify_claims(claims, sources)[0]
        assert v.cited == [1, 2]
        assert v.citation_mismatch is False


# ── 引用解析 / 批量接口 / 判定器可替换 ────────────────────────

class TestInterface:
    def test_extract_citations_forms(self):
        assert extract_citations("A [1] B [来源 2] C [3,4] D [5-6]") == [1, 2, 3, 4, 5, 6]
        assert extract_citations("无引用") == []

    def test_batch_and_order_preserved(self):
        claims = ["规则库共 35 条规则，覆盖求职与显存教训。", "没有源支撑的句子：支持 128 种语言重构。"]
        sources = [_src(1, "规则库共 35 条规则，覆盖求职与显存教训，含检索层。")]
        verdicts = verify_claims(claims, sources)
        assert [v.claim for v in verdicts] == claims
        assert [v.status for v in verdicts] == [SUPPORTED, UNSUPPORTED]

    def test_verdict_is_structured_and_serializable(self):
        v = verify_claims(["规则库共 35 条规则。"], [_src(1, "规则库共 35 条规则。")])[0]
        assert isinstance(v, Verdict)
        d = v.to_dict()
        assert set(("claim", "status", "evidence", "method", "cited", "citation_mismatch")) <= set(d)
        assert d["status"] in (SUPPORTED, PARTIAL, UNSUPPORTED)

    def test_judge_is_replaceable(self):
        """判定器可替换：传入自定义 judge 后由它出结果（生成/判分离）。"""
        class AlwaysPartial:
            name = "stub-judge"

            def judge(self, claim, sources):
                return Verdict(claim=claim, status=PARTIAL, evidence=[], method=self.name)

        v = verify_claims(["任意 claim"], [_src(1, "无关文本")], judge=AlwaysPartial())[0]
        assert v.status == PARTIAL
        assert v.method == "stub-judge"

    def test_rule_judge_metadata_is_stable(self):
        judge = RuleBasedJudge()
        assert judge.name
        spec = rule_judge_spec()
        assert spec.stamp == rule_judge_spec().stamp, "同一判据 → 稳定戳（judge 冻结用）"
        v = verify_claims(["规则库共 35 条规则。"], [_src(1, "规则库共 35 条规则。")], judge=judge)[0]
        assert v.method == judge.name

    def test_llm_judge_hook_does_not_call_network(self):
        """LLM judge 只暴露 prompt 构造 / 响应解析钩子，本身不发请求。"""
        prompt = LLMJudge.build_prompt("规则库共 35 条规则 [1]", [_src(1, "规则库共 35 条规则。")])
        assert "规则库共 35 条规则" in prompt
        assert "supported" in prompt and "partial" in prompt and "unsupported" in prompt

        verdict = LLMJudge.parse_response(
            '{"status": "supported", "source_id": 1, "snippet": "规则库共 35 条规则。"}',
            "规则库共 35 条规则 [1]",
        )
        assert verdict.status == SUPPORTED
        assert verdict.method == "llm-judge"

    def test_judge_returning_none_falls_back_to_rules(self):
        class Abstain:
            name = "abstain"

            def judge(self, claim, sources):
                return None

        v = verify_claims(["规则库共 35 条规则。"], [_src(1, "规则库共 35 条规则。")], judge=Abstain())[0]
        assert v.status == SUPPORTED
        assert v.method == RuleBasedJudge.name


# ── 接现有管线：吃 report_formatter 的 {N: url} 映射 ───────────

class TestPipelineWiring:
    def test_exported_from_safety_package(self):
        from agent_harness.core.safety import verify_claims as pkg_verify, Verdict as pkg_verdict
        assert pkg_verify is verify_claims
        assert pkg_verdict is Verdict

    def test_consumes_report_formatter_source_map(self):
        from agent_harness.apps.research.pipeline.report_formatter import _format_citations

        _, source_map = _format_citations("规则库共 35 条规则 [https://a.com]")
        assert source_map == {1: "https://a.com"}
        sources = sources_from_citation_map(source_map, {1: "规则库共 35 条规则，含检索层。"})

        v = verify_claims(["规则库共 35 条规则 [1]"], sources)[0]
        assert v.status == SUPPORTED
        assert v.citation_mismatch is False

    def test_source_without_original_text_is_not_assumed_ok(self):
        """只有 URL、没抓到正文 → 一律不支撑（宁缺毋滥，不假装验过）。"""
        sources = sources_from_citation_map({1: "https://a.com"})
        v = verify_claims(["规则库共 35 条规则 [1]"], sources)[0]
        assert v.status == UNSUPPORTED
        assert v.citation_mismatch is True
