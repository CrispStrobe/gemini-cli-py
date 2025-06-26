#
# File: tools/core_tools.py
# Revision: 4 (Fixes ShellTool constructor and execution context)
#
import asyncio
import logging
from pathlib import Path

from .base import Tool
from config import Config
from services.file_discovery_service import FileDiscoveryService

class ShellTool(Tool):
    """A tool for executing shell commands."""
    # *** FIX: Added constructor to accept the config object ***
    def __init__(self, config: Config):
        self._config = config

    @property
    def name(self) -> str: return "shell"
    @property
    def description(self) -> str: return "Executes a shell command."
    @property
    def schema(self) -> dict:
        return {"name": self.name,"description": self.description,"parameters": {"type": "OBJECT","properties": {"command": {"type": "STRING","description": "The command to execute."}},"required": ["command"],},}

    async def execute(self, command: str) -> dict:
        logging.info(f"Executing shell command: {command}")
        try:
            # *** FIX: Executes the command in the correct project directory ***
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._config.get_target_dir() 
            )
            stdout, stderr = await process.communicate()
            return {"stdout": stdout.decode('utf-8', 'ignore'),"stderr": stderr.decode('utf-8', 'ignore'),"returncode": process.returncode}
        except Exception as e:
            return {"error": str(e)}


class ReadFileTool(Tool):
    """A tool for reading the contents of a file."""
    def __init__(self, config: Config):
        self._root_dir = config.get_target_dir()
        self._file_service = config.get_file_service()
    @property
    def name(self) -> str: return "read_file"
    @property
    def description(self) -> str: return "Reads the content of a specified file."
    @property
    def schema(self) -> dict:
        return {"name": self.name,"description": self.description,"parameters": {"type": "OBJECT","properties": {"path": {"type": "STRING","description": "The absolute or relative path to the file."}},"required": ["path"],},}

    async def execute(self, path: str) -> dict:
        logging.info(f"Reading file: {path}")
        try:
            # Resolve path relative to the root directory for safety
            file_path = self._root_dir / path
            if not file_path.resolve().is_relative_to(self._root_dir.resolve()):
                 return {"error": f"Path traversal detected. Access denied."}
            if self._file_service.is_ignored(file_path):
                 return {"error": f"File is ignored by git or gemini ignore rules: {path}"}
            if not file_path.is_file():
                return {"error": f"Path is not a file: {path}"}
            content = file_path.read_text(encoding='utf-8', errors='ignore')
            return {"content": content}
        except Exception as e:
            return {"error": str(e)}

class WriteFileTool(Tool):
    """A tool for writing content to a file."""
    def __init__(self, config: Config):
        self._root_dir = config.get_target_dir()
        self._file_service = config.get_file_service()
    @property
    def name(self) -> str: return "write_file"
    @property
    def description(self) -> str: return "Writes content to a file, overwriting it."
    @property
    def schema(self) -> dict:
        return {"name": self.name,"description": self.description,"parameters": {"type": "OBJECT","properties": {"path": {"type": "STRING","description": "The path of the file to write to."},"content": {"type": "STRING","description": "The content to write."}},"required": ["path", "content"],},}
    
    async def execute(self, path: str, content: str) -> dict:
        logging.info(f"Writing to file: {path}")
        try:
            file_path = self._root_dir / path
            if not file_path.resolve().is_relative_to(self._root_dir.resolve()):
                return {"error": f"Path traversal detected. Access denied."}
            if self._file_service.is_ignored(file_path):
                return {"error": f"File is ignored by git or gemini ignore rules: {path}"}
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding='utf-8')
            return {"success": True, "message": f"Wrote {len(content)} characters to {path}"}
        except Exception as e:
            return {"error": str(e)}