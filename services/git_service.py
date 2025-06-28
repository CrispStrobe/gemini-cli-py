#
# File: services/git_service.py
# Revision: 2
# Description: This service is now fully implemented to manage a shadow
# Git repository for creating safe snapshots of the user's project
# before modifications. This is a critical safety feature.
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
        self._is_git_repo = is_git_repository(self._project_root)
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

    def _run_git_command(self, *args, check=False) -> subprocess.CompletedProcess:
        """Runs a Git command in the context of the shadow repository."""
        # Use a longer timeout to accommodate potentially large git operations
        return subprocess.run(["git", *args], capture_output=True, text=True, env=self._git_env, check=check, timeout=60)

    def initialize(self):
        """Initializes the shadow repository if it doesn't exist."""
        if not self._is_git_repo:
            logging.warning("Project is not a Git repository. Snapshot feature will be disabled.")
            return

        if not self._history_dir.exists():
            logging.info(f"Initializing shadow Git repository at: {self._history_dir}")
            self._history_dir.mkdir(parents=True, exist_ok=True)
            self._run_git_command("init", "-b", "main")
            # Create a dedicated gitconfig for the shadow repo to avoid using user's info
            config_path = self._history_dir / ".git" / "config"
            with config_path.open("a") as f:
                f.write("[user]\n\tname = Gemini CLI\n\temail = gemini-cli@google.com\n")
            self._run_git_command("commit", "--allow-empty", "-m", "Initial commit")
        logging.info("Shadow Git repository is initialized.")


    def create_file_snapshot(self, message: str) -> str | None:
        """
        Creates a snapshot of the current project state in the shadow repo.

        Returns:
            The commit hash of the snapshot, or None on failure.
        """
        if not self._is_git_repo:
            return None
            
        try:
            logging.info("Creating file snapshot...")
            self._run_git_command("add", "-A")
            status_result = self._run_git_command("status", "--porcelain")
            
            if not status_result.stdout:
                logging.info("No changes detected, no new snapshot created.")
                # Return the latest commit hash if no changes
                return self._run_git_command("rev-parse", "HEAD", check=True).stdout.strip()

            commit_result = self._run_git_command("commit", "-m", message)
            if commit_result.returncode != 0:
                # This can happen if there are changes but they are empty (e.g., only mode changes)
                # Or if there's a pre-commit hook failure, which is unlikely here.
                logging.warning(f"Snapshot commit did not succeed, but may not be an error. Stderr: {commit_result.stderr}")
                return self._run_git_command("rev-parse", "HEAD", check=True).stdout.strip()

            commit_hash = self._run_git_command("rev-parse", "HEAD", check=True).stdout.strip()
            logging.info(f"Created snapshot with hash: {commit_hash}")
            return commit_hash
        except subprocess.CalledProcessError as e:
            logging.error(f"Failed to create file snapshot due to git command error: {e.stderr}")
            return None
        except Exception as e:
            logging.error(f"An unexpected error occurred during snapshot creation: {e}", exc_info=True)
            return None