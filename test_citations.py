"""Test the _format_citations function and integration."""
import sys
sys.path.insert(0, r"C:\Users\zwq\agent-harness")

from src.agent_harness.pipeline.report_formatter import _format_citations, generate_report_html

passed = 0

# Test 1: Basic [url] conversion
text = "Some discovery from example page [https://example.com] and another [https://example2.org]"
result, source_map = _format_citations(text)
assert result == "Some discovery from example page [1] and another [2]", f"FAIL: {result}"
assert source_map == {1: "https://example.com", 2: "https://example2.org"}
print("Test 1 PASS - Basic [url] conversion")
passed += 1

# Test 2: Deduplication
text = "First [https://example.com] and again [https://example.com]"
result, source_map = _format_citations(text)
assert result == "First [1] and again [1]", f"FAIL: {result}"
assert source_map == {1: "https://example.com"}
print("Test 2 PASS - Deduplication")
passed += 1

# Test 3: No URLs in brackets (no-op)
text = "Regular text with no URLs and some [source 1] markers"
result, source_map = _format_citations(text)
assert result == text, f"FAIL: should be unchanged"
assert source_map == {}
print("Test 3 PASS - No [url] patterns")
passed += 1

# Test 4: Search tool format
text = "DeepSeek releases new model: enhanced reasoning capabilities [https://www.deepseek.com/blog/new-model]"
result, source_map = _format_citations(text)
assert "[1]" in result and "[https://" not in result, f"FAIL: {result}"
print("Test 4 PASS - Search tool format")
passed += 1

# Test 5: Integration with generate_report_html (no [url] patterns)
html = generate_report_html("Test Report", "## Section 1\nSome content here\n\n## Section 2\nMore content")
assert "<title>Test Report</title>" in html
assert "<h1>Test Report</h1>" in html
print(f"Test 5 PASS - generate_report_html integration ({len(html)} chars)")
passed += 1

# Test 6: Integration with [url] patterns - should now have 参考来源 section
html2 = generate_report_html("Citation Test", "Found this [https://example.com] and that [https://test.org]")
assert "<title>Citation Test</title>" in html2
assert "参考来源" in html2, f"FAIL: 参考来源 not found in HTML"
assert "example.com" in html2
assert "test.org" in html2
# The [url] should be converted to [N] superscript in body
assert "https://example.com" not in html2.split("<div class=\"section\">")[0], "URL should not appear in body"
print(f"Test 6 PASS - Report with [url] citations (参考来源 found, {len(html2)} chars)")
passed += 1

# Test 7: Mixed [url] and [来源 N] patterns
text = "Discovery [https://site1.com] according to [来源 2] and also [https://site2.com]"
result, source_map = _format_citations(text)
assert "[1]" in result and "[来源 2]" in result and "[2]" in result, f"FAIL: {result}"
assert 1 in source_map and 2 in source_map
print("Test 7 PASS - Mixed [url] and [来源 N] patterns")
passed += 1

print(f"\n{'='*40}")
print(f"All {passed}/7 tests PASSED")
