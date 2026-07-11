"""Demo tool functions for knowledge_qa app.

TODO: Replace with your actual tools.
"""


def demo_tool(text: str) -> dict:
    """A demo tool that echoes back the input.

    Args:
        text: A string to echo.

    Returns:
        Dictionary with the echoed text.
    """
    return {
        "tool": "demo_tool",
        "input": text,
        "output": f"Echo: {text}",
    }
