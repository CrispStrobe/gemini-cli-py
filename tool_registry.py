#
# File: tool_registry.py
# Revision: 3 (Registers all file system tools)
#
import logging
from config import Config
from tools.base import Tool
from tools.core_tools import ShellTool, ReadFileTool, WriteFileTool
from tools.find_tools import ListDirectoryTool, GlobTool, GrepTool

class ToolRegistry:
    """Discovers, registers, and provides access to all available tools."""
    def __init__(self, config: Config):
        self._config = config
        self._tools: dict[str, Tool] = {}
        self._register_core_tools()

    def _register_core_tools(self):
        logging.info("Registering core tools...")
        tool_classes = [
            ShellTool, ReadFileTool, WriteFileTool,
            ListDirectoryTool, GlobTool, GrepTool
        ]
        for tool_class in tool_classes:
            # Pass the config object to each tool's constructor
            tool_instance = tool_class(self._config)
            if tool_instance.name in self._tools:
                logging.warning(f"Tool '{tool_instance.name}' is already registered. Overwriting.")
            self._tools[tool_instance.name] = tool_instance
        logging.info(f"Registered {len(self._tools)} tools: {list(self._tools.keys())}")

    def get_tool(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def get_declarations(self) -> list[dict]:
        return [tool.schema for tool in self._tools.values()]