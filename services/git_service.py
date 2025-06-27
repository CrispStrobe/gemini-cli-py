#
# File: services/git_service.py
# Revision: 1
# Description: Manages a shadow Git repository for creating safe snapshots
# of the user's project before modifications.
#

import logging
import os
import subprocess
from pathlib import Path

from utils.git_utils import is_git_repository
from utils.paths import get_project_hash

class GitService:
    """
    Manages a hidden Git repository to support checkpointing and undoing changes.
    """
    def __init__(self, project_root: Path):
        self._project_root = project_root
        self._history_dir = self._get_history_dir()
        self._git_env = self._get_git_env()

    def _get_history_dir(self) -> Path:
        """Determines the location of the shadow git repo."""
        home_dir = Path.home()
        project_hash = get_project_hash(str(self._project_root))
        return home_dir / ".gemini" / "history" / project_hash

    def _get_git_env(self) -> dict:
        """Creates the environment variables needed to control the shadow repo."""
        return {
            **os.environ,
            "GIT_DIR": str(self._history_dir / ".git"),
            "GIT_WORK_TREE": str(self._project_root),
        }

    def _run_git_command(self, *args) -> subprocess.CompletedProcess:
        """Runs a Git command in the context of the shadow repository."""
        return subprocess.run(["git", *args], capture_output=True, text=True, env=self._git_env, check=False)

    def initialize(self):
        """Initializes the shadow repository if it doesn't exist."""
        if not is_git_repository(self._project_root):
            logging.warning("Project is not a Git repository. Snapshot feature will be disabled.")
            return

        if not self._history_dir.exists():
            logging.info(f"Initializing shadow Git repository at: {self._history_dir}")
            self._history_dir.mkdir(parents=True, exist_ok=True)
            self._run_git_command("init", "-b", "main")
            # Create a dedicated gitconfig for the shadow repo to avoid using user's info
            (self._history_dir / ".gitconfig").write_text(
                "[user]\n  name = Gemini CLI\n  email = gemini-cli@google.com\n"
            )
            # This is a fix, GIT_DIR is already in the env, no need to pass it as an argument
            self._run_git_command("commit", "--allow-empty", "-m", "Initial commit")


    def create_file_snapshot(self, message: str) -> str | None:
        """
        Creates a snapshot of the current project state.

        Returns:
            The commit hash of the snapshot, or None on failure.
        """
        try:
            self._run_git_command("add", "-A")
            status_result = self._run_git_command("status", "--porcelain")
            if not status_result.stdout:
                logging.info("No changes to snapshot.")
                return self._run_git_command("rev-parse", "HEAD").stdout.strip()

            commit_result = self._run_git_command("commit", "-m", message)
            if commit_result.returncode != 0:
                logging.error(f"Snapshot commit failed: {commit_result.stderr}")
                return None
            
            commit_hash = self._run_git_command("rev-parse", "HEAD").stdout.strip()
            logging.info(f"Created snapshot with hash: {commit_hash}")
            return commit_hash
        except Exception as e:
            logging.error(f"Failed to create file snapshot: {e}")
            return None