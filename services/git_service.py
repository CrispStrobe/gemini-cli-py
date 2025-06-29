#
# File: services/git_service.py
# Revision: 3
# Description: Implements the restore and branch-checking capabilities.
# - Adds `restore_project_from_snapshot` to revert the working directory
#   to a specific commit hash from the shadow repository.
# - Adds `get_current_branch_name` to provide contextual information to the UI.
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
            config_path = self._history_dir / ".git" / "config"
            with config_path.open("a") as f:
                f.write("[user]\n\tname = Gemini CLI\n\temail = gemini-cli@google.com\n")
            self._run_git_command("commit", "--allow-empty", "-m", "Initial commit")
        logging.info("Shadow Git repository is initialized.")

    def create_file_snapshot(self, message: str) -> str | None:
        """
        Creates a snapshot of the current project state in the shadow repo.
        """
        if not self._is_git_repo:
            return None
            
        try:
            logging.info("Creating file snapshot...")
            self._run_git_command("add", "-A")
            status_result = self._run_git_command("status", "--porcelain")
            
            if not status_result.stdout:
                logging.info("No changes detected, no new snapshot created.")
                return self._run_git_command("rev-parse", "HEAD", check=True).stdout.strip()

            commit_result = self._run_git_command("commit", "-m", message)
            if commit_result.returncode != 0:
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
            
    def get_current_branch_name(self) -> str | None:
        """Gets the current branch name of the user's repository."""
        if not self._is_git_repo:
            return None
        try:
            # We run this command in the actual project directory, not the shadow repo
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, check=True, cwd=self._project_root
            )
            branch = result.stdout.strip()
            return branch if branch != "HEAD" else None
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None

    def restore_project_from_snapshot(self, commit_hash: str) -> bool:
        """
        Restores the working directory to the state of a specific commit hash.
        This is a destructive operation.
        """
        if not self._is_git_repo:
            logging.error("Cannot restore snapshot: project is not a git repository.")
            return False
        try:
            logging.warning(f"Restoring project to snapshot {commit_hash}. This will discard current changes.")
            # Reset the state of the files to the commit
            self._run_git_command("reset", "--hard", commit_hash, check=True)
            # Remove any new untracked files and directories
            self._run_git_command("clean", "-fdx", check=True)
            logging.info(f"Successfully restored project to snapshot {commit_hash}.")
            return True
        except subprocess.CalledProcessError as e:
            logging.error(f"Failed to restore project from snapshot {commit_hash}: {e.stderr}")
            return False
        except Exception as e:
            logging.error(f"An unexpected error occurred during restore: {e}", exc_info=True)
            return False