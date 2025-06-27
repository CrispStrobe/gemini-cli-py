#
# File: utils/git_utils.py
# Revision: 2
# Description: Adds a function to find the root of a Git repository.
#

import os
from pathlib import Path

def find_git_root(start_dir: str | Path) -> Path | None:
    """
    Finds the root directory of a git repository by traversing up from a
    starting directory.

    Returns:
        The Path object of the git root directory, or None if not found.
    """
    try:
        current_dir = Path(start_dir).resolve()
        while True:
            if (current_dir / '.git').is_dir():
                return current_dir
            if current_dir.parent == current_dir:
                # Reached the filesystem root (e.g., '/')
                return None
            current_dir = current_dir.parent
    except (OSError, PermissionError):
        return None

def is_git_repository(directory: str | Path) -> bool:
    """
    Checks if a directory is within a git repository.
    """
    return find_git_root(directory) is not None