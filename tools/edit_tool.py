#
# File: tools/edit_tool.py
# Revision: 1
# Description: The tool for replacing content within a file, leveraging
# the self-healing edit corrector.
#

import logging
from pathlib import Path

from .base import Tool
from .tool_io import ToolConfirmationOutcome
from config import Config
from utils.diff_utils import create_diff
from utils.edit_corrector import ensure_correct_edit

class ReplaceInFileTool(Tool):
    """
    A tool to replace a specific string in a file with a new string.
    """
    def __init__(self, config: Config):
        self._root_dir = config.get_target_dir()
        self._file_service = config.get_file_service()

    @property
    def name(self) -> str: return "replace_in_file"
    @property
    def description(self) -> str: return "Replaces an `old_string` with a `new_string` in a specified file."
    @property
    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "path": {"type": "STRING", "description": "The path to the file to edit."},
                    "old_string": {"type": "STRING", "description": "The exact string to be replaced."},
                    "new_string": {"type": "STRING", "description": "The string to replace the `old_string` with."}
                },
                "required": ["path", "old_string", "new_string"],
            }
        }

    async def should_confirm_execute(self, path: str, old_string: str, new_string: str) -> dict | None:
        file_path = self._root_dir / path
        if not file_path.is_file():
            return None # Let execute handle the "file not found" error.

        try:
            original_content = file_path.read_text('utf-8')
            
            # Use the corrector to get the right `old_string`
            correction = await ensure_correct_edit(original_content, old_string, new_string)
            final_old_string = correction["old_string"]

            if final_old_string not in original_content:
                return None # Again, let execute handle the error cleanly.

            new_content = original_content.replace(final_old_string, new_string, 1)
            diff = create_diff(original_content, new_content, path)
            
            return {
                "type": "edit",
                "path": path,
                "diff": diff,
                "correction_reason": correction["reason"]
            }
        except Exception as e:
            logging.error(f"Error creating diff for confirmation: {e}")
            return None

    async def execute(self, path: str, old_string: str, new_string: str) -> dict:
        logging.info(f"Executing edit for file: {path}")
        try:
            file_path = self._root_dir / path
            if not file_path.resolve().is_relative_to(self._root_dir.resolve()):
                return {"error": "Path traversal detected. Access denied."}
            if not file_path.is_file():
                return {"error": f"File not found at path: {path}"}
            if self._file_service.is_ignored(file_path):
                return {"error": f"File is ignored by git or gemini ignore rules: {path}"}

            original_content = file_path.read_text('utf-8')
            
            correction = await ensure_correct_edit(original_content, old_string, new_string)
            final_old_string = correction["old_string"]
            final_new_string = correction["new_string"]

            if final_old_string not in original_content:
                return {"error": f"The `old_string` was not found in the file, even after correction. Correction reason: {correction['reason']}"}

            new_content = original_content.replace(final_old_string, final_new_string, 1)
            file_path.write_text(new_content, 'utf-8')

            return {"success": True, "message": f"Successfully replaced content in {path}."}
        except Exception as e:
            return {"error": str(e)}