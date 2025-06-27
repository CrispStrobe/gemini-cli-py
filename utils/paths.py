#
# File: utils/paths.py
# Revision: 3
# Description: Corrects the function call for getting the user's home
# directory from the incorrect `os.homedir()` to `pathlib.Path.home()`.
#

import os
import hashlib
from pathlib import Path

GEMINI_DIR = '.gemini'
TMP_DIR_NAME = 'tmp'

def get_project_hash(project_root: str) -> str:
    """
    Generates a unique hash for a project based on its root path.
    """
    return hashlib.sha256(project_root.encode('utf-8')).hexdigest()

def get_project_temp_dir(project_root: str) -> Path:
    """
    Generates a unique temporary directory path for a project.
    """
    project_hash = get_project_hash(project_root)
    # Use Path.home() which is the correct Pythonic way.
    return Path.home() / GEMINI_DIR / TMP_DIR_NAME / project_hash