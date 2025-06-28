#
# File: tools/core_tools.py
# Revision: 7
# Description: 
# The ShellTool is significantly enhanced to be more robust and secure.
# - Uses `create_subprocess_exec` instead of `shell=True` for better security.
# - Streams stdout/stderr to detect binary content and prevent terminal corruption.
# - Returns a detailed dictionary of the execution result.
#

import asyncio
import logging
from pathlib import Path
import shlex

from .base import Tool
from .tool_io import ToolConfirmationOutcome
from config import Config

def is_binary(data: bytes, sample_size=1024) -> bool:
    """Checks if a Buffer is likely binary by testing for NULL bytes."""
    if not data:
        return False
    sample = data[:sample_size]
    return b'\x00' in sample

class ShellTool(Tool):
    """A tool for executing shell commands in a controlled manner."""
    def __init__(self, config: Config):
        self._config = config
        self._whitelist = set()

    @property
    def name(self) -> str: return "shell"
    @property
    def description(self) -> str: return "Executes a shell command."
    @property
    def schema(self) -> dict:
        return {"name": self.name,"description": self.description,"parameters": {"type": "OBJECT","properties": {"command": {"type": "STRING","description": "The command to execute."}},"required": ["command"],},}

    def _get_command_root(self, command: str) -> str | None:
        """Extracts the base command for whitelisting (e.g., 'ls' from 'ls -l')."""
        try:
            return shlex.split(command.strip())[0]
        except (ValueError, IndexError):
            return None

    async def should_confirm_execute(self, command: str) -> dict | None:
        """Checks if the shell command needs user confirmation."""
        command_root = self._get_command_root(command)
        if command_root and command_root in self._whitelist:
            logging.info(f"Command '{command_root}' is whitelisted, skipping confirmation.")
            return None # No confirmation needed

        return {
            "type": "exec",
            "command": command,
            "root_command": command_root
        }

    async def handle_confirmation_response(self, root_command: str, outcome: ToolConfirmationOutcome):
        """Handles the user's response to a confirmation request."""
        if outcome == ToolConfirmationOutcome.PROCEED_ALWAYS:
            logging.info(f"Whitelisting command: {root_command}")
            if root_command:
                self._whitelist.add(root_command)

    async def execute(self, command: str) -> dict:
        logging.info(f"Executing shell command: {command}")
        try:
            # Use shlex to safely split the command for create_subprocess_exec
            if platform.system() == "Windows":
                 # On Windows, shell=True is often more reliable for complex commands.
                 # We accept the reduced security for better cross-platform compatibility here.
                 proc = await asyncio.create_subprocess_shell(
                     command,
                     stdout=asyncio.subprocess.PIPE,
                     stderr=asyncio.subprocess.PIPE,
                     cwd=self._config.get_target_dir()
                 )
            else:
                 # On Unix-like systems, use shlex.split for better security.
                 cmd_parts = shlex.split(command)
                 proc = await asyncio.create_subprocess_exec(
                     *cmd_parts,
                     stdout=asyncio.subprocess.PIPE,
                     stderr=asyncio.subprocess.PIPE,
                     cwd=self._config.get_target_dir()
                 )

            stdout_chunks = []
            stderr_chunks = []

            # Stream output to detect binary content
            binary_detected = False
            async def read_stream(stream, chunk_list):
                nonlocal binary_detected
                while True:
                    chunk = await stream.read(1024)
                    if not chunk:
                        break
                    if not binary_detected:
                        if is_binary(chunk):
                            binary_detected = True
                        chunk_list.append(chunk)

            await asyncio.gather(
                read_stream(proc.stdout, stdout_chunks),
                read_stream(proc.stderr, stderr_chunks)
            )

            await proc.wait()

            stdout_bytes = b"".join(stdout_chunks)
            stderr_bytes = b"".join(stderr_chunks)

            if binary_detected:
                return {
                    "stdout": f"[Binary output detected ({len(stdout_bytes)} bytes)]",
                    "stderr": f"[Binary output detected in stderr ({len(stderr_bytes)} bytes)]",
                    "returncode": proc.returncode,
                    "error": None
                }
            else:
                 return {
                    "stdout": stdout_bytes.decode('utf-8', 'ignore'),
                    "stderr": stderr_bytes.decode('utf-8', 'ignore'),
                    "returncode": proc.returncode,
                    "error": None
                }

        except FileNotFoundError as e:
            return {"error": f"Command not found: {e.filename}", "returncode": 127}
        except Exception as e:
            return {"error": str(e), "returncode": 1}


class ReadFileTool(Tool):
    # ... (Unchanged)
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
    async def should_confirm_execute(self, **kwargs) -> dict | None:
        return None # Reading a file is considered safe.
    async def execute(self, path: str) -> dict:
        logging.info(f"Reading file: {path}")
        try:
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
    # ... (Unchanged)
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
    async def should_confirm_execute(self, **kwargs) -> dict | None:
        # Writing/overwriting a file should require confirmation.
        return {"type": "write", "path": kwargs.get("path")}
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