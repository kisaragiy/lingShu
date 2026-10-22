"""Report Formatter — generates professional formal reports from agent analysis.

Takes raw chat analysis content, re-processes it through the multi-agent
to add source citations, proper formatting, and generates a standalone HTML file.
"""

import html as _html
import logging
import re
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _reports_dir() -> Path:
    """报告目录的**唯一来源** = report_store.REPORTS_DIR（调用时解析，不再各自 export 常量）。

    此前本模块自带一份同默认值的 import-time 常量，与 store 的那份独立绑定：
    任何只重定向其一的场景（测试 fixture、按 env 定制部署）都会变成
    "写进 A 目录、按 B 目录读" —— 报告存了打不开，索引里留下悬空条目。
    单测里最直观：只 patch store 一处，.html 仍会落进真实 ~/.agent-harness。
    """
    from agent_harness.apps.research.pipeline.report_store import REPORTS_DIR as _rd
    return _rd


def _ensure():
    _reports_dir().mkdir(parents=True, exist_ok=True)


# ─── 字数统计（ENG-31）───
# 旧实现 `len(content)` 把 markdown 标记、标点、表格分隔符全算成"字"，中文报告页脚虚高。
# 口径：中文每字计 1，英文/数字连续串每串计 1 词；markdown 语法符号、纯标点、空白、URL 均不计。
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[.\-_'][A-Za-z0-9]+)*")
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_CODE_RE = re.compile(r"`+([^`]*)`+")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+")


def _count_words(text: str) -> int:
    """中文按字、英文/数字按词统计；markdown 语法与标点/空白不计入（ENG-31）。"""
    if not text:
        return 0
    s = _MD_IMAGE_RE.sub(" ", text)   # 图片整体不计（alt 不是正文）
    s = _MD_LINK_RE.sub(r"\1", s)     # 链接只算锚文本，URL 不计
    s = _MD_CODE_RE.sub(r"\1", s)     # 行内代码只算内容，反引号不计
    s = _URL_RE.sub(" ", s)           # 裸 URL 不计
    return len(_CJK_RE.findall(s)) + len(_WORD_RE.findall(s))


def _extract_sources_from_text(text: str) -> list[dict]:
    """Extract source URLs from text content.

    Detects patterns like:
      - http(s)://... URLs in the text
      - [来源 N] markers followed by URLs
      - URLs in brackets at end of lines
    """
    sources = []
    seen = set()

    # Pattern 1: explicit URLs
    urls = re.findall(r'https?://[^\s\[\]()<>"]+', text)
    for url in urls:
        # Clean trailing punctuation
        url = url.rstrip(".,;:!?")
        if url not in seen and _is_valid_url(url):
            seen.add(url)
            sources.append({"url": url, "title": url.rsplit("/", 1)[-1][:50]})

    return sources


def _format_citations(text: str) -> tuple[str, dict[int, str]]:
    """Convert [url] patterns in text to numbered [N] citations.

    Detects URLs wrapped in square brackets (e.g. "snippet [https://example.com]")
    produced by the search tool, replaces each with [N], and builds a source
    mapping: {N: url}. Deduplicates identical URLs.

    Returns:
        (modified_text, source_map) where source_map maps N → url
    """
    seen_urls: dict[str, int] = {}
    url_order: list[str] = []
    counter = [0]

    def _replace(m):
        raw = m.group(1) or m.group(2)
        if not raw:
            return m.group(0)
        url = raw.rstrip(".,;:!?)")
        if url in seen_urls:
            num = seen_urls[url]
        else:
            counter[0] += 1
            num = counter[0]
            seen_urls[url] = num
            url_order.append(url)
        return f"[{num}]"

    # Match [http...] or [https...] forms
    result = re.sub(r'\[(https?://[^\]]+)\]', _replace, text)

    # Build source map maintaining insertion order
    source_map: dict[int, str] = {}
    for url in url_order:
        source_map[seen_urls[url]] = url

    return result, source_map


def _is_valid_url(url: str) -> bool:
    """Basic URL validity check."""
    return url.startswith("http") and len(url) > 15


def _link_sources(text: str, sources: list[dict]) -> str:
    """Convert [来源 N] markers to HTML sup tags with anchor links.

    Also adds a source index at the end of the content from search results.
    """
    if not sources:
        return text

    # Replace [来源 N] → <sup class="cite"><a href="#source-N">[N]</a></sup>
    def _replace_source(m):
        num = m.group(1)
        return f'<sup class="cite"><a href="#source-{num}">[{num}]</a></sup>'

    text = re.sub(r'\[来源\s*(\d+)\]', _replace_source, text)
    return text


def _esc(value) -> str:
    """转义一切用户/模型可控文本。**必须**在任何 HTML 拼装之前调用。

    存储型 XSS 成因：`_markdown_to_html` 原样插值 content，而 .html 报告经
    `GET /v1/reports/{id}` 以 text/html 同源返回、CSP 又放行 inline →
    裸 `<script>` / `onerror=` 可读走 localStorage 的 JWT。
    """
    return _html.escape("" if value is None else str(value), quote=True)


_ALLOWED_URL_SCHEMES = ("http://", "https://", "mailto:")


def _safe_href(url: str) -> str:
    """URL 协议允许列表：只放行 http(s)/mailto/站内相对路径，其余返回 ""。

    `html.escape` 挡的是**标记注入**，对**协议语义**无效——`javascript:` 不是标记，
    但它会进 href 并在同源点击时执行（读走 localStorage 的 JWT）。sources[].url
    由请求体直通（api.py:683→685），故必须在渲染出口这**单点**判定。
    """
    u = "" if url is None else str(url)
    # 先剥掉控制字符/空白夹带（`java\tscript:`、`jav\nascript:`）
    compact = re.sub(r"[\x00-\x20\x7f]", "", u).strip().lower()
    if not compact:
        return ""
    if compact.startswith(_ALLOWED_URL_SCHEMES) or compact.startswith(("#", "/")):
        return u.strip()
    return ""


# ─── ENG-29/32/33: 核验三态（provenance → 三态汇总块数据） ───

def _verdict_three_state(report: str, source_map: dict, provenance: list) -> dict:
    """把 claim_verifier 裁定 + 数字级核验映射成报告三态汇总块数据。

    返回 {"checked","verified","not_found","unknown","unknown_reasons",
          "provenance_count","not_found_items"}，供 ``_render_factcheck_html`` 渲染。
    目标场景（如金融研报类尽调，仅作可证伪的演示场景）。
    """
    from agent_harness.core.graph.graph_multi import (
        _normalize_number_tokens, _normalize_sources_numbers)

    provenance = provenance or []
    source_map = source_map or {}
    reasons: list[str] = []
    prov_by_url = {p.get("url", ""): p.get("snippet", "") for p in provenance
                   if isinstance(p, dict)}
    source_texts = {n: prov_by_url.get(url, "") for n, url in source_map.items()}

    # ── 数字级三态（ENG-32/34 口径：报告数字 vs 来源 snippet 数字集合）──
    report_nums = sorted(_normalize_number_tokens(report))
    prov_nums: set = set()
    for p in provenance:
        if isinstance(p, dict):
            # 标题 + snippet 都算来源正文（「2023 年报」这类标题数字不误报）
            prov_nums |= _normalize_number_tokens(
                f"{p.get('title', '')} {p.get('snippet', '')}")
    not_found_items = [n for n in report_nums if n not in prov_nums]

    verified = not_found = 0
    if provenance and report_nums:
        not_found = len(not_found_items)
        verified = len(report_nums) - not_found
    elif provenance and not report_nums:
        reasons.append("报告正文无可核对数字，未做数字核验")

    # ── 断言级裁定（claim_verifier：词面覆盖 + 数字锚点；不判语义等价）──
    try:
        from agent_harness.core.safety.claim_verifier import (
            RuleBasedJudge, sources_from_citation_map, verify_claims)
        sources = sources_from_citation_map(source_map, source_texts=source_texts)
        sources = _normalize_sources_numbers(sources)   # ENG-34：中文单位同口径
        claims = [s for s in re.split(r"(?<=[。！？\n])", report or "") if len(s.strip()) > 15]
        if claims and sources:
            verdicts = verify_claims(claims, sources, judge=RuleBasedJudge())
            supported = sum(1 for v in verdicts if v.status == "supported")
            unsupported = sum(1 for v in verdicts if v.status in ("partial", "unsupported"))
            if not not_found_items:
                verified, not_found = supported, unsupported
    except Exception as exc:   # 校验异常绝不误判 → 落 v-unknown + 显式原因
        reasons.append(f"校验流程异常（{exc}），已跳过以避免误判")

    unknown = 0
    if not provenance:
        unknown = 1
        reasons.append("未检索到可用来源正文（provenance 为空），无法比对")

    return {
        "checked": bool(provenance),
        "verified": verified,
        "not_found": not_found,
        "unknown": unknown,
        "unknown_reasons": reasons,
        "provenance_count": len(provenance),
        "not_found_items": not_found_items[:12],
    }


def _render_factcheck_html(factcheck: dict | None) -> str:
    """生成 factcheck-summary 块（三态计数 + 未找到依据的数字 + 未核验原因）。

    插在「📎 参考来源」块之前；动态内容全部过 ``_esc``。
    """
    if not factcheck:
        return ""
    vr = _esc(factcheck.get("verified", 0))
    mf = _esc(factcheck.get("not_found", 0))
    un = _esc(factcheck.get("unknown", 0))
    n_src = _esc(factcheck.get("provenance_count", 0))

    miss_lis = ""
    items = factcheck.get("not_found_items") or []
    if items:
        from agent_harness.core.graph.graph_multi import _fmt_num
        inner = "、".join(_esc(_fmt_num(n)) for n in items)
        miss_lis = f'<p class="v v-miss">❌ 未找到依据的数字：{inner}</p>'

    reason_lis = "".join(
        f"<li>{_esc(r)}</li>" for r in (factcheck.get("unknown_reasons") or [])
    )
    reasons_html = f"<ul>{reason_lis}</ul>" if reason_lis else ""
    return f"""
        <div class="section factcheck-summary">
            <h2>🔍 事实核验说明</h2>
            <p>本报告关键数字已对照 <b>{n_src}</b> 条一手来源（工具名 + URL + 抓取时间）核验：
               <span class="v v-ok">✅ 已核验 {vr} 条</span> ·
               <span class="v v-miss">❌ 未找到依据 {mf} 条</span> ·
               <span class="v v-unknown">⚠️ 未核验 {un} 条</span>。</p>
            {miss_lis}
            <p class="factcheck-hint">未找到依据的断言请勿直接采信；未核验项原因见下。</p>
            {reasons_html}
        </div>"""


def generate_report_html(title: str, content: str, sources: list[dict] = None,
                         tags: list[str] = None, provenance: list | None = None,
                         factcheck: dict | None = None) -> str:
    """Generate a professional HTML report with embedded CSS and source citations.

    Args:
        title: Report title
        content: Markdown-ish content from the agent (may contain [来源 N] markers)
        sources: Optional list of {url, title} for citations
        tags: Optional list of tag strings for the report
        provenance: Optional 一手来源记录（ENG-29：工具名 + URL + 抓取时间 + snippet）；
            给了就渲染「事实核验说明」三态块
        factcheck: Optional 预先算好的三态汇总（见 ``_verdict_three_state``）；
            缺省且给了 provenance 时自动计算

    Returns:
        Complete HTML string ready to view/print
    """
    now = datetime.now().strftime("%Y年%m月%d日")
    sources = sources or []
    tags = tags or []
    # ─── 安全：入参（title/content/sources/tags）全部不可信，先转义再拼装 ───
    title = _esc(title)
    tags = [_esc(t) for t in tags]

    # Step 1: Convert [url] patterns to [N] numbered citations
    # Also builds a source_map and a "## 参考来源" section to append
    formatted_content, source_map = _format_citations(content)

    # ─── ENG-29: provenance → 核验三态（无 provenance 时不渲染汇总块，旧行为不变）───
    if factcheck is None and provenance is not None:
        try:
            factcheck = _verdict_three_state(content, source_map, provenance)
        except Exception as exc:      # 渲染路径绝不因核验失败而崩
            factcheck = {"verified": 0, "not_found": 0, "unknown": 1,
                         "unknown_reasons": [f"校验流程异常（{exc}），已跳过以避免误判"],
                         "provenance_count": 0, "not_found_items": []}
    factcheck_html = _render_factcheck_html(factcheck)

    # Step 2: Build reference section from source_map (from [url] → [N] conversion)
    ref_lines = []
    for num in sorted(source_map.keys()):
        url = source_map[num]
        # Try to derive a title from the URL（用独立变量：此前复用 title 覆盖了报告标题，
        # 导致 h1/页面 title 变成最后一个 URL 的路径尾部，且覆盖后不再转义）
        ref_title = url.rsplit("/", 1)[-1][:50] if "/" in url else url
        ref_lines.append(f"[来源 {num}] {ref_title} - {url}")
    if ref_lines:
        formatted_content += "\n\n## 参考来源\n\n" + "\n".join(ref_lines)

    # Step 3: Auto-detect and extract URLs from content if no explicit sources provided
    if not sources:
        sources = _extract_sources_from_text(formatted_content)

    # ─── 安全：唯一转义点，且在 _link_sources 之前 ───
    # <sup class="cite"> 由 _link_sources 在本行之后注入（可信），与用户文本互不污染。
    escaped_content = _esc(formatted_content)

    # Link source markers in text
    linked_content = _link_sources(escaped_content, sources)

    # Format the main content (simple markdown → HTML conversion)
    body_html = _markdown_to_html(linked_content)

    # ─── Build table of contents ───
    toc_items = []
    seen_headers = set()
    for line in escaped_content.split("\n"):
        line_s = line.strip()
        if line_s.startswith("## ") or line_s.startswith("### "):
            h_text = line_s.lstrip("#").strip()
            if h_text and h_text not in seen_headers:
                seen_headers.add(h_text)
                anchor = h_text.lower().replace(" ", "-").replace("（","").replace("）","").replace("，","").replace("、","-")[:40]
                level = "toc-h3" if line_s.startswith("### ") else "toc-h2"
                toc_items.append(f'<li class="{level}"><a href="#{anchor}">{h_text}</a></li>')

    toc_html = ""
    if len(toc_items) > 2:
        toc_html = """
        <div class="toc">
            <h2 class="toc-title">📑 目录</h2>
            <ol class="toc-list">{}</ol>
        </div>""".format("\n".join(toc_items))

    # Add anchors to headers in body_html
    seen_headers = set()
    def _anchor_header(m):
        h_text = m.group(2)
        if h_text in seen_headers:
            return m.group(0)
        seen_headers.add(h_text)
        anchor = h_text.lower().replace(" ", "-").replace("（","").replace("）","").replace("，","").replace("、","-")[:40]
        return f'<{m.group(1)} id="{anchor}">{h_text}</{m.group(1)}>'

    body_html = re.sub(r'<(h[23])>(.*?)</\1>', _anchor_header, body_html)

    # Word count + reading time（ENG-31：中文按字、英文按词，markdown 符号不计）
    word_count = _count_words(content)
    read_time = max(1, word_count // 300)

    # Build sources section with anchor IDs + metadata
    from datetime import datetime as _dt
    _now_str = _dt.now().strftime("%Y-%m-%d")
    sources_html = ""
    if sources:
        items = ""
        for i, s in enumerate(sources, 1):
            raw_url = "" if s.get("url") is None else str(s.get("url"))
            safe_url = _safe_href(raw_url)
            url = _esc(safe_url)            # 属性位：连引号一起转义
            title_text = _esc(s.get("title") or raw_url)
            source_type_raw = s.get("type", "web")
            source_type = _esc(source_type_raw)
            type_icon = {"news": "📰", "paper": "📄", "official": "🏛️", "web": "🌐"}.get(source_type_raw, "🌐")
            access_date = _esc(s.get("access_date", _now_str))
            items += f'<li id="source-{i}" class="source-item">'
            items += f'<span class="source-type">{type_icon}</span> '
            if url:
                items += f'<a href="{url}" target="_blank" rel="noopener noreferrer">{title_text}</a>'
            else:
                # 非允许协议降级为纯文本：绝不进 href（点击不执行）
                items += f'<span class="source-plain">{title_text}</span>'
                items += '<span class="source-meta">（链接已禁用：非 http(s) 协议）</span> '
            items += f'<span class="source-meta"> · {source_type} · {access_date}</span>'
            if factcheck is None:
                # ENG-29 第一层：没有任何 provenance/核验数据时，逐条诚实标注「未独立核验」
                items += '<span class="source-meta"> · <span class="unverified-tag">未独立核验</span></span>'
            items += '</li>'
        sources_html = f"""
        <div class="section">
            <h2>📎 参考来源</h2>
            <ol class="sources">{items}</ol>
        </div>"""

    # factcheck 汇总块插在「📎 参考来源」之前（ENG-33 落位）
    sources_html = factcheck_html + sources_html

    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{}</title>
<style>
  @page {{ margin: 2.2cm 2.5cm; size: A4; }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', sans-serif;
    color: #1e293b; background: #f1f5f9; line-height: 1.75;
    padding: 40px 24px; max-width: 960px; margin: 0 auto;
  }}
  .report-container {{
    background: #ffffff; border-radius: 16px; box-shadow: 0 4px 24px rgba(0,0,0,0.06);
    padding: 48px 56px; margin-bottom: 24px;
  }}
  .report-header {{
    border-bottom: 3px solid #1e293b; padding-bottom: 28px; margin-bottom: 36px;
  }}
  .report-header h1 {{
    font-size: 30px; font-weight: 700; color: #0f172a; margin-bottom: 12px;
    line-height: 1.3; letter-spacing: 0.5px;
  }}
  .report-meta {{
    font-size: 13px; color: #64748b; display: flex; flex-wrap: wrap; gap: 16px;
  }}
  .report-meta span {{ display: inline-flex; align-items: center; gap: 4px; }}
  .tag {{
    display: inline-block; padding: 2px 10px; border-radius: 6px;
    background: #eef2ff; color: #2563eb; font-size: 11px; font-weight: 500;
  }}
  /* ─── TOC ─── */
  .toc {{ background: #f8fafc; border-radius: 12px; padding: 24px 28px; margin-bottom: 32px; border: 1px solid #e2e8f0; }}
  .toc-title {{ font-size: 16px; font-weight: 600; color: #0f172a; margin-bottom: 12px; border: none; padding: 0; }}
  .toc-list {{ list-style: none; padding: 0; margin: 0; }}
  .toc-list li {{ margin-bottom: 6px; font-size: 14px; }}
  .toc-list li a {{ color: #2563eb; text-decoration: none; }}
  .toc-list li a:hover {{ text-decoration: underline; }}
  .toc-list li.toc-h3 {{ padding-left: 20px; font-size: 13px; color: #475569; }}
  /* ─── Headings ─── */
  h2 {{
    font-size: 22px; font-weight: 600; color: #0f172a;
    margin: 36px 0 16px; padding-bottom: 8px;
    border-bottom: 2px solid #e2e8f0;
  }}
  h2:first-of-type {{ margin-top: 0; }}
  h3 {{
    font-size: 17px; font-weight: 600; color: #1e293b;
    margin: 24px 0 10px;
  }}
  /* ─── Body ─── */
  p {{ margin-bottom: 14px; font-size: 14.5px; color: #334155; }}
  table {{
    width: 100%; border-collapse: separate; border-spacing: 0;
    margin: 20px 0; font-size: 13.5px; border-radius: 10px; overflow: hidden;
    border: 1px solid #e2e8f0;
  }}
  th {{
    background: #f1f5f9; color: #0f172a; padding: 10px 14px;
    text-align: left; font-weight: 600; font-size: 13px;
    border-bottom: 2px solid #e2e8f0;
  }}
  td {{ padding: 10px 14px; border-bottom: 1px solid #f1f5f9; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:nth-child(even) td {{ background: #f8fafc; }}
  ul, ol {{ margin: 8px 0 16px 20px; font-size: 14.5px; color: #334155; }}
  li {{ margin-bottom: 6px; }}
  strong {{ color: #0f172a; }}
  .highlight {{
    background: #f8fafc; padding: 18px 22px; border-radius: 10px;
    border-left: 4px solid #2563eb; margin: 20px 0;
    font-size: 14px; color: #1e293b;
  }}
  /* ─── Sources ─── */
  .sources {{ margin: 12px 0; }}
  .sources li {{ margin-bottom: 10px; }}
  .source-item {{ padding: 4px 0; }}
  .source-type {{ font-size: 14px; margin-right: 4px; }}
  .source-meta {{ font-size: 11px; color: #94a3b8; }}
  .sources a {{ color: #2563eb; text-decoration: none; word-break: break-all; font-size: 13px; }}
  .sources a:hover {{ text-decoration: underline; }}
  sup.cite {{ font-size: 11px; vertical-align: super; line-height: 0; margin: 0 2px; }}
  sup.cite a {{ color: #2563eb; text-decoration: none; font-weight: 600; }}
  sup.cite a:hover {{ text-decoration: underline; }}
  /* ─── Fact-check 三态（ENG-29/33） ─── */
  .factcheck-summary {{ background: #f8fafc; border: 1px solid #e2e8f0;
    border-radius: 12px; padding: 16px 20px; margin-bottom: 24px; font-size: 13.5px; }}
  .factcheck-summary h2 {{ font-size: 16px; border: none; margin: 0 0 8px; padding: 0; }}
  .factcheck-summary ul {{ font-size: 13px; color: #475569; }}
  .factcheck-hint {{ font-size: 12px; color: #64748b; margin-top: 6px; }}
  .v {{ padding: 1px 6px; border-radius: 4px; font-size: 11px; }}
  .v-ok {{ color: #15803d; background: #dcfce7; }}
  .v-miss {{ color: #b91c1c; background: #fee2e2; }}
  .v-unknown {{ color: #a16207; background: #fef9c3; }}
  .unverified-tag {{ color: #a16207; background: #fef9c3; padding: 1px 6px;
    border-radius: 4px; font-size: 10.5px; }}
  /* ─── Footer / Print bar ─── */
  .footer {{ text-align: center; font-size: 12px; color: #94a3b8; padding: 24px 0; }}
  .print-bar {{ text-align: center; margin-bottom: 12px; }}
  .print-btn {{
    background: #2563eb; color: #fff; border: none; padding: 8px 20px;
    border-radius: 8px; font-size: 13px; cursor: pointer; transition: background .15s;
  }}
  .print-btn:hover {{ background: #1d4ed8; }}
  @media print {{
    body {{ background: #fff; padding: 0; }}
    .report-container {{ box-shadow: none; border-radius: 0; padding: 40px; break-inside: avoid; }}
    .print-bar {{ display: none; }}
    .footer {{ position: running(footer); }}
    h2, h3 {{ break-after: avoid; }}
    table {{ break-inside: avoid; }}
  }}
</style>
</head>
<body>
<div class="report-container">
  <div class="report-header">
    <h1>{}</h1>
    <div class="report-meta">
      <span>📅 {}</span>
      <span>📄 {} 字 · 约 {} 分钟阅读</span>
      <span>⚡ 灵枢 AI 调研助手</span>
    </div>
  </div>
  {}
  {}
  {}
  {}
</div>
<div class="print-bar"><button class="print-btn" onclick="window.print()">🖨️ 打印 / 导出 PDF</button></div>
<div class="footer">由灵枢 (LingShu Agent) 自动生成 · 数据基于公开搜索结果 · 仅供参考</div>
<script>document.addEventListener('keydown',function(e){{if(e.key==='p'&&(e.ctrlKey||e.metaKey))window.print()}});</script>
</body>
</html>""".format(title, title, now, f"{word_count}", f"{read_time}",
            toc_html, body_html, sources_html, "<div class='tags'>{}</div>".format(" ".join(f'<span class="tag">{t}</span>' for t in tags)) if tags else "")

    return html


def save_formal_report(title: str, html: str, tags: list[str] = None, source_session: str = "", owner_id: str = "") -> dict:
    """Save a formal report as HTML file and return metadata."""
    _ensure()
    timestamp = int(time.time())
    report_id = f"formal_{timestamp}_{_slugify(title)[:20]}"
    filename = f"{report_id}.html"
    filepath = _reports_dir() / filename

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)

    meta = {
        "id": report_id,
        "title": title,
        "created_at": timestamp,
        "tags": tags or [],
        "source_session": source_session,
        "owner_id": owner_id,
        "filename": filename,
        "format": "html",
        "chars": len(html),
    }
    # 入索引：正式报告此前只在磁盘上有文件、无索引条目 → 无 owner 记录，
    # 只能靠直接读目录的路径提供（IDOR 载体）。登记后统一经 store 的
    # get_report(owner_id=...) 访问，所有权在校验点唯一。
    try:
        from agent_harness.apps.research.pipeline.report_store import get_report, index_report
        index_report(meta)
        # 回查：index_report 不抛但也没写进去（静默失败）同样要可见
        if get_report(report_id) is None:
            raise RuntimeError("index_report 未抛异常，但索引中查不到该条目")
    except Exception as exc:                     # noqa: BLE001 — 失败必须可见，不是吞掉
        # 登记失败的症状正是"文件在磁盘上、UI 里全不可见"，且无 owner 记录
        # → 又变回 IDOR 载体。fail-loud：结构化字段 + ERROR 日志，两条通道都留。
        meta["index_error"] = f"{type(exc).__name__}: {exc}"
        logger.error(
            "报告登记入索引失败 id=%s（文件已写盘但 UI 不可见）: %s",
            report_id, exc, exc_info=True,
        )
    return meta


def _markdown_to_html(text: str) -> str:
    """Simple Markdown to HTML conversion for report content.

    Supports:
      - # → h2, ## → h2, ### → h3
      - Tables: | ... |
      - Lists: -, *, 1.
      - Bold: **text**
      - [来源 N] → superscript citation links (already converted by _link_sources)
    """
    lines = text.split("\n")
    html = []
    in_table = False
    in_list = False
    prev_table_row = None   # 暂存上一表格行，等下一行裁决它是表头还是数据行

    for line in lines:
        stripped = line.strip()

        # Table row
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            is_sep = all(set(c) <= set(":-") for c in cells)
            if is_sep:
                # 分隔行 ⟹ 上一行是表头。此前直接 continue，表头被当数据行渲染成 <td>，
                # 表格从此没有表头（ENG-26）。
                if prev_table_row is not None:
                    if not in_table:
                        html.append("<table>")
                        in_table = True
                    html.append("<tr><th>" + "</th><th>".join(prev_table_row) + "</th></tr>")
                    prev_table_row = None
                continue
            # 非分隔行：先刷出暂存的上一行（它没有分隔行跟随 → 是数据行），当前行重新暂存
            if prev_table_row is not None:
                if not in_table:
                    html.append("<table>")
                    in_table = True
                html.append("<tr><td>" + "</td><td>".join(prev_table_row) + "</td></tr>")
            prev_table_row = cells
            continue
        else:
            # 非表格行：先刷出暂存行，再闭合表格
            if prev_table_row is not None:
                if not in_table:
                    html.append("<table>")
                    in_table = True
                html.append("<tr><td>" + "</td><td>".join(prev_table_row) + "</td></tr>")
                prev_table_row = None
            if in_table:
                html.append("</table>")
                in_table = False

        # Headers
        if stripped.startswith("### "):
            html.append(f"<h3>{stripped[4:]}</h3>")
        elif stripped.startswith("## "):
            html.append(f"<h2>{stripped[3:]}</h2>")
        elif stripped.startswith("# "):
            html.append(f"<h2>{stripped[2:]}</h2>")
        # Unordered list
        elif stripped.startswith("- ") or stripped.startswith("* "):
            content = stripped[2:]
            if not in_list:
                html.append("<ul>")
                in_list = True
            html.append(f"<li>{content}</li>")
        # Ordered list
        elif re.match(r"^\d+[.、]\s", stripped):
            content = re.sub(r"^\d+[.、]\s", "", stripped)
            if not in_list:
                html.append("<ol>")
                in_list = True
            html.append(f"<li>{content}</li>")
        else:
            if in_list:
                html.append("</ul>")
                in_list = False
            # Empty line = paragraph break
            if not stripped:
                pass
            # Bold wrapping
            elif stripped.startswith("**") and stripped.endswith("**"):
                html.append(f"<p><strong>{stripped[2:-2]}</strong></p>")
            else:
                html.append(f"<p>{stripped}</p>")

    if prev_table_row is not None:      # 文末残留的最后一行表格
        if not in_table:
            html.append("<table>")
            in_table = True
        html.append("<tr><td>" + "</td><td>".join(prev_table_row) + "</td></tr>")
        prev_table_row = None
    if in_table:
        html.append("</table>")
    if in_list:
        html.append("</ul>")

    return "\n".join(html)


def _slugify(text: str) -> str:
    text = re.sub(r'[^\w\s-]', '', text.lower())
    text = re.sub(r'[-\s]+', '_', text)
    return text.strip('_')[:30]
