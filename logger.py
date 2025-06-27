#
# File: logger.py
# Revision: 1
# Description: Handles saving and loading chat session checkpoints.
#

import json
import logging
from pathlib import Path
from typing import List, Dict, Any

from utils.paths import get_project_temp_dir

class Logger:
    """
    Manages saving and loading of conversation checkpoints.
    """
    def __init__(self, project_root: Path):
        self._temp_dir = get_project_temp_dir(str(project_root))
        self._checkpoint_file = self._temp_dir / "checkpoint.json"

    def save_checkpoint(self, history: List[Dict[str, Any]]):
        """Saves the chat history to a checkpoint file."""
        try:
            self._temp_dir.mkdir(parents=True, exist_ok=True)
            with open(self._checkpoint_file, 'w', encoding='utf-8') as f:
                json.dump(history, f, indent=2)
            logging.info(f"Chat session checkpoint saved to: {self._checkpoint_file}")
        except IOError as e:
            logging.error(f"Failed to save checkpoint: {e}")

    def load_checkpoint(self) -> List[Dict[str, Any]] | None:
        """Loads chat history from a checkpoint file if it exists."""
        if not self._checkpoint_file.exists():
            return None
        try:
            with open(self._checkpoint_file, 'r', encoding='utf-8') as f:
                history = json.load(f)
            logging.info(f"Resuming session from checkpoint: {self._checkpoint_file}")
            return history
        except (IOError, json.JSONDecodeError) as e:
            logging.error(f"Failed to load checkpoint: {e}")
            return None