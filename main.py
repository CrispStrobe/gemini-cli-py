#
# File: main.py
# Revision: 31
# Description: Implements snapshot safety feature and fixes UI bugs.
# - Integrates the now-functional GitService to automatically create a project
#   snapshot before every agentic turn.
# - Fixes the toolbar styling by replacing raw ANSI strings with a proper
#   `prompt_toolkit.styles.Style` object, removing the `<b>` tags.
#

import argparse
import asyncio
import traceback
import logging
from pathlib import Path
from enum import Enum, auto
import time
import re

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.formatted_text import to_formatted_text
from prompt_toolkit.styles import Style

from config import Config, load_final_config, validate_auth, USER_SETTINGS_DIR
from gemini_client import GeminiClient, Models
from chat_session import ChatSession
from tools.tool_io import ToolConfirmationOutcome
from logging_config import configure_logging
from at_command_processor import handle_at_command
from slash_command_processor import SlashCommandProcessor

class AppState(Enum):
    IDLE = auto()
    PROCESSING = auto()
    WAITING_FOR_CONFIRMATION = auto()

# FIX: Define a style for the toolbar instead of using raw tags
ui_style = Style.from_dict({
    'toolbar': 'bg:#444444 #ffffff bold',
})

def prompt_for_confirmation(confirmation_details: dict) -> ToolConfirmationOutcome:
    details_type = confirmation_details.get("type")
    prompt_str = ""
    if details_type == "edit":
        print("\n--- Proposed Change ---"); print(confirmation_details['diff']); print("---------------------\n")
        prompt_str += f"Apply this change to `{confirmation_details['path']}`?\n"
    elif details_type == "memory_write":
        prompt_str += f"Save the following fact to your global memory file (`{confirmation_details['path']}`)?"
        prompt_str += f"\n  Fact: \"{confirmation_details['fact']}\"\n"
    elif details_type == "exec":
        prompt_str += f"Execute shell command: `{confirmation_details['command']}`?\n"
    elif details_type == "write":
        prompt_str += f"Write/overwrite file: `{confirmation_details['path']}`?\n"
    else:
        prompt_str += f"Proceed with tool call: {confirmation_details}?\n"

    if details_type == 'exec':
        prompt_str += "  (y)es, (n)o, (a)lways"
    else:
        prompt_str += "  (y)es, (n)o"

    while True:
        response = input(f"\n[CONFIRMATION] {prompt_str}\n> ").lower().strip()
        if response in ['y', 'yes']: return ToolConfirmationOutcome.PROCEED_ONCE
        if response in ['n', 'no']: return ToolConfirmationOutcome.CANCEL
        if response in ['a', 'always'] and details_type == 'exec': return ToolConfirmationOutcome.PROCEED_ALWAYS
        print("Invalid input. Please enter a valid option.")

