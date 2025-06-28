#
# File: main.py
# Revision: 27
# Description:
# Integrates the new at_command_processor. Before sending a prompt to the
# Turn, it's now pre-processed to handle @-file commands.
#

import argparse
import asyncio
import traceback
import logging
from pathlib import Path
import re # Import re for at_command_processor integration

# Import prompt_toolkit components
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory

from gemini_client import GeminiClient, Models, ChatSession
from config import load_final_config, validate_auth, USER_SETTINGS_DIR
from tools.tool_io import ToolConfirmationOutcome
from logging_config import configure_logging, toggle_debug_mode
from at_command_processor import handle_at_command # <-- Import the new processor

# ... (prompt_for_confirmation function remains the same) ...
def prompt_for_confirmation(confirmation_details: dict) -> ToolConfirmationOutcome:
    """Prompts the user to confirm a tool action."""
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
        # Use standard input for confirmation prompts, as the main prompt is now async
        response = input(f"\n[CONFIRMATION] {prompt_str}\n> ").lower().strip()
        if response in ['y', 'yes']: return ToolConfirmationOutcome.PROCEED_ONCE
        if response in ['n', 'no']: return ToolConfirmationOutcome.CANCEL
        if response in ['a', 'always'] and details_type == 'exec': return ToolConfirmationOutcome.PROCEED_ALWAYS
        print("Invalid input. Please enter a valid option.")

async def main():
    parser = argparse.ArgumentParser(description="A command-line interface for Google Gemini.")
    parser.add_argument("prompt", nargs='?', default=None, help="The initial prompt.")
    parser.add_argument("-m", "--model", default=Models.DEFAULT, choices=Models.all(), help="The model to use.")
    parser.add_argument("--reset", action="store_true", help="Start a new session, ignoring any saved checkpoint.")
    parser.add_argument("--debug", action="store_true", help="Enable detailed debug logging.")
    args = parser.parse_args()

    # Configure logging based on the --debug flag
    configure_logging(args.debug)

    client: GeminiClient | None = None
    try:
        logging.info("Loading configuration...")
        config = load_final_config()
        logger = config.get_logger()
        config.get_git_service()

        error_message = validate_auth(config)
        if error_message: raise ValueError(error_message)

        logging.info("Initializing Gemini OAuth Client...")
        client = GeminiClient(config)
        await client.initialize_user()

        chat_session = client.start_chat(config, args.model)

        if not args.reset:
            saved_history = logger.load_checkpoint()
            if saved_history:
                chat_session.history = saved_history
                print("--- Resumed session from checkpoint ---")
        else:
            print("--- Started new session (--reset flag used) ---")

        logging.info("Client initialized successfully!")

        async def handle_send_message(prompt, turn_session: ChatSession): # prompt type updated implicitly
            turn_generator = turn_session.send_message(prompt)
            async for event in turn_generator:
                if event['type'] == 'content':
                    print(event['value'], end='', flush=True)
                elif event['type'] == 'tool_call_response':
                    response_data = event['value']['functionResponse']['response']
                    tool_name = event['value']['functionResponse']['name']
                    if 'error' in response_data:
                        print(f"\n[AGENT] Error from {tool_name}: {response_data['error']}")
                    else:
                        print(f"\n[AGENT] Tool {tool_name} executed.")
                elif event['type'] == 'error':
                    print(f"\n[ERROR] An error occurred: {event['value']}")
                elif event['type'] == 'confirmation_request':
                    print() # Newline for clarity
                    # Run blocking input in a separate thread to not block asyncio event loop
                    outcome = await asyncio.to_thread(prompt_for_confirmation, event['value']['confirmation_details'])
                    turn_session.current_turn.provide_confirmation_response(event['value'], outcome)

        async def run_turn(prompt, turn_session: ChatSession): # prompt type updated implicitly
            await handle_send_message(prompt, turn_session)
            next_speaker = await turn_session.check_next_speaker()
            if next_speaker == "model":
                print(f"\n[AGENT] Continuing task (using model: {turn_session.model})...")
                # When continuing, the prompt is a simple instruction, not a user input
                await run_turn("Continue.", turn_session)

        # Handle non-interactive mode
        if args.prompt:
            print(f"\n> {args.prompt}")
            print("\n--- Gemini ---")
            processed_prompt = await handle_at_command(args.prompt, config, chat_session.tool_registry)
            await run_turn(processed_prompt, chat_session)
            logger.save_checkpoint(chat_session.history)
            print("\n----------------\n")
            return # Exit after non-interactive run

        # Setup for interactive REPL mode
        print(f"\n--- Starting Interactive Chat (Model: {chat_session.model}) ---")
        print("Type '/reset' to start a new conversation, '/debug' to toggle debug logs, or 'quit'/'exit' to end.")
        
        history_path = USER_SETTINGS_DIR / "prompt_history.txt"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        session = PromptSession(history=FileHistory(str(history_path)))

        # Main REPL loop using prompt_toolkit
        while True:
            try:
                user_input = await session.prompt_async("> ")
                cmd = user_input.lower().strip()

                if cmd in ["quit", "exit", "/quit"]: break

                if cmd.startswith('/m '):
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
                            chat_session.model = target_model
                            print(f"[SYSTEM] Model switched to: {chat_session.model}")
                        else:
                            print(f"[ERROR] Invalid model. Available: {', '.join(Models.all())} or shorthands 'pro', 'flash'.")
                    else:
                        print("[SYSTEM] Usage: /m <model_name|pro|flash>")
                    continue

                if cmd == '/reset':
                    chat_session.reset()
                    print("\n--- New conversation started ---")
                    continue

                if cmd == '/debug':
                    is_now_debug = toggle_debug_mode()
                    print(f"[SYSTEM] Debug mode is now {'ON' if is_now_debug else 'OFF'}.")
                    continue

                if not user_input.strip(): continue

                print("\n--- Gemini ---")
                # Pre-process the prompt for @-commands before sending
                processed_prompt = await handle_at_command(user_input, config, chat_session.tool_registry)
                await run_turn(processed_prompt, chat_session)
                logger.save_checkpoint(chat_session.history)
                print("\n----------------\n")

            except (KeyboardInterrupt, EOFError):
                # Handle Ctrl+C gracefully in the prompt, Ctrl+D to exit
                break

    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        traceback.print_exc()
    finally:
        if client:
            await client.aclose()
        print("\nExiting application. Goodbye!")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, EOFError):
        # Prevent traceback on graceful exit
        print("\nExiting.")