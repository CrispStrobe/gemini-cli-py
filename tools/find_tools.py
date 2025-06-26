#
# File: tools/find_tools.py
# Revision: 2 (Upgrades GrepTool to use git grep)
# Description: Contains tools for discovering files and searching content.
#

import asyncio
import logging
import re
from pathlib import Path
from datetime import datetime

from .base import Tool
from config import Config
from services.file_discovery_service import FileDiscoveryService

class ListDirectoryTool(Tool):
    # ... (This class is unchanged)
    def __init__(self, config: Config):
        self._file_service = config.get_file_service()
        self._root_dir = config.get_target_dir()
    @property
    def name(self) -> str: return "list_directory"
    @property
    def description(self) -> str: return "Lists files and subdirectories, respecting ignore rules."
    @property
    def schema(self) -> dict:
        return {"name": self.name,"description": self.description,"parameters": {"type": "OBJECT","properties": {"path": {"type": "STRING","description": "The path to the directory to list."}},"required": ["path"]}}
    async def execute(self, path: str) -> dict:
        try:
            target_path = (self._root_dir / path).resolve()
            if not target_path.is_relative_to(self._root_dir):
                 return {"error": f"Path must be within project root"}
            if not target_path.is_dir():
                return {"error": f"Path is not a directory: {path}"}
            all_entries = list(target_path.iterdir())
            unignored_paths = self._file_service.filter_files(all_entries)
            def sort_key(p: Path): return (not p.is_dir(), p.name.lower())
            unignored_paths.sort(key=sort_key)
            formatted = [f"[DIR] {p.name}" if p.is_dir() else p.name for p in unignored_paths]
            return {"listing": formatted}
        except Exception as e:
            return {"error": str(e)}

class GlobTool(Tool):
    # ... (This class is unchanged)
    def __init__(self, config: Config):
        self._file_service = config.get_file_service()
        self._root_dir = config.get_target_dir()
    @property
    def name(self) -> str: return "glob"
    @property
    def description(self) -> str: return "Finds files matching a glob pattern, sorted by modification time."
    @property
    def schema(self) -> dict:
        return {"name": self.name,"description": self.description,"parameters": {"type": "OBJECT","properties": {"pattern": {"type": "STRING","description": "e.g., 'src/**/*.py'"}},"required": ["pattern"]}}
    async def execute(self, pattern: str) -> dict:
        try:
            all_files = list(self._root_dir.rglob(pattern))
            unignored_files = [f for f in self._file_service.filter_files(all_files) if f.is_file()]
            if not unignored_files: return {"files": []}
            def sort_key(p: Path): return (-p.stat().st_mtime, str(p))
            unignored_files.sort(key=sort_key)
            return {"files": [str(p.relative_to(self._root_dir)) for p in unignored_files]}
        except Exception as e:
            return {"error": str(e)}

class GrepTool(Tool):
    """
    Searches file contents using `git grep`, which is fast and git-aware.
    """
    def __init__(self, config: Config):
        self._config = config
        self._root_dir = config.get_target_dir()

    @property
    def name(self) -> str:
        return "search_file_content"

    @property
    def description(self) -> str:
        return "Searches for a regex pattern within all files in the project using 'git grep'."

    @property
    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "pattern": {
                        "type": "STRING",
                        "description": "The regex pattern to search for."
                    }
                },
                "required": ["pattern"],
            },
        }

    async def execute(self, pattern: str) -> dict:
        """Executes the `git grep` search."""
        logging.info(f"Executing git grep for '{pattern}' in '{self._root_dir}'")
        
        try:
            # Command to run: git grep -n -E --untracked <pattern>
            # -n: show line numbers
            # -E: use extended regular expressions
            # --untracked: search in untracked files as well
            command = ['git', 'grep', '-n', '-E', '--untracked', '--', pattern]
            
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._root_dir
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0 and process.returncode != 1:
                # returncode 1 means no matches were found, which is not an error.
                # Other non-zero codes indicate a real error.
                err_message = stderr.decode('utf-8', 'ignore').strip()
                logging.error(f"`git grep` failed with code {process.returncode}: {err_message}")
                return {"error": f"git grep command failed: {err_message}"}

            output = stdout.decode('utf-8', 'ignore').strip()
            if not output:
                return {"matches": []}
            
            matches = []
            for line in output.splitlines():
                try:
                    # git grep output is typically "file:line:content"
                    parts = line.split(':', 2)
                    if len(parts) == 3:
                        matches.append({
                            "file_path": parts[0],
                            "line_number": int(parts[1]),
                            "line_content": parts[2]
                        })
                except (ValueError, IndexError):
                    logging.warning(f"Could not parse git grep line: {line}")

            return {"matches": matches}

        except FileNotFoundError:
            return {"error": "'git' command not found. Please ensure Git is installed and in your PATH."}
        except Exception as e:
            logging.error(f"Error during grep for '{pattern}': {e}", exc_info=True)
            return {"error": str(e)}