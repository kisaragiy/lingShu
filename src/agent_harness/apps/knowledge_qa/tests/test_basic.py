"""Basic placeholder tests for knowledge_qa app."""

from tools.tools import demo_tool
from agents.agent import Knowledge_QaAgent


def test_demo_tool():
    """Test the demo tool returns expected structure."""
    result = demo_tool("hello")
    assert result["tool"] == "demo_tool"
    assert result["input"] == "hello"
    assert "Echo" in result["output"]


def test_knowledge_qa_agent():
    """Test the knowledge_qa agent returns expected structure."""
    agent = Knowledge_QaAgent(name="TestAgent")
    import asyncio

    result = asyncio.run(agent.run({"key": "value"}))
    assert result["agent"] == "TestAgent"
    assert result["status"] == "ok"
    assert "value" in result["result"]