class AgenticREPL:
    """Manages the application state and main REPL loop."""
    def __init__(self, config: Config, initial_model: str, reset_session: bool):
        self.config = config
        self.logger = config.get_logger()
        # Initialize the GitService as a core component of the REPL
        self.git_service = config.get_git_service()
        self.is_running = True
        self.state = AppState.IDLE
        self.client: GeminiClient | None = None
        self.chat_session: ChatSession | None = None
        self.initial_model = initial_model
        self.reset_session = reset_session
        self.processing_task: asyncio.Task | None = None
        self.start_time = 0

    def _get_toolbar_text(self):
        """Generates the styled text for the bottom toolbar."""
        elapsed = f"{(time.time() - self.start_time):.1f}s" if self.start_time else ""
        model_name = self.chat_session.model if self.chat_session else ""
        
        if self.state == AppState.IDLE:
            text = f"[IDLE] Model: {model_name}"
        elif self.state == AppState.PROCESSING:
            text = f"[PROCESSING... {elapsed}] Model: {model_name}"
        elif self.state == AppState.WAITING_FOR_CONFIRMATION:
            text = f"[WAITING FOR CONFIRMATION] Model: {model_name}"
        else:
            text = "[UNKNOWN STATE]"
            
        # FIX: Use `to_formatted_text` for proper styling without raw tags
        return to_formatted_text(text, style='class:toolbar')

    async def _initialize(self):
        """Initializes client and creates the chat session."""
        self.client = GeminiClient(self.config)
        await self.client.initialize_user()
        self.chat_session = ChatSession(self.client, self.config, self.initial_model)
        
        if not self.reset_session:
            saved_history = self.logger.load_checkpoint()
            if saved_history:
                self.chat_session.history = saved_history
                print("--- Resumed session from checkpoint ---")
        else:
            print("--- Started new session (--reset flag used) ---")

    async def _run_turn(self, prompt):
        """Processes a single turn of the conversation."""
        self.state = AppState.PROCESSING
        self.start_time = time.time()
        try:
            # Create a snapshot before the turn begins
            snapshot_hash = self.git_service.create_file_snapshot(f"Snapshot before prompt: {prompt if isinstance(prompt, str) else prompt[0]['text'][:50]}...")
            if snapshot_hash:
                logging.info(f"Created pre-turn snapshot: {snapshot_hash}")

            turn_generator = self.chat_session.send_message_stream(prompt)
            async for event in turn_generator:
                if event['type'] == 'content':
                    print(event['value'], end='', flush=True)
                elif event['type'] == 'confirmation_request':
                    self.state = AppState.WAITING_FOR_CONFIRMATION
                    print()
                    outcome = await asyncio.to_thread(prompt_for_confirmation, event['value']['confirmation_details'])
                    self.state = AppState.PROCESSING
                    self.chat_session.provide_confirmation_response(event['value'], outcome)
                elif event['type'] == 'error':
                     print(f"\n[ERROR] An error occurred: {event['value']}")

            next_speaker = await self.chat_session.check_next_speaker()
            if next_speaker == "model":
                print(f"\n[AGENT] Continuing task (using model: {self.chat_session.model})...")
                await self._run_turn("Continue.")

        finally:
            self.state = AppState.IDLE
            self.start_time = 0

    async def run(self):
        """Runs the main REPL loop."""
        await self._initialize()
        command_processor = SlashCommandProcessor(self, self.chat_session)
        
        history_path = USER_SETTINGS_DIR / "prompt_history.txt"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        session = PromptSession(
            history=FileHistory(str(history_path)),
            bottom_toolbar=self._get_toolbar_text,
            refresh_interval=0.5,
            style=ui_style # Apply the style object to the session
        )
        
        print(f"\n--- Starting Interactive Chat (Model: {self.chat_session.model}) ---")
        print("Type /help for a list of commands.")

        while self.is_running:
            try:
                with patch_stdout():
                    user_input = await session.prompt_async("> ")
                
                if await command_processor.process(user_input):
                    continue

                if not user_input.strip(): continue

                print("\n--- Gemini ---")
                processed_prompt = await handle_at_command(user_input, self.config, self.chat_session.tool_registry)
                
                self.processing_task = asyncio.create_task(self._run_turn(processed_prompt))
                await self.processing_task
                
                self.logger.save_checkpoint(self.chat_session.history)
                print("\n----------------\n")
                
            except (KeyboardInterrupt, EOFError):
                self.is_running = False
                break

async def main():
    parser = argparse.ArgumentParser(description="A command-line interface for Google Gemini.")
    parser.add_argument("prompt", nargs='?', default=None, help="The initial prompt.")
    parser.add_argument("-m", "--model", choices=Models.all(), help="The model to use.")
    parser.add_argument("--reset", action="store_true", help="Start a new session, ignoring any saved checkpoint.")
    parser.add_argument("--debug", action="store_true", help="Enable detailed debug logging.")
    args = parser.parse_args()

    configure_logging(args.debug)
    
    repl_app: AgenticREPL | None = None
    try:
        config = load_final_config(args)
        error_message = validate_auth(config)
        if error_message: raise ValueError(error_message)
        
        if args.prompt:
            print("[INFO] Non-interactive mode is a future enhancement.")
            return

        repl_app = AgenticREPL(config, config.get_model(), args.reset)
        await repl_app.run()
        
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        traceback.print_exc()
    finally:
        if repl_app and repl_app.client:
            await repl_app.client.aclose()
        print("\nExiting application. Goodbye!")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, EOFError):
        print("\nExiting.")