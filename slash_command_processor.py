#
# File: slash_command_processor.py
# Revision: 1 (New)
# Description: Implements Step 3 of the development plan.
# This module provides a centralized processor for all slash commands,
# making the REPL more powerful and extensible.
#

import logging
from typing import TYPE_CHECKING, Callable, Dict, Awaitable

if TYPE_CHECKING:
    from gemini_client import ChatSession
    from main import AgenticREPL

class SlashCommandProcessor:
    """Parses and dispatches slash commands entered in the REPL."""

    def __init__(self, repl_app: 'AgenticREPL', chat_session: 'ChatSession'):
        self.app = repl_app
        self.session = chat_session
        self.commands: Dict[str, Callable[[], Awaitable[None]]] = {
            "/reset": self._handle_reset,
            "/debug": self._handle_debug,
            "/stats": self._handle_stats,
            "/help": self._handle_help,
            "/?": self._handle_help, # Alias for help
        }
        # Add aliases for quit commands
        self.quit_commands = {"/quit", "quit", "exit"}

    async def process(self, user_input: str) -> bool:
        """
        Processes the user input to see if it's a slash command.

        Returns:
            True if a command was found and handled, False otherwise.
        """
        cmd_str = user_input.lower().strip()

        if cmd_str in self.quit_commands:
            self.app.is_running = False
            return True

        if cmd_str in self.commands:
            await self.commands[cmd_str]()
            return True
        
        # Handle parameterized commands like /m
        if cmd_str.startswith('/m '):
            await self._handle_model_switch(user_input)
            return True

        return False

    async def _handle_reset(self):
        """Resets the current chat session."""
        self.session.reset()
        print("\n--- New conversation started ---")

    async def _handle_debug(self):
        """Toggles debug logging on and off."""
        from logging_config import toggle_debug_mode
        is_now_debug = toggle_debug_mode()
        print(f"[SYSTEM] Debug mode is now {'ON' if is_now_debug else 'OFF'}.")

    async def _handle_stats(self):
        """Displays simple stats about the current session."""
        stats = self.session.get_stats()
        print("\n--- Session Stats ---")
        print(f"  Model: {self.session.model}")
        print(f"  History Length: {stats['history_length']} messages")
        print("---------------------\n")

    async def _handle_help(self):
        """Displays a help message with available commands."""
        help_text = """
--- Gemini CLI Help ---
/help or /?      Show this help message.
/reset           Start a new conversation.
/quit            Exit the application.
/stats           Show session statistics.
/debug           Toggle debug logging.
/m <pro|flash>   Switch the model for the current session.
-----------------------
"""
        print(help_text)
        
    async def _handle_model_switch(self, user_input: str):
        """Switches the model used by the chat session."""
        from gemini_client import Models
        parts = user_input.strip().split(' ', 1)
        if len(parts) > 1:
            new_model_arg = parts[1].strip().lower()
            target_model = None
            if new_model_arg == 'flash':
                target_model = Models.FLASH
            elif new_model_arg == 'pro':
                target_model = Models.DEFAULT
            elif new_model_arg in Models.all():
                target_model = new_model_arg

            if target_model:
                self.session.model = target_model
                print(f"[SYSTEM] Model switched to: {self.session.model}")
            else:
                print(f"[ERROR] Invalid model. Available: {', '.join(Models.all())} or shorthands 'pro', 'flash'.")
        else:
            print("[SYSTEM] Usage: /m <model_name|pro|flash>")