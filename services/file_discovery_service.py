#
# File: services/file_discovery_service.py
# Revision: 1
# Description: Service for discovering files while respecting ignore rules.
#

import logging
from pathlib import Path
from typing import List

from utils.git_ignore_parser import GitIgnoreParser
from utils.git_utils import is_git_repository

GEMINI_IGNORE_FILE_NAME = '.geminiignore'

class FileDiscoveryService:
    """Filters file paths based on .gitignore and .geminiignore rules."""
    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()
        
        self._git_ignore_parser = GitIgnoreParser(self.project_root)
        if is_git_repository(self.project_root):
            logging.debug("Git repository detected, loading .gitignore patterns.")
            self._git_ignore_parser.add_patterns(['.git/'])
            self._git_ignore_parser.load_patterns_from_file('.gitignore')
            self._git_ignore_parser.load_patterns_from_file('.git/info/exclude')
            
        self._gemini_ignore_parser = GitIgnoreParser(self.project_root)
        self._gemini_ignore_parser.load_patterns_from_file(GEMINI_IGNORE_FILE_NAME)

    def is_ignored(self, file_path: str | Path) -> bool:
        """Checks if a file is ignored by either .git or .gemini rules."""
        return self._git_ignore_parser.is_ignored(file_path) or \
               self._gemini_ignore_parser.is_ignored(file_path)

    def filter_files(self, file_paths: List[Path]) -> List[Path]:
        """Filters a list of Path objects, returning only those not ignored."""
        return [p for p in file_paths if not self.is_ignored(p)]