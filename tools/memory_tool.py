#
# File: tools/memory_tool.py
# Revision: 2
# Description: Fixes an AttributeError by importing the USER_SETTINGS_DIR
# constant directly from the config module.
#

import logging
from pathlib import Path
from datetime import datetime

from .base import Tool
from .tool_io import ToolConfirmationOutcome
from config import Config, USER_SETTINGS_DIR # <-- Import the constant directly

class MemoryTool(Tool):
    """
    A tool to save a piece of information ("fact") to a global
    GEMINI.md file for long-term memory.
    """
    def __init__(self, config: Config):
        # The config object is passed but not used directly for this path.
        # We use the imported constant instead.
        self._memory_file = USER_SETTINGS_DIR / "GEMINI.md"

    @property
    def name(self) -> str: return "save_memory"
    @property
    def description(self) -> str: return "Saves a 'fact' to your long-term memory to be used in future sessions."

    @property
    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "fact": {"type": "STRING", "description": "The piece of information or fact to remember."}
                },
                "required": ["fact"],
            }
        }

    async def should_confirm_execute(self, fact: str) -> dict | None:
        """Saving to a global memory file always requires confirmation."""
        return {
            "type": "memory_write",
            "fact": fact,
            "path": str(self._memory_file)
        }

    async def execute(self, fact: str) -> dict:
        logging.info(f"Saving fact to global memory file: {self._memory_file}")
        try:
            self._memory_file.parent.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            with open(self._memory_file, 'a', encoding='utf-8') as f:
                f.write(f"\n# Fact saved on {timestamp}\n")
                f.write(f"- {fact}\n")

            return {"success": True, "message": f"Successfully saved fact to {self._memory_file}."}
        except IOError as e:
            error_message = f"Failed to write to memory file: {e}"
            logging.error(error_message)
            return {"error": error_message}