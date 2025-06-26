#
# File: utils/git_ignore_parser.py
# Revision: 1
# Description: A parser for .gitignore and .geminiignore files.
#

import logging
from pathlib import Path
from typing import List

# Third-party library for robust .gitignore pattern matching.
# To install: pip install pathspec
import pathspec

from .git_utils import is_git_repository

class GitIgnoreParser:
    """
    Parses ignore files and determines if a given file path should be ignored.
    """
    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()
        self._patterns: List[str] = []
        self._spec = None

    def add_patterns(self, patterns: List[str]):
        self._patterns.extend(patterns)
        self._spec = None # Force recompile on next check

    def load_patterns_from_file(self, file_name: str):
        patterns_file_path = self.project_root / file_name
        if not patterns_file_path.exists():
            return
        try:
            with open(patterns_file_path, 'r', encoding='utf-8') as f:
                new_patterns = [p.strip() for p in f if p.strip() and not p.strip().startswith('#')]
                self.add_patterns(new_patterns)
        except IOError as e:
            logging.warning(f"Could not read ignore file {patterns_file_path}: {e}")

    def _compile_spec(self):
        if self._spec is None:
            self._spec = pathspec.PathSpec.from_lines(pathspec.patterns.GitWildMatchPattern, self._patterns)

    def is_ignored(self, file_path: str | Path) -> bool:
        self._compile_spec()
        try:
            relative_path = Path(file_path).relative_to(self.project_root)
            return self._spec.match_file(str(relative_path))
        except ValueError:
            return False