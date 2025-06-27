#
# File: services/memory_discovery.py
# Revision: 1
# Description: Service for discovering and loading hierarchical memory from
# GEMINI.md files.
#

import logging
from pathlib import Path

from utils.git_utils import find_git_root

MEMORY_FILE_NAME = "GEMINI.md"

def load_memory(start_dir: Path) -> str:
    """
    Finds and loads all GEMINI.md files in a hierarchical manner.

    The search order is:
    1. Scan from start_dir up to the git root (or start_dir's root).
    2. Scan from that root directory downwards, finding all other memory files.
    """
    logging.info(f"Searching for memory files starting from: {start_dir}")
    project_root = find_git_root(start_dir) or start_dir.resolve()
    memory_files = []

    # 1. Scan upwards from start_dir to project_root
    current_dir = start_dir.resolve()
    while True:
        memory_path = current_dir / MEMORY_FILE_NAME
        if memory_path.is_file() and memory_path not in memory_files:
            memory_files.append(memory_path)
        if current_dir == project_root or current_dir.parent == current_dir:
            break
        current_dir = current_dir.parent

    # 2. Scan downwards from project_root to find all others
    for path in project_root.rglob(f"**/{MEMORY_FILE_NAME}"):
        if path.is_file() and path not in memory_files:
            memory_files.append(path)

    # Reverse the list so parent memories are loaded first
    memory_files.reverse()

    if not memory_files:
        logging.info("No memory files found.")
        return ""

    logging.info(f"Found {len(memory_files)} memory file(s): {[str(p) for p in memory_files]}")
    all_content = []
    for file_path in memory_files:
        try:
            content = file_path.read_text('utf-8')
            all_content.append(f"\n--- Memory from {file_path.relative_to(project_root)} ---\n{content}")
        except IOError as e:
            logging.warning(f"Could not read memory file {file_path}: {e}")

    return "\n".join(all_content)