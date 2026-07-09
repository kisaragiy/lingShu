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

    # Build sources section with anchor IDs
    sources_html = ""
    if sources:
        items = ""
        for i, s in enumerate(sources, 1):
            title_text = s.get("title", s["url"])
            items += '<li id="source-%d"><a href="%s" target="_blank" rel="noopener">%s</a></li>' % (
                i, s["url"], title_text
            )
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
  @page { margin: 2cm; size: A4; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', sans-serif;
    color: #1a1a2e; background: #f8f9fc; line-height: 1.7;
    padding: 40px; max-width: 900px; margin: 0 auto;
  }
  .report-container {
    background: #fff; border-radius: 12px; box-shadow: 0 2px 20px rgba(0,0,0,0.08);
    padding: 48px 56px; margin-bottom: 24px;
  }
  .report-header {
    border-bottom: 3px solid #7c5cfc; padding-bottom: 24px; margin-bottom: 32px;
  }
  .report-header h1 { font-size: 28px; font-weight: 700; color: #1a1a2e; margin-bottom: 8px; }
  .report-meta { font-size: 13px; color: #888; }
  .report-meta span { margin-right: 16px; }
  h2 { font-size: 20px; font-weight: 600; color: #7c5cfc; margin: 32px 0 16px; padding-bottom: 8px; border-bottom: 1px solid #e8e8f0; }
  h3 { font-size: 16px; font-weight: 600; color: #2d2d44; margin: 24px 0 12px; }
  p { margin-bottom: 12px; font-size: 14px; color: #333; }
  table {
    width: 100%%; border-collapse: collapse; margin: 16px 0; font-size: 13px;
    border-radius: 8px; overflow: hidden;
  }
  th { background: #7c5cfc; color: #fff; padding: 10px 14px; text-align: left; font-weight: 600; }
  td { padding: 10px 14px; border-bottom: 1px solid #e8e8f0; }
  tr:last-child td { border-bottom: none; }
  tr:nth-child(even) td { background: #f8f6ff; }
  ul, ol { margin: 8px 0 16px 20px; font-size: 14px; color: #333; }
  li { margin-bottom: 6px; }
  strong { color: #1a1a2e; }
  .highlight { background: #f0ecfe; padding: 16px 20px; border-radius: 8px; border-left: 4px solid #7c5cfc; margin: 16px 0; }
  .sources { margin: 12px 0; }
  .sources a { color: #7c5cfc; text-decoration: none; word-break: break-all; font-size: 13px; }
  .sources a:hover { text-decoration: underline; }
  .footer { text-align: center; font-size: 12px; color: #aaa; padding: 24px; }
  .tag {
    display: inline-block; padding: 2px 10px; border-radius: 4px;
    background: #f0ecfe; color: #7c5cfc; font-size: 11px; margin-right: 4px;
  }
  /* Citation superscripts */
  sup.cite {
    font-size: 11px; vertical-align: super; line-height: 0;
    margin: 0 1px;
  }
  sup.cite a {
    color: #2563eb; text-decoration: none; font-weight: 600;
  }
  sup.cite a:hover {
    text-decoration: underline;
  }
  @media print {
    body { background: #fff; padding: 0; }
    .report-container { box-shadow: none; border-radius: 0; padding: 40px; }
    .footer { page-break-after: always; }
  }
</style>
</head>
<body>
<div class="report-container">
  <div class="report-header">
    <h1>%s</h1>
    <div class="report-meta">
      <span>📅 生成日期：%s</span>
      <span>⚡ 灵枢 AI 调研助手</span>
    </div>
  </div>
  %s
  %s
</div>
<div class="footer">由灵枢 (LingShu Agent) 自动生成 · 数据基于公开搜索结果 · 仅供参考</div>
</body>
</html>""" % (title, title, now, body_html, sources_html)

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
