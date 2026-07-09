"""Report Formatter — generates professional formal reports from agent analysis.

Takes raw chat analysis content, re-processes it through the multi-agent
to add source citations, proper formatting, and generates a standalone HTML file.
"""

import json
import os
import re
import time
from pathlib import Path
from datetime import datetime

REPORTS_DIR = Path(os.environ.get(
    "HARNESS_REPORTS_DIR",
    os.path.expanduser("~/.agent-harness/reports"),
))


def _ensure():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)


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


def generate_report_html(title: str, content: str, sources: list[dict] = None) -> str:
    """Generate a professional HTML report with embedded CSS and source citations.

    Args:
        title: Report title
        content: Markdown-ish content from the agent (may contain [来源 N] markers)
        sources: Optional list of {url, title} for citations

    Returns:
        Complete HTML string ready to view/print
    """
    now = datetime.now().strftime("%Y年%m月%d日")
    sources = sources or []

    # Auto-detect and extract URLs from content if no explicit sources provided
    if not sources:
        sources = _extract_sources_from_text(content)

    # Link source markers in text
    linked_content = _link_sources(content, sources)

    # Format the main content (simple markdown → HTML conversion)
    body_html = _markdown_to_html(linked_content)

    # Build sources section with anchor IDs + metadata
    from datetime import datetime as _dt
    _now_str = _dt.now().strftime("%Y-%m-%d")
    sources_html = ""
    if sources:
        items = ""
        for i, s in enumerate(sources, 1):
            url = s.get("url", "")
            title_text = s.get("title", url)
            source_type = s.get("type", "web")
            type_icon = {"news": "📰", "paper": "📄", "official": "🏛️", "web": "🌐"}.get(source_type, "🌐")
            access_date = s.get("access_date", _now_str)
            items += '<li id="source-%d" class="source-item">' % i
            items += '<span class="source-type">%s</span> ' % type_icon
            items += '<a href="%s" target="_blank" rel="noopener">%s</a>' % (url, title_text)
            items += '<span class="source-meta"> · %s · %s</span>' % (source_type, access_date)
            items += '</li>'
        sources_html = """
        <div class="section">
            <h2>📎 参考来源</h2>
            <ol class="sources">%s</ol>
        </div>""" % items

    # ─── Build table of contents ───
    toc_items = []
    seen_headers = set()
    for line in content.split("\n"):
        line_s = line.strip()
        if line_s.startswith("## ") or line_s.startswith("### "):
            h_text = line_s.lstrip("#").strip()
            if h_text and h_text not in seen_headers:
                seen_headers.add(h_text)
                anchor = h_text.lower().replace(" ", "-").replace("（","").replace("）","").replace("，","").replace("、","-")[:40]
                level = "toc-h3" if line_s.startswith("### ") else "toc-h2"
                toc_items.append('<li class="%s"><a href="#%s">%s</a></li>' % (level, anchor, h_text))

    toc_html = ""
    if len(toc_items) > 2:
        toc_html = """
        <div class="toc">
            <h2 class="toc-title">📑 目录</h2>
            <ol class="toc-list">%s</ol>
        </div>""" % "\n".join(toc_items)

    # Add anchors to headers in body_html
    seen_headers = set()
    def _anchor_header(m):
        h_text = m.group(2)
        if h_text in seen_headers:
            return m.group(0)
        seen_headers.add(h_text)
        anchor = h_text.lower().replace(" ", "-").replace("（","").replace("）","").replace("，","").replace("、","-")[:40]
        return '<%s id="%s">%s</%s>' % (m.group(1), anchor, h_text, m.group(1))

    body_html = re.sub(r'<(h[23])>(.*?)</\1>', _anchor_header, body_html)

    # Word count + reading time
    word_count = len(content.replace("\n", ""))
    read_time = max(1, word_count // 300)

    # Build sources section with anchor IDs + metadata
    from datetime import datetime as _dt
    _now_str = _dt.now().strftime("%Y-%m-%d")
    sources_html = ""
    if sources:
        items = ""
        for i, s in enumerate(sources, 1):
            url = s.get("url", "")
            title_text = s.get("title", url)
            source_type = s.get("type", "web")
            type_icon = {"news": "📰", "paper": "📄", "official": "🏛️", "web": "🌐"}.get(source_type, "🌐")
            access_date = s.get("access_date", _now_str)
            items += '<li id="source-%d" class="source-item">' % i
            items += '<span class="source-type">%s</span> ' % type_icon
            items += '<a href="%s" target="_blank" rel="noopener">%s</a>' % (url, title_text)
            items += '<span class="source-meta"> · %s · %s</span>' % (source_type, access_date)
            items += '</li>'
        sources_html = """
        <div class="section">
            <h2>📎 参考来源</h2>
            <ol class="sources">%s</ol>
        </div>""" % items

    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>%s</title>
<style>
  @page { margin: 2.2cm 2.5cm; size: A4; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', sans-serif;
    color: #1e293b; background: #f1f5f9; line-height: 1.75;
    padding: 40px 24px; max-width: 960px; margin: 0 auto;
  }
  .report-container {
    background: #ffffff; border-radius: 16px; box-shadow: 0 4px 24px rgba(0,0,0,0.06);
    padding: 48px 56px; margin-bottom: 24px;
  }
  .report-header {
    border-bottom: 3px solid #1e293b; padding-bottom: 28px; margin-bottom: 36px;
  }
  .report-header h1 {
    font-size: 30px; font-weight: 700; color: #0f172a; margin-bottom: 12px;
    line-height: 1.3; letter-spacing: 0.5px;
  }
  .report-meta {
    font-size: 13px; color: #64748b; display: flex; flex-wrap: wrap; gap: 16px;
  }
  .report-meta span { display: inline-flex; align-items: center; gap: 4px; }
  .tag {
    display: inline-block; padding: 2px 10px; border-radius: 6px;
    background: #eef2ff; color: #2563eb; font-size: 11px; font-weight: 500;
  }
  /* ─── TOC ─── */
  .toc { background: #f8fafc; border-radius: 12px; padding: 24px 28px; margin-bottom: 32px; border: 1px solid #e2e8f0; }
  .toc-title { font-size: 16px; font-weight: 600; color: #0f172a; margin-bottom: 12px; border: none; padding: 0; }
  .toc-list { list-style: none; padding: 0; margin: 0; }
  .toc-list li { margin-bottom: 6px; font-size: 14px; }
  .toc-list li a { color: #2563eb; text-decoration: none; }
  .toc-list li a:hover { text-decoration: underline; }
  .toc-list li.toc-h3 { padding-left: 20px; font-size: 13px; color: #475569; }
  /* ─── Headings ─── */
  h2 {
    font-size: 22px; font-weight: 600; color: #0f172a;
    margin: 36px 0 16px; padding-bottom: 8px;
    border-bottom: 2px solid #e2e8f0;
  }
  h2:first-of-type { margin-top: 0; }
  h3 {
    font-size: 17px; font-weight: 600; color: #1e293b;
    margin: 24px 0 10px;
  }
  /* ─── Body ─── */
  p { margin-bottom: 14px; font-size: 14.5px; color: #334155; }
  table {
    width: 100%%; border-collapse: separate; border-spacing: 0;
    margin: 20px 0; font-size: 13.5px; border-radius: 10px; overflow: hidden;
    border: 1px solid #e2e8f0;
  }
  th {
    background: #f1f5f9; color: #0f172a; padding: 10px 14px;
    text-align: left; font-weight: 600; font-size: 13px;
    border-bottom: 2px solid #e2e8f0;
  }
  td { padding: 10px 14px; border-bottom: 1px solid #f1f5f9; }
  tr:last-child td { border-bottom: none; }
  tr:nth-child(even) td { background: #f8fafc; }
  ul, ol { margin: 8px 0 16px 20px; font-size: 14.5px; color: #334155; }
  li { margin-bottom: 6px; }
  strong { color: #0f172a; }
  .highlight {
    background: #f8fafc; padding: 18px 22px; border-radius: 10px;
    border-left: 4px solid #2563eb; margin: 20px 0;
    font-size: 14px; color: #1e293b;
  }
  /* ─── Sources ─── */
  .sources { margin: 12px 0; }
  .sources li { margin-bottom: 10px; }
  .source-item { padding: 4px 0; }
  .source-type { font-size: 14px; margin-right: 4px; }
  .source-meta { font-size: 11px; color: #94a3b8; }
  .sources a { color: #2563eb; text-decoration: none; word-break: break-all; font-size: 13px; }
  .sources a:hover { text-decoration: underline; }
  sup.cite { font-size: 11px; vertical-align: super; line-height: 0; margin: 0 2px; }
  sup.cite a { color: #2563eb; text-decoration: none; font-weight: 600; }
  sup.cite a:hover { text-decoration: underline; }
  /* ─── Footer / Print bar ─── */
  .footer { text-align: center; font-size: 12px; color: #94a3b8; padding: 24px 0; }
  .print-bar { text-align: center; margin-bottom: 12px; }
  .print-btn {
    background: #2563eb; color: #fff; border: none; padding: 8px 20px;
    border-radius: 8px; font-size: 13px; cursor: pointer; transition: background .15s;
  }
  .print-btn:hover { background: #1d4ed8; }
  @media print {
    body { background: #fff; padding: 0; }
    .report-container { box-shadow: none; border-radius: 0; padding: 40px; break-inside: avoid; }
    .print-bar { display: none; }
    .footer { position: running(footer); }
    h2, h3 { break-after: avoid; }
    table { break-inside: avoid; }
  }
</style>
</head>
<body>
<div class="report-container">
  <div class="report-header">
    <h1>%s</h1>
    <div class="report-meta">
      <span>📅 %s</span>
      <span>📄 %s 字 · 约 %s 分钟阅读</span>
      <span>⚡ 灵枢 AI 调研助手</span>
    </div>
  </div>
  %s
  %s
  %s
  %s
  %s
</div>
<div class="print-bar"><button class="print-btn" onclick="window.print()">🖨️ 打印 / 导出 PDF</button></div>
<div class="footer">由灵枢 (LingShu Agent) 自动生成 · 数据基于公开搜索结果 · 仅供参考</div>
<script>document.addEventListener('keydown',function(e){if(e.key==='p'&&(e.ctrlKey||e.metaKey))window.print()});</script>
</body>
</html>""" % (title, title, now, "%d" % word_count, "%d" % read_time,
            toc_html, body_html, sources_html, "<div class='tags'>%s</div>" % " ".join('<span class="tag">%s</span>' % t for t in tags) if tags else "")

    return html


def save_formal_report(title: str, html: str, tags: list[str] = None, source_session: str = "", owner_id: str = "") -> dict:
    """Save a formal report as HTML file and return metadata."""
    _ensure()
    timestamp = int(time.time())
    report_id = "formal_%d_%s" % (timestamp, _slugify(title)[:20])
    filename = "%s.html" % report_id
    filepath = REPORTS_DIR / filename

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

    for line in lines:
        stripped = line.strip()

        # Table row
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            # Check if it's a header separator
            if all(set(c) <= set(":-") for c in cells):
                continue
            if not in_table:
                html.append("<table>")
                in_table = True
            html.append("<tr><td>" + "</td><td>".join(cells) + "</td></tr>")
            continue
        else:
            if in_table:
                html.append("</table>")
                in_table = False

        # Headers
        if stripped.startswith("### "):
            html.append("<h3>%s</h3>" % stripped[4:])
        elif stripped.startswith("## "):
            html.append("<h2>%s</h2>" % stripped[3:])
        elif stripped.startswith("# "):
            html.append("<h2>%s</h2>" % stripped[2:])
        # Unordered list
        elif stripped.startswith("- ") or stripped.startswith("* "):
            content = stripped[2:]
            if not in_list:
                html.append("<ul>")
                in_list = True
            html.append("<li>%s</li>" % content)
        # Ordered list
        elif re.match(r"^\d+[.、]\s", stripped):
            content = re.sub(r"^\d+[.、]\s", "", stripped)
            if not in_list:
                html.append("<ol>")
                in_list = True
            html.append("<li>%s</li>" % content)
        else:
            if in_list:
                html.append("</ul>")
                in_list = False
            # Empty line = paragraph break
            if not stripped:
                pass
            # Bold wrapping
            elif stripped.startswith("**") and stripped.endswith("**"):
                html.append("<p><strong>%s</strong></p>" % stripped[2:-2])
            else:
                html.append("<p>%s</p>" % stripped)

    if in_table:
        html.append("</table>")
    if in_list:
        html.append("</ul>")

    return "\n".join(html)


def _slugify(text: str) -> str:
    text = re.sub(r'[^\w\s-]', '', text.lower())
    text = re.sub(r'[-\s]+', '_', text)
    return text.strip('_')[:30]
