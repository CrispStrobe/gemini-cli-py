#
# File: config.py
# Revision: 5 (Complete and Unified)
# Description: Handles all hierarchical configuration loading and provides
# a central Config object for the application, including shared services.
#

import argparse
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional, List, Dict, Any

from dotenv import load_dotenv

# This import assumes a services/file_discovery_service.py file exists
# as previously implemented.
from services.file_discovery_service import FileDiscoveryService

# ---
# Constants
# ---
SETTINGS_DIRECTORY_NAME = '.gemini'
USER_SETTINGS_DIR = Path.home() / SETTINGS_DIRECTORY_NAME
USER_SETTINGS_PATH = USER_SETTINGS_DIR / 'settings.json'
DEFAULT_MODEL = "gemini-2.5-pro"

class AuthType:
    """Mirrors the AuthType enum from the blueprint."""
    LOGIN_WITH_GOOGLE_PERSONAL = 'oauth-personal'
    LOGIN_WITH_GOOGLE_ENTERPRISE = 'oauth-enterprise'
    USE_GEMINI = 'gemini-api-key'
    USE_VERTEX_AI = 'vertex-ai'

class Config:
    """
    A class to encapsulate the final, merged configuration and provide
    access to shared services, mirroring the Config class from the blueprint.
    """
    def __init__(self, config_dict: dict):
        self._config = config_dict
        self._target_dir = Path.cwd()
        self._file_service: Optional[FileDiscoveryService] = None

    def get_target_dir(self) -> Path:
        """Returns the project's root/target directory."""
        return self._target_dir

    def get_file_service(self) -> FileDiscoveryService:
        """Initializes and returns the singleton FileDiscoveryService."""
        if self._file_service is None:
            logging.debug("Initializing FileDiscoveryService.")
            self._file_service = FileDiscoveryService(self._target_dir)
        return self._file_service

    def get_model(self) -> str:
        """Returns the configured model name."""
        return self._config.get("model", DEFAULT_MODEL)

    def get_auth_type(self) -> str:
        """Returns the configured authentication type."""
        return self._config.get("auth_type", AuthType.LOGIN_WITH_GOOGLE_PERSONAL)

    def get_project_id(self) -> Optional[str]:
        """Returns the Google Cloud Project ID, if configured."""
        return self._config.get("GOOGLE_CLOUD_PROJECT")

    # Generic getter for any other config values
    def get(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

# ---
# Helper Functions for Loading Configuration
# ---

def find_env_file(start_dir: Path) -> Optional[Path]:
    """
    Searches for .env files in the workspace and then user's home directory.
    """
    current_dir = start_dir.resolve()
    while True:
        gemini_env_path = current_dir / SETTINGS_DIRECTORY_NAME / '.env'
        if gemini_env_path.exists():
            return gemini_env_path
        env_path = current_dir / '.env'
        if env_path.exists():
            return env_path
        parent_dir = current_dir.parent
        if parent_dir == current_dir:  # Reached root
            home_gemini_env = Path.home() / SETTINGS_DIRECTORY_NAME / '.env'
            if home_gemini_env.exists():
                return home_gemini_env
            home_env = Path.home() / '.env'
            if home_env.exists():
                return home_env
            return None
        current_dir = parent_dir

def resolve_env_vars(config_obj: Any) -> Any:
    """
    Recursively resolves environment variables in a config object.
    """
    if isinstance(config_obj, dict):
        return {k: resolve_env_vars(v) for k, v in config_obj.items()}
    elif isinstance(config_obj, list):
        return [resolve_env_vars(i) for i in config_obj]
    elif isinstance(config_obj, str):
        env_var_regex = r'\$(?:(\w+)|{([^}]+)})'
        def replace_env(match):
            var_name = match.group(1) or match.group(2)
            return os.environ.get(var_name, match.group(0))
        return re.sub(env_var_regex, replace_env, config_obj)
    return config_obj

def load_settings_file(file_path: Path) -> Dict:
    """Loads and parses a single settings.json file."""
    if not file_path.exists():
        return {}
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            # Simple comment stripping
            content = "".join(line for line in f if not line.strip().startswith('//'))
            parsed_settings = json.loads(content)
            return resolve_env_vars(parsed_settings)
    except (IOError, json.JSONDecodeError) as e:
        logging.warning(f"Could not load or parse settings from {file_path}: {e}")
        return {}

def load_and_merge_settings(workspace_dir: Path) -> Dict:
    """
    Loads user and workspace settings and merges them, with workspace taking precedence.
    """
    logging.debug(f"Loading user settings from: {USER_SETTINGS_PATH}")
    user_settings = load_settings_file(USER_SETTINGS_PATH)

    workspace_settings_path = workspace_dir / SETTINGS_DIRECTORY_NAME / 'settings.json'
    logging.debug(f"Loading workspace settings from: {workspace_settings_path}")
    workspace_settings = load_settings_file(workspace_settings_path)

    merged = user_settings.copy()
    merged.update(workspace_settings)
    logging.debug("Merged user and workspace settings.")
    return merged

def parse_arguments() -> argparse.Namespace:
    """
    Parses command-line arguments.
    """
    parser = argparse.ArgumentParser(description="Gemini CLI Python Replica")
    parser.add_argument("-m", "--model", type=str, help="Model to use.")
    auth_choices = [getattr(AuthType, attr) for attr in dir(AuthType) if not attr.startswith('__')]
    parser.add_argument("--auth_type", type=str, choices=auth_choices, help="Authentication type.")
    # Add other arguments as needed
    return parser.parse_args()

# ---
# Main Configuration Loading Function
# ---

def load_final_config() -> Config:
    """
    Orchestrates loading from all sources and returns a single,
    unified Config object.
    """
    args = parse_arguments()

    env_file_path = find_env_file(Path.cwd())
    if env_file_path:
        load_dotenv(dotenv_path=env_file_path, override=True)
        logging.info(f"Loaded environment variables from: {env_file_path}")

    settings = load_and_merge_settings(Path.cwd())

    # Combine sources in order of precedence: CLI args > env vars > settings files
    raw_config_dict = {
        "model": (
            args.model or
            os.getenv("GEMINI_MODEL") or
            settings.get("model") or
            DEFAULT_MODEL
        ),
        "auth_type": (
            args.auth_type or
            os.getenv("GEMINI_AUTH_TYPE") or
            settings.get("selectedAuthType") or
            AuthType.LOGIN_WITH_GOOGLE_PERSONAL
        ),
        "GOOGLE_CLOUD_PROJECT": (
            os.getenv("GOOGLE_CLOUD_PROJECT") or
            settings.get("GOOGLE_CLOUD_PROJECT")
        ),
        # Add other final config values here
    }
    return Config(raw_config_dict)

def validate_auth(config: Config) -> Optional[str]:
    """
    Validates if the required environment variables for an auth method are set.
    """
    auth_method = config.get_auth_type()
    logging.debug(f"Validating auth method: {auth_method}")
    if auth_method == AuthType.LOGIN_WITH_GOOGLE_ENTERPRISE:
        if not config.get_project_id():
            return 'GOOGLE_CLOUD_PROJECT environment variable not found or set for Enterprise login.'
    # Add other validations (e.g., for API keys) here if they are implemented
    return None