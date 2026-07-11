"""Placeholder agent class for template app.

TODO: Replace with your actual agent implementation.
"""


class TemplateAgent:
    """A simple placeholder agent for the template app."""

    def __init__(self, name: str = "TemplateAgent"):
        self.name = name

    async def run(self, input_data: dict) -> dict:
        """Run the agent logic.

        Args:
            input_data: Dictionary with input parameters.

        Returns:
            Dictionary with results.
        """
        return {
            "agent": self.name,
            "status": "ok",
            "result": f"Processed: {input_data}",
        }
