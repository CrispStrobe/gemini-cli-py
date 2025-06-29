#
# File: slash_command_processor.py
# Revision: 2
# Description: Implements the full suite of /chat and /restore commands.
# This makes the snapshot and checkpointing system fully interactive,
# allowing users to save, list, and restore session states and file states.
#

import logging
from typing import TYPE_CHECKING, Callable, Dict, Awaitable

if TYPE_CHECKING:
    from chat_session import ChatSession
    from main import AgenticREPL

class SlashCommandProcessor:
    """Parses and dispatches slash commands entered in the REPL."""

    def __init__(self, repl_app: 'AgenticREPL', chat_session: 'ChatSession'):
        self.app = repl_app
        self.session = chat_session
        self.commands: Dict[str, Callable[[str], Awaitable[None]]] = {
            "/reset": self._handle_reset,
            "/debug": self._handle_debug,
            "/stats": self._handle_stats,
            "/help": self._handle_help,
            "/?": self._handle_help,
            "/chat": self._handle_chat,
            "/restore": self._handle_restore,
        }
        self.quit_commands = {"/quit", "quit", "exit"}

    async def process(self, user_input: str) -> bool:
        """
        Processes the user input to see if it's a slash command.
        """
        text = user_input.strip()
        if not text:
            return False

        if text.lower() in self.quit_commands:
            self.app.is_running = False
            return True

        command_part = text.split(' ')[0].lower()
        if command_part in self.commands:
            await self.commands[command_part](text)
            return True
        
        return False

    async def _handle_reset(self, _):
        """Resets the current chat session."""
        self.session.reset()
        print("\n--- New conversation started ---")

    async def _handle_debug(self, _):
        """Toggles debug logging on and off."""
        from logging_config import toggle_debug_mode
        is_now_debug = toggle_debug_mode()
        print(f"[SYSTEM] Debug mode is now {'ON' if is_now_debug else 'OFF'}.")

    async def _handle_stats(self, _):
        """Displays simple stats about the current session."""
        stats = self.session.get_stats()
        print("\n--- Session Stats ---")
        print(f"  Model: {self.session.model}")
        print(f"  History Length: {stats['history_length']} messages")
        print("---------------------\n")

    async def _handle_help(self, _):
        """Displays a help message with available commands."""
        help_text = """
--- Gemini CLI Help ---
/help or /?                       Show this help message.
/reset                            Start a new conversation.
/quit                             Exit the application.
/stats                            Show session statistics.
/debug                            Toggle debug logging.
/m <pro|flash>                    Switch the model for the current session.
/chat list                        List all saved conversation checkpoints.
/chat save <tag>                  Save the current conversation with a tag.
/chat resume <tag> | /restore <tag> Restore project files and conversation from a tagged checkpoint.
-----------------------
"""
        print(help_text)
        
    async def _handle_chat(self, user_input: str):
        """Handles /chat subcommands: list, save, resume."""
        parts = user_input.strip().split()
        if len(parts) < 2:
            print("[ERROR] Invalid /chat command. Use '/chat list', '/chat save <tag>', or '/chat resume <tag>'.")
            return

        subcommand = parts[1].lower()
        if subcommand == 'list':
            checkpoints = self.app.logger.list_checkpoints()
            if not checkpoints:
                print("[SYSTEM] No saved checkpoints found.")
            else:
                print("[SYSTEM] Available checkpoints:\n- " + "\n- ".join(checkpoints))
        elif subcommand == 'save':
            if len(parts) < 3:
                print("[ERROR] Usage: /chat save <tag>")
                return
            tag = parts[2]
            commit_hash = self.app.git_service.create_file_snapshot(f"Manual save for checkpoint: {tag}")
            self.app.logger.save_checkpoint(self.session.history, commit_hash, tag)
            print(f"[SYSTEM] Conversation saved as '{tag}'.")
        elif subcommand == 'resume':
            if len(parts) < 3:
                print("[ERROR] Usage: /chat resume <tag>")
                return
            await self._handle_restore(" ".join(parts)) # Delegate to restore handler
        else:
            print(f"[ERROR] Unknown /chat command: {subcommand}")
            
    async def _handle_restore(self, user_input: str):
        """Handles /restore <tag> and /chat resume <tag>."""
        parts = user_input.strip().split()
        if len(parts) < 2:
            print("[ERROR] Usage: /restore <tag>")
            return
        tag = parts[1]
        
        checkpoint = self.app.logger.load_checkpoint(tag)
        if not checkpoint:
            print(f"[ERROR] Checkpoint '{tag}' not found.")
            return
            
        commit_hash = checkpoint.get("commit_hash")
        history = checkpoint.get("history")

        if not commit_hash or not history:
            print(f"[ERROR] Checkpoint '{tag}' is malformed and cannot be restored.")
            return

        print(f"\n[CONFIRMATION] This will revert all files in your project to the state they were in when checkpoint '{tag}' was created (commit: {commit_hash[:7]}).")
        print("All current uncommitted changes will be lost.")
        response = input("Are you sure you want to continue? (y/n): ").lower().strip()

        if response == 'y':
            print(f"Restoring project state from snapshot {commit_hash[:7]}...")
            success = self.app.git_service.restore_project_from_snapshot(commit_hash)
            if success:
                self.session.history = history
                print(f"\n--- Restored session and files from checkpoint '{tag}' ---")
            else:
                print("[ERROR] Failed to restore project files. Your session was not changed.")
        else:
            print("[SYSTEM] Restore cancelled.")