#
# File: config.py
# Revision: 9
# Description: CRITICAL FIX. Reverts the DEFAULT_MODEL constant from the
# incorrect "1.5" version back to the correct "2.5-pro" version.
# This fixes the bug causing the application to start with the wrong model.
#

import argparse
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional, List, Dict, Any

from dotenv import load_dotenv

from services.file_discovery_service import FileDiscoveryService
from services.git_service import GitService
from logger import Logger

# --- Constants ---
SETTINGS_DIRECTORY_NAME = '.gemini'
USER_SETTINGS_DIR = Path.home() / SETTINGS_DIRECTORY_NAME
USER_SETTINGS_PATH = USER_SETTINGS_DIR / 'settings.json'
# FIX: Reverted to the correct default model.
DEFAULT_MODEL = "gemini-2.5-pro"

class AuthType:
    LOGIN_WITH_GOOGLE_PERSONAL = 'oauth-personal'
    LOGIN_WITH_GOOGLE_ENTERPRISE = 'oauth-enterprise'
    USE_GEMINI = 'gemini-api-key'
    USE_VERTEX_AI = 'vertex-ai'

class Config:
    def __init__(self, config_dict: dict):
        self._config = config_dict
        self._target_dir = Path.cwd()
        self._file_service: Optional[FileDiscoveryService] = None
        self._git_service: Optional[GitService] = None
        self._logger: Optional[Logger] = None

    def get_target_dir(self) -> Path:
        return self._target_dir

    def get_file_service(self) -> FileDiscoveryService:
        if self._file_service is None:
            self._file_service = FileDiscoveryService(self._target_dir)
        return self._file_service

    def get_git_service(self) -> GitService:
        """Initializes and returns the singleton GitService."""
        if self._git_service is None:
            self._git_service = GitService(self._target_dir)
            self._git_service.initialize() # Initialize on first access
        return self._git_service

    def get_logger(self) -> Logger:
        """Initializes and returns the singleton Logger service."""
        if self._logger is None:
            self._logger = Logger(self._target_dir)
        return self._logger

    def get_model(self) -> str:
        return self._config.get("model", DEFAULT_MODEL)

    def get_auth_type(self) -> str:
        return self._config.get("auth_type", AuthType.LOGIN_WITH_GOOGLE_PERSONAL)

    def get_project_id(self) -> Optional[str]:
        return self._config.get("GOOGLE_CLOUD_PROJECT")

    def get(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

def find_env_file(start_dir: Path) -> Optional[Path]:
    current_dir = start_dir.resolve()
    while True:
        gemini_env_path = current_dir / SETTINGS_DIRECTORY_NAME / '.env'
        if gemini_env_path.exists(): return gemini_env_path
        env_path = current_dir / '.env'
        if env_path.exists(): return env_path
        parent_dir = current_dir.parent
        if parent_dir == current_dir:
            home_gemini_env = Path.home() / SETTINGS_DIRECTORY_NAME / '.env'
            if home_gemini_env.exists(): return home_gemini_env
            home_env = Path.home() / '.env'
            if home_env.exists(): return home_env
            return None
        current_dir = parent_dir

def resolve_env_vars(config_obj: Any) -> Any:
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
    if not file_path.exists(): return {}
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = "".join(line for line in f if not line.strip().startswith('//'))
            return resolve_env_vars(json.loads(content))
    except (IOError, json.JSONDecodeError) as e:
        logging.warning(f"Could not load or parse settings from {file_path}: {e}")
        return {}

def load_and_merge_settings(workspace_dir: Path) -> Dict:
    user_settings = load_settings_file(USER_SETTINGS_PATH)
    workspace_settings = load_settings_file(workspace_dir / SETTINGS_DIRECTORY_NAME / 'settings.json')
    merged = user_settings.copy()
    merged.update(workspace_settings)
    return merged

def parse_arguments() -> argparse.Namespace:
    # This function is now only used by load_final_config. The main parser is in main.py.
    return argparse.Namespace()

def load_final_config(cli_args: argparse.Namespace = None) -> Config:
    args = cli_args if cli_args is not None else parse_arguments()
    env_file_path = find_env_file(Path.cwd())
    if env_file_path:
        load_dotenv(dotenv_path=env_file_path, override=True)
        logging.info(f"Loaded environment variables from: {env_file_path}")
    settings = load_and_merge_settings(Path.cwd())
    raw_config_dict = {
        "model": (args.model if hasattr(args, 'model') and args.model else os.getenv("GEMINI_MODEL") or settings.get("model") or DEFAULT_MODEL),
        "auth_type": (os.getenv("GEMINI_AUTH_TYPE") or settings.get("selectedAuthType") or AuthType.LOGIN_WITH_GOOGLE_PERSONAL),
        "GOOGLE_CLOUD_PROJECT": (os.getenv("GOOGLE_CLOUD_PROJECT") or settings.get("GOOGLE_CLOUD_PROJECT")),
    }
    return Config(raw_config_dict)

def validate_auth(config: Config) -> Optional[str]:
    auth_method = config.get_auth_type()
    if auth_method == AuthType.LOGIN_WITH_GOOGLE_ENTERPRISE:
        if not config.get_project_id():
            return 'GOOGLE_CLOUD_PROJECT environment variable not found or set for Enterprise login.'
    return None