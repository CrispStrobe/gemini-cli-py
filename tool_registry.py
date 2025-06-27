#
# File: tool_registry.py
# Revision: 10
# Description: Final version. get_declarations is updated to serve
# different toolsets based on a specified mode.
#
import logging
from typing import List, Literal

from config import Config
from tools.base import Tool
from tools.core_tools import ShellTool, ReadFileTool, WriteFileTool
from tools.find_tools import ListDirectoryTool, GlobTool, GrepTool
from tools.edit_tool import ReplaceInFileTool
from tools.web_search import WebSearchTool
from tools.memory_tool import MemoryTool

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
            ListDirectoryTool, GlobTool, GrepTool,
            ReplaceInFileTool, WebSearchTool,
            MemoryTool
        ]
        for tool_class in tool_classes:
            tool_instance = tool_class(self._config)
            self._tools[tool_instance.name] = tool_instance
        logging.info(f"Registered {len(self._tools)} tools: {list(self._tools.keys())}")


    def get_tool(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def get_declarations(self) -> List[dict]:
        """
        Generates the list of all tool definitions.
        """
        function_declarations = []
        for tool in self._tools.values():
            if tool.schema.get("parameters"):
                function_declarations.append(tool.schema)
        
        if function_declarations:
            logging.debug(f"Providing {len(function_declarations)} function tool declarations.")
            return [{"functionDeclarations": function_declarations}]
        return []