"""Basic placeholder tests for template app."""

from tools.tools import demo_tool
from agents.agent import TemplateAgent


def test_demo_tool():
    """Test the demo tool returns expected structure."""
    result = demo_tool("hello")
    assert result["tool"] == "demo_tool"
    assert result["input"] == "hello"
    assert "Echo" in result["output"]


def test_template_agent():
    """Test the template agent returns expected structure."""
    agent = TemplateAgent(name="TestAgent")
    import asyncio

    result = asyncio.run(agent.run({"key": "value"}))
    assert result["agent"] == "TestAgent"
    assert result["status"] == "ok"
    assert "value" in result["result"]
