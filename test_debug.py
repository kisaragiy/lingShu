"""Debug test 6."""
import sys
sys.path.insert(0, r"C:\Users\zwq\agent-harness")

from src.agent_harness.pipeline.report_formatter import generate_report_html, _format_citations

# Debug _format_citations
content = "Found this [https://example.com] and that [https://test.org]"
formatted, source_map = _format_citations(content)
print("Formatted content:")
print(formatted)
print()
print("Source map:", source_map)

# Debug generate_report_html
html = generate_report_html("Citation Test", content)
print(f"\nHTML length: {len(html)}")
print(f"Has <title>Citation Test</title>: {'<title>Citation Test</title>' in html}")
print(f"Has 参考来源: {'参考来源' in html}")
print(f"Has example.com: {'example.com' in html}")
print(f"Has [1]: {bool('href=\"#source-1\"' in html or '>[1]</' in html)}")

# Show the first 500 and last 500 chars
print("\n=== FIRST 500 ===")
print(html[:500])
print("\n=== LAST 500 ===")
print(html[-500:])
