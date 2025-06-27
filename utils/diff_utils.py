#
# File: utils/diff_utils.py
# Revision: 1
# Description: A utility for creating text-based diffs.
#

import difflib

def create_diff(old_content: str, new_content: str, file_path: str) -> str:
    """
    Generates a unified diff string to show changes between two versions of a file.
    """
    diff = difflib.unified_diff(
        old_content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
    )
    return "".join(diff)