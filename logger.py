#
# File: logger.py
# Revision: 3
# Description: Upgrades checkpoint format to a dictionary containing both
# the 'history' and the 'commit_hash'. This links a conversation state
# to a specific file system snapshot, enabling the /restore feature.
#

import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from utils.paths import get_project_temp_dir

CheckpointData = Dict[str, Any]

class Logger:
    """
    Manages saving and loading of conversation checkpoints, including git snapshots.
    """
    def __init__(self, project_root: Path):
        self._temp_dir = get_project_temp_dir(str(project_root))

    def _get_checkpoint_path(self, tag: Optional[str] = None) -> Path:
        """Gets the file path for a given checkpoint tag."""
        filename = f"checkpoint-{tag}.json" if tag else "checkpoint.json"
        return self._temp_dir / filename

    def save_checkpoint(self, history: List[Dict[str, Any]], commit_hash: Optional[str], tag: Optional[str] = None):
        """Saves the chat history and commit hash to a checkpoint file."""
        checkpoint_file = self._get_checkpoint_path(tag)
        checkpoint_data: CheckpointData = {
            "history": history,
            "commit_hash": commit_hash
        }
        try:
            self._temp_dir.mkdir(parents=True, exist_ok=True)
            with open(checkpoint_file, 'w', encoding='utf-8') as f:
                json.dump(checkpoint_data, f, indent=2)
            logging.info(f"Chat session checkpoint saved to: {checkpoint_file}")
        except IOError as e:
            logging.error(f"Failed to save checkpoint: {e}")

    def load_checkpoint(self, tag: Optional[str] = None) -> CheckpointData | None:
        """Loads checkpoint data (history and hash) from a file if it exists."""
        checkpoint_file = self._get_checkpoint_path(tag)
        if not checkpoint_file.exists():
            return None
        try:
            with open(checkpoint_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            logging.info(f"Resuming session from checkpoint: {checkpoint_file}")
            return data
        except (IOError, json.JSONDecodeError) as e:
            logging.error(f"Failed to load checkpoint: {e}")
            return None
            
    def list_checkpoints(self) -> List[str]:
        """Lists all available saved checkpoint tags."""
        if not self._temp_dir.exists():
            return []
        checkpoints = []
        for f in self._temp_dir.glob("checkpoint-*.json"):
            tag = f.stem.replace("checkpoint-", "")
            checkpoints.append(tag)
        return sorted(checkpoints)