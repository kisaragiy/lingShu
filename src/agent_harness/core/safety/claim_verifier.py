"""独立溯源验证 agent —— claim ↔ source 一致性判定（生成/评判分离）。

痛点
----
管线现状只有**格式化**：``report_formatter._format_citations`` 把 URL 变成 ``[N]``
标号，仅此而已。报告里"这句话到底有没有被哪个源支撑"、"引的 [N] 是不是真的
支撑它"，**无人校验** —— 引用编号可以错配，数字可以无源，读者毫无察觉。
格式化 ≠ 溯源。

落点（本模块）
--------------
一个**只判定、不生成**的判定器：输入「claims 列表 + 引用来源原文」，输出每条
claim 的结构化裁定：

* ``supported``   —— 有源支撑（词面/数字/实体均对得上）；
* ``partial``     —— 只被部分覆盖（或关键数字/实体未在源中出现）；
* ``unsupported`` —— 无源支撑；
* ``citation_mismatch`` —— 独立的**错配位**：claim 声明的 ``[N]`` 编号所对应的源
  **并不支撑**它（哪怕另一个源支撑它，或根本没有源支撑它）。

三条设计原则
------------
1. **生成/评判分离** —— 本模块不生成任何内容，只对已有 claim + 已抓到的源做裁定；
   不给"重写得更像有来源"的能力，避免评判者顺手改答案。
2. **判定器可替换** —— :class:`ClaimJudge` 是协议；默认 :class:`RuleBasedJudge`
   是**纯规则版**（标准库、离线、零 GPU）；:class:`LLMJudge` 是给 LLM 的**接线钩子**
   （只构造 prompt / 解析响应，**本模块自身不发起任何网络调用**，由调用方注入
   ``complete`` 回调时才成立）。
3. **可冻结** —— :func:`rule_judge_spec` 复用 ``judge_registry`` 的稳定戳，
   判据一改戳就变，量尺不会悄悄漂。

诚实边界（规则版做不到什么）
----------------------------
规则版判的是**词面重合近似**，不是语义蕴含。它能可靠抓到：整句无源、关键数字
无源、引用编号指错。它**不能**可靠判定：同义改写（"不支持"vs"拒绝"）、否定与
条件（源说"仅在 A 时成立"，claim 说"总是成立"）、以及数字之外的逻辑冲突。
凡词面近似不可判的，一律下压到 ``partial``/``unsupported``，**绝不升级为
supported** —— 假阳性（把无源说成有源）比假阴性更有害。

本模块不 import ``apps``（架构方向 core ← apps），不触网，不写全局态。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

from agent_harness.core.judge_registry import JudgeSpec

__all__ = [
    "SUPPORTED",
    "PARTIAL",
    "UNSUPPORTED",
    "STATUSES",
    "CLAIM_VERIFIER_JUDGE",
    "SUPPORT_THRESHOLD",
    "PARTIAL_THRESHOLD",
    "Evidence",
    "Verdict",
    "ClaimJudge",
    "RuleBasedJudge",
    "LLMJudge",
    "extract_citations",
    "verify_claims",
    "verify_one",
    "score_overlap",
    "sources_from_citation_map",
    "rule_judge_spec",
]

# ── 裁定级 ────────────────────────────────────────────────────
SUPPORTED = "supported"
PARTIAL = "partial"
UNSUPPORTED = "unsupported"
STATUSES = (SUPPORTED, PARTIAL, UNSUPPORTED)

# 词面覆盖率阈值（claim token 中被源命中的比例）。
SUPPORT_THRESHOLD = 0.60
PARTIAL_THRESHOLD = 0.30

CLAIM_VERIFIER_JUDGE = "claim-verifier-rules"

# ── 分词（中文 2-gram + 拉丁词 + 数字；零外部依赖） ────────────
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_LATIN = re.compile(r"[a-zA-Z][a-zA-Z0-9_+#.\-]{1,}")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
# 引用编号：[1] / [来源 1] / [3,4] / [5-6]；含 "://" 的是 URL 不是引用。
_BRACKET = re.compile(r"\[([^\[\]]{1,40})\]")


def _tokens(text: str) -> set[str]:
    """切 token：CJK 连续段取 2-gram，拉丁词与数字各自成 token。"""
    if not text:
        return set()
    low = text.lower()
    out: set[str] = set()
    for run in _CJK_RUN.findall(low):
        if len(run) == 1:
            out.add(run)
        else:
            out.update(run[i:i + 2] for i in range(len(run) - 1))
    out.update(_LATIN.findall(low))
    out.update(_NUMBER.findall(low))
    return out


def _numbers(text: str) -> set[str]:
    return set(_NUMBER.findall(text))


def _latin_entities(text: str) -> set[str]:
    """拉丁实体（如 SearXNG / FastAPI / OpenAI）—— 大小写不敏感，但要求长度 ≥ 3。"""
    return {m.lower() for m in _LATIN.findall(text) if len(m) >= 3 and not m.isdigit()}


def score_overlap(claim: str, source_text: str) -> float:
    """claim 的 token 被 source 命中的比例（0~1）。纯词面近似，无语义。"""
    c = _tokens(claim)
    if not c:
        return 0.0
    s = _tokens(source_text or "")
    return len(c & s) / len(c)


# ── 引用编号解析 ──────────────────────────────────────────────

def extract_citations(text: str) -> list[int]:
    """抽出 claim 里声明的引用编号（去重升序）。支持 ``[1] [来源 2] [3,4] [5-6]``。"""
    found: set[int] = set()
    for inner in _BRACKET.findall(text or ""):
        if "://" in inner:            # [https://...] 是源地址，不是引用编号
            continue
        body = re.sub(r"来源|source", "", inner, flags=re.IGNORECASE)
        if not re.search(r"\d", body):
            continue
        body = re.sub(r"\s+", "", body)
        for part in body.split(","):
            rng = re.fullmatch(r"(\d+)-(\d+)", part)
            if rng:
                lo, hi = int(rng.group(1)), int(rng.group(2))
                if lo <= hi and hi - lo < 100:
                    found.update(range(lo, hi + 1))
            else:
                for num in re.findall(r"\d+", part):
                    found.add(int(num))
    return sorted(found)


def _strip_citations(text: str) -> str:
    """去掉引用标记 ``[N]`` / ``[来源 N]``（保留 URL 型方括号，那是内容）。"""
    def _sub(m: re.Match) -> str:
        inner = m.group(1)
        if "://" in inner:
            return m.group(0)
        body = re.sub(r"来源|source", "", inner, flags=re.IGNORECASE)
        if not re.search(r"\d", body):
            return m.group(0)
        return " "
    return _BRACKET.sub(_sub, text or "")


def sources_from_citation_map(citation_map: dict[int, str],
                              source_texts: dict[int, str] | None = None) -> list[dict]:
    """把 ``report_formatter._format_citations`` 的 ``{N: url}`` 转成本模块的源列表。

    ``source_texts`` 提供 ``{N: 抓到/召回的正文}``；缺正文的源只能验"编号存在"，
    **不能**验内容一致性（会判成不支撑 —— 宁缺毋滥）。
    """
    texts = source_texts or {}
    out: list[dict] = []
    for sid in sorted(citation_map):
        out.append({
            "id": int(sid),
            "url": citation_map[sid],
            "text": texts.get(sid, ""),
        })
    return out


# ── 数据结构 ──────────────────────────────────────────────────

@dataclass(frozen=True)
class Evidence:
    """命中证据：哪个源、覆盖了多少、命中的片段。"""

    source_id: int
    score: float
    snippet: str = ""
    url: str = ""

    def to_dict(self) -> dict:
        return {"source_id": self.source_id, "score": self.score,
                "snippet": self.snippet, "url": self.url}


@dataclass(frozen=True)
class Verdict:
    """一条 claim 的裁定。"""

    claim: str
    status: str
    evidence: list[Evidence] = field(default_factory=list)
    method: str = ""
    cited: list[int] = field(default_factory=list)
    citation_mismatch: bool = False
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "claim": self.claim,
            "status": self.status,
            "evidence": [e.to_dict() for e in self.evidence],
            "method": self.method,
            "cited": list(self.cited),
            "citation_mismatch": self.citation_mismatch,
            "notes": self.notes,
        }


# ── 判定器协议 ────────────────────────────────────────────────

@runtime_checkable
class ClaimJudge(Protocol):
    """判定器协议：``judge(claim, sources) -> Verdict | None``。

    返回 ``None`` = 弃权，调用方回退到规则版（judge 没好用时不要静默通过）。
    """

    name: str

    def judge(self, claim: str, sources: list[dict]) -> Verdict | None:  # pragma: no cover
        ...


def _source_id(src: dict, fallback: int) -> int:
    sid = src.get("id")
    try:
        return int(sid)
    except (TypeError, ValueError):
        return fallback


def _source_text(src: dict) -> str:
    for key in ("text", "content", "body", "snippet"):
        val = src.get(key)
        if val:
            return str(val)
    return ""


def _snippet(source_text: str, claim: str, limit: int = 160) -> str:
    """在源文里截一段能证明命中的窗口；命不中就取开头。"""
    text = source_text or ""
    if not text:
        return ""
    hit = None
    for tok in sorted(_tokens(claim), key=len, reverse=True):
        pos = text.lower().find(tok)
        if pos >= 0:
            hit = pos
            break
    if hit is None:
        return text[:limit]
    start = max(0, hit - 40)
    return text[start:start + limit]


class RuleBasedJudge:
    """纯规则判定器：词面覆盖 + 数字/实体门 + 引用错配位。

    规则（宁缺毋滥）：
      * 覆盖 ≥ ``SUPPORT_THRESHOLD`` 且 claim 中所有数字都能在源文找到、且无
        缺失的拉丁实体 → ``supported``；
      * 覆盖 ≥ ``SUPPORT_THRESHOLD`` 但数字/实体对不上 → ``partial``（词面对上但
        **可证伪锚点**没对上，不升级）；
      * 覆盖 ≥ ``PARTIAL_THRESHOLD`` → ``partial``；
      * 其余 → ``unsupported``（证据为空）。
    """

    name = CLAIM_VERIFIER_JUDGE

    def judge(self, claim: str, sources: list[dict]) -> Verdict:
        return self._verify(claim, sources or [])

    # -- 内部 --

    def _score_one(self, claim: str, source_text: str) -> tuple[float, str]:
        """返回 (分数, 降级原因)；原因非空表示不得判 supported。

        评分前先剥掉引用编号 ``[N]``：编号是"声明"而非"断言内容"，留着会让
        ``[1]`` 里的 "1" 被当成待证数字，把本来有源的句子误压成 partial。
        """
        claim = _strip_citations(claim)
        cov = score_overlap(claim, source_text)
        if cov < PARTIAL_THRESHOLD:
            return cov, ""
        missing_nums = _numbers(claim) - _numbers(source_text)
        if missing_nums:
            return cov, f"关键数字未被来源覆盖: {sorted(missing_nums)}"
        missing_ent = _latin_entities(claim) - _tokens(source_text)
        if missing_ent:
            return cov, f"实体未被来源覆盖: {sorted(missing_ent)}"
        return cov, ""

    def _verify(self, claim: str, sources: list[dict]) -> Verdict:
        cited = extract_citations(claim)
        scored: list[tuple[float, str, int, dict, str]] = []  # score, reason, sid, src, text
        for idx, src in enumerate(sources, start=1):
            sid = _source_id(src, idx)
            text = _source_text(src)
            cov, reason = self._score_one(claim, text)
            scored.append((cov, reason, sid, src, text))
        scored.sort(key=lambda t: t[0], reverse=True)

        evidence = [
            Evidence(source_id=sid, score=round(cov, 4), snippet=_snippet(text, claim),
                     url=str(src.get("url", "") or ""))
            for cov, _reason, sid, src, text in scored
            if cov >= PARTIAL_THRESHOLD
        ][:3]

        notes = ""
        if not sources:
            status = UNSUPPORTED
            notes = "无引用来源可比对"
        elif not scored or scored[0][0] < PARTIAL_THRESHOLD:
            status = UNSUPPORTED
        else:
            best_cov, best_reason = scored[0][0], scored[0][1]
            if best_cov >= SUPPORT_THRESHOLD and not best_reason:
                status = SUPPORTED
            else:
                status = PARTIAL
                notes = best_reason or "仅被部分覆盖"

        mismatch = self._detect_mismatch(claim, sources, cited)
        if mismatch and status == SUPPORTED and not notes:
            notes = "内容有源，但 claim 声明的引用编号不支撑它"
        return Verdict(claim=claim, status=status, evidence=evidence,
                       method=self.name, cited=cited,
                       citation_mismatch=mismatch, notes=notes)

    def _detect_mismatch(self, claim: str, sources: list[dict], cited: list[int]) -> bool:
        """claim 里每个引用编号，其指向的源是否真的支撑**紧邻它的那段话**。

        无引用 → 不存在错配。任一引用编号所指的源都不支撑其对应片段（含编号
        不存在的情况）→ 错配。
        """
        if not cited:
            return False
        by_id = {_source_id(src, i + 1): src for i, src in enumerate(sources)}
        for seg_text, sids in self._cited_segments(claim, cited):
            ok = False
            for sid in sids:
                src = by_id.get(sid)
                if src is None:
                    continue  # 引用了不存在的源编号
                cov, reason = self._score_one(seg_text, _source_text(src))
                if cov >= SUPPORT_THRESHOLD and not reason:
                    ok = True
                    break
            if not ok:
                return True
        return False

    @staticmethod
    def _cited_segments(claim: str, cited: list[int]) -> list[tuple[str, list[int]]]:
        """把 claim 切成「片段 → 它声明的引用编号」。

        约定：紧跟某个编号**之前**的那段文字由该编号背书（"陈述 [N]" 的常见写法）。
        过短（<4 字）的片段视作连接词，不参与错配判定。
        """
        segs: list[tuple[str, list[int]]] = []
        prev_end = 0
        for m in _BRACKET.finditer(claim):
            inner = m.group(1)
            if "://" in inner:
                continue
            nums = extract_citations(m.group(0))
            if not nums:
                continue
            seg = claim[prev_end:m.start()].strip()
            prev_end = m.end()
            if len(seg) >= 4:
                segs.append((seg, nums))
        return segs or [(claim, cited)]


class LLMJudge:
    """LLM 判定钩子（**本类不发起网络请求**）。

    用法（将来接本机/远端模型时）：

        judge = LLMJudge(complete=lambda prompt: my_llm(prompt))
        verify_claims(claims, sources, judge=judge)

    ``complete`` 由调用方注入 —— 本模块只负责 :meth:`build_prompt` 与
    :meth:`parse_response` 两端，模型选择/预算/缓存都留在调用方（便于 judge
    冻结与成本归属）。本任务不接远程 LLM，故不提供默认 ``complete``。
    """

    name = "llm-judge"

    def __init__(self, complete: Callable[[str], str]) -> None:
        if not callable(complete):
            raise TypeError("LLMJudge 需要 callable complete(prompt) -> str")
        self._complete = complete

    @staticmethod
    def build_prompt(claim: str, sources: list[dict]) -> str:
        blocks = []
        for i, src in enumerate(sources, start=1):
            sid = _source_id(src, i)
            blocks.append(f"[{sid}] url={src.get('url', '')}\n{_source_text(src)[:2000]}")
        return (
            "你是溯源判定器：只判定下面这条 claim 是否被给定来源支撑，不要改写 claim。\n"
            "可能的状态：supported（有源明确支撑）/ partial（仅部分被支撑或关键数字/"
            "实体对不上）/ unsupported（无源支撑）。\n"
            "只输出 JSON：{\"status\": \"...\", \"source_id\": <int|null>, "
            "\"snippet\": \"<来源原文片段>\"}。\n\n"
            f"claim: {claim}\n\n来源：\n" + "\n\n".join(blocks)
        )

    @staticmethod
    def parse_response(response: str, claim: str) -> Verdict:
        """解析模型输出；解析不了 → 弃权（unsupported + 说明），**不猜 supported**。"""
        data: dict[str, Any] = {}
        m = re.search(r"\{.*\}", response or "", re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
            except (ValueError, TypeError):
                data = {}
        status = str(data.get("status", "")).strip().lower()
        if status not in STATUSES:
            return Verdict(claim=claim, status=UNSUPPORTED, method=LLMJudge.name,
                           notes="judge 响应无法解析，按不支撑处理")
        evidence: list[Evidence] = []
        sid = data.get("source_id")
        if sid is not None:
            try:
                evidence.append(Evidence(source_id=int(sid), score=1.0 if status == SUPPORTED else 0.5,
                                         snippet=str(data.get("snippet", ""))))
            except (TypeError, ValueError):
                pass
        return Verdict(claim=claim, status=status, evidence=evidence, method=LLMJudge.name)

    def judge(self, claim: str, sources: list[dict]) -> Verdict:
        out = self._complete(self.build_prompt(claim, sources))
        return self.parse_response(out, claim)


# ── 对外入口 ──────────────────────────────────────────────────

def verify_one(claim: str, sources: list[dict],
               judge: ClaimJudge | None = None) -> Verdict:
    """判定单条 claim。``judge`` 弃权（返回 None）时回退规则版。"""
    if judge is not None:
        verdict = judge.judge(claim, sources)
        if verdict is not None:
            return verdict
    return RuleBasedJudge().judge(claim, sources)


def verify_claims(claims: list[str], sources: list[dict],
                  judge: ClaimJudge | None = None) -> list[Verdict]:
    """批量判定：逐条 claim 对照来源原文，输出结构化裁定（顺序与输入一致）。

    Args:
        claims: 报告里的断言句（可含 ``[N]`` 引用编号）。
        sources: 来源列表，每项建议 ``{"id": int, "url": str, "text": str}``；
                 缺 ``id`` 时按出现顺序编号。
        judge: 可选判定器；``None`` → 规则版。传 LLM judge 即可升级为语义判定
               （本模块不含任何默认远程调用）。
    """
    return [verify_one(claim, sources, judge) for claim in claims]


def rule_judge_spec() -> JudgeSpec:
    """规则判定器的 :class:`JudgeSpec` —— 阈值一变戳就变（judge 冻结/漂移检测用）。"""
    return JudgeSpec(
        name=CLAIM_VERIFIER_JUDGE,
        version="1",
        model="",
        criteria={
            "support_threshold": SUPPORT_THRESHOLD,
            "partial_threshold": PARTIAL_THRESHOLD,
            "signals": ["char_2gram_overlap", "number_backing", "latin_entity_backing"],
            "citation_mismatch": "per_cited_segment",
        },
        notes="规则版：词面近似判定，不判语义蕴含（同义改写/否定/条件不覆盖）。",
    )
