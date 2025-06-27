#
# File: main.py
# Revision: 24
# Description: Implements conditional debug logging via a --debug flag
# and a /debug REPL command, using the new centralized logging_config module.
#

import argparse
import asyncio
import traceback
import logging

from gemini_client import GeminiClient, Models, ChatSession
from config import load_final_config, validate_auth
from tools.tool_io import ToolConfirmationOutcome
from logging_config import configure_logging, toggle_debug_mode # <-- Import new logging utils

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
        print(f"\n--- Starting Interactive Chat (Model: {chat_session.model}) ---")
        print("Type '/reset' to start a new conversation, '/debug' to toggle debug logs, or 'quit'/'exit' to end.")

        async def handle_send_message(prompt: str, turn_session: ChatSession):
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
                    outcome = prompt_for_confirmation(event['value']['confirmation_details'])
                    turn_session.current_turn.provide_confirmation_response(event['value'], outcome)

        async def run_turn(prompt: str, turn_session: ChatSession):
            await handle_send_message(prompt, turn_session)
            next_speaker = await turn_session.check_next_speaker()
            if next_speaker == "model":
                print(f"\n[AGENT] Continuing task (using model: {turn_session.model})...")
                await run_turn("Continue.", turn_session)
        
        if args.prompt:
            print(f"\n> {args.prompt}")
            print("\n--- Gemini ---")
            await run_turn(args.prompt, chat_session)
            logger.save_checkpoint(chat_session.history)
            print("\n----------------\n")

        while True:
            try:
                user_input = input("> ")
                if user_input.lower() in ["quit", "exit"]: break
                if user_input.lower() == '/reset':
                    chat_session.reset()
                    print("\n--- New conversation started ---")
                    continue
                if user_input.lower() == '/debug':
                    is_now_debug = toggle_debug_mode()
                    print(f"[SYSTEM] Debug mode is now {'ON' if is_now_debug else 'OFF'}.")
                    continue
                if not user_input.strip(): continue
                
                print("\n--- Gemini ---")
                await run_turn(user_input, chat_session)
                logger.save_checkpoint(chat_session.history)
                print("\n----------------\n")
            except KeyboardInterrupt:
                print("\nUse 'quit' or 'exit' to end the session.")
                continue
            
    except Exception as e:
        logging.error(f"An unexpected error occurred: {e}")
        traceback.print_exc()
    finally:
        if client:
            await client.aclose()
        print("\nExiting application. Goodbye!")

if __name__ == '__main__':
    asyncio.run(main())