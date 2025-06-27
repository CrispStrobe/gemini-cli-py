#
# File: tools/web_search.py
# Revision: 2
# Description: Adds a constructor to conform to the ToolRegistry's instantiation pattern.
#

from .base import Tool
from config import Config

class WebSearchTool(Tool):
    """
    A special tool that signals the model to use its internal Google Search capability.
    """
    def __init__(self, config: Config):
        # This tool doesn't need config, but we accept it to match the
        # registration pattern in ToolRegistry.
        self._config = config

    @property
    def name(self) -> str: return "Google Search"
    @property
    def description(self) -> str: return "Performs a Google search to find up-to-date information or answer questions."

    @property
    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
        }

    async def should_confirm_execute(self, **kwargs) -> dict | None:
        """Web search is a safe, read-only operation and does not need confirmation."""
        return None

    async def execute(self, **kwargs) -> dict:
        """
        This tool's execution is handled by the Gemini backend. This method
        is a formality and should not be called directly.
        """
        return {
            "note": "Web search results are automatically integrated into the model's response."
        }