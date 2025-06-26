#
# File: tools/find_tools.py
# Revision: 1
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
    """Lists the contents of a directory."""
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
            target_path = Path(path).resolve()
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
    """Finds files using glob patterns."""
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
    """Searches file contents using regex."""
    def __init__(self, config: Config):
        self._root_dir = config.get_target_dir()
        self._file_service = config.get_file_service()
    @property
    def name(self) -> str: return "search_file_content"
    @property
    def description(self) -> str: return "Searches for a regex pattern within files in the project."
    @property
    def schema(self) -> dict:
        return {"name": self.name,"description": self.description,"parameters": {"type": "OBJECT","properties": {"pattern": {"type": "STRING", "description": "The regex pattern."}, "path": {"type": "STRING", "description": "Optional directory to search."}},"required": ["pattern"]}}

    async def execute(self, pattern: str, path: str = None) -> dict:
        search_path = self._root_dir / path if path else self._root_dir
        try:
            matches = []
            files_to_search = self._file_service.filter_files(list(search_path.rglob('*')))
            for file_path in files_to_search:
                if file_path.is_file():
                    try:
                        with open(file_path, 'r', 'utf-8', errors='ignore') as f:
                            for i, line in enumerate(f, 1):
                                if re.search(pattern, line):
                                    matches.append({"file_path": str(file_path.relative_to(self._root_dir)),"line_number": i,"line_content": line.strip()})
                    except Exception: continue
            return {"matches": matches}
        except Exception as e:
            return {"error": str(e)}