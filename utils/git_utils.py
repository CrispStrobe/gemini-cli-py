#
# File: utils/git_utils.py
# Revision: 1
# Description: Git-related utility functions.
#

import os
from pathlib import Path

def is_git_repository(directory: str | Path) -> bool:
    """
    Checks if a directory is within a git repository by traversing up
    the directory tree and looking for a .git directory.
    """
    try:
        current_dir = Path(directory).resolve()
        
        while True:
            git_dir = current_dir / '.git'
            if git_dir.exists():
                return True
            if current_dir.parent == current_dir:
                break
            current_dir = current_dir.parent
        return False
    except (OSError, PermissionError):
        return False