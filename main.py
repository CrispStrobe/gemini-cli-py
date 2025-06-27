#
# File: main.py
# Revision: 23
# Description: Final, stable version with all bug fixes, including the --reset
# command-line argument.
#

import argparse
import asyncio
import traceback
import logging

# Centralized imports
from gemini_client import GeminiClient, Models, ChatSession
from config import load_final_config, validate_auth, Config
from tools.tool_io import ToolConfirmationOutcome
from utils.next_speaker_checker import NextSpeaker
from turn import Turn
from tool_registry import ToolRegistry
from typing import List, Dict, Any, AsyncGenerator


# Setup logging
logging.basicConfig(level=logging.DEBUG, format='[%(asctime)s] [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s')

def prompt_for_confirmation(confirmation_details: dict) -> ToolConfirmationOutcome:
    # ... (function is unchanged)
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
    if details_type == 'exec': prompt_str += "  (y)es, (n)o, (a)lways"
    else: prompt_str += "  (y)es, (n)o"
    while True:
        response = input(f"\n[CONFIRMATION] {prompt_str}\n").lower().strip()
        if response in ['y', 'yes']: return ToolConfirmationOutcome.PROCEED_ONCE
        if response in ['n', 'no']: return ToolConfirmationOutcome.CANCEL
        if response in ['a', 'always'] and details_type == 'exec': return ToolConfirmationOutcome.PROCEED_ALWAYS
        elif response in ['a', 'always']:
             print("The 'always' option is only available for shell commands. Please choose 'y' or 'n'.")
             continue
        print("Invalid input. Please enter a valid option.")

# Patch ChatSession to add the reset method
def _chat_session_reset(self):
    logging.info("Resetting chat session history.")
    self._initialize_chat_context()
ChatSession.reset = _chat_session_reset

async def main():
    parser = argparse.ArgumentParser(description="A command-line interface for Google Gemini using OAuth.")
    parser.add_argument("prompt", nargs='?', default=None, help="The initial prompt to send to the model before starting the interactive session.")
    parser.add_argument("-m", "--model", default=Models.DEFAULT, choices=Models.all(), help="The model to use.")
    # This ensures the --reset flag is recognized
    parser.add_argument("--reset", action="store_true", help="Start a new session and ignore any saved checkpoint.")
    args = parser.parse_args()

    client: GeminiClient | None = None
    chat_session: ChatSession | None = None
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
        print(f"\n--- Starting Interactive Chat (Model: {chat_session.model}) ---") # Use session model
        print("Type '/reset' to start a new conversation, or 'quit'/'exit' to end the session.")

        # ... (handle_send_message and run_turn functions are unchanged)
        async def handle_send_message(prompt: str):
            # Pass the chat session to the API request context
            turn_generator = chat_session.send_message(prompt)
            citations = None
            async for event in turn_generator:
                if event['type'] == 'content': print(event['value'], end='', flush=True)
                elif event['type'] == 'citations': citations = event['value']
                elif event['type'] == 'tool_call_response':
                    result_name = event['value']['functionResponse']['name']
                    result_content = event['value']['functionResponse']['response'].get('content', {})
                    if 'error' in result_content: print(f"\n[AGENT] Error from {result_name}: {result_content['error']}")
                    else: print(f"\n[AGENT] Tool {result_name} executed.")
                elif event['type'] == 'error': print(f"\n[ERROR] An error occurred: {event['value']}")
                elif event['type'] == 'confirmation_request':
                    print()
                    outcome = prompt_for_confirmation(event['value']['confirmation_details'])
                    if chat_session.current_turn: 
                        chat_session.current_turn.provide_confirmation_response(event['value'], outcome)
                        # Continue the turn after providing confirmation response
                        continue
            if citations:
                print("\n\n--- Sources ---")
                for i, ref in enumerate(citations['references']): print(f"[{i+1}] {ref.get('title', 'N/A')}: {ref.get('uri', 'N/A')}")
        
        async def run_turn(prompt: str):
            # Pass the chat session to API requests made within the turn
            await handle_send_message(prompt)
            next_speaker = await chat_session.check_next_speaker()
            if next_speaker == "model":
                print(f"\n[AGENT] Continuing task (using model: {chat_session.model})...")
                await run_turn("Continue.")
        
        if args.prompt:
            print(f"\n> {args.prompt}")
            print("\n--- Gemini ---")
            await run_turn(args.prompt)
            if chat_session and config: config.get_logger().save_checkpoint(chat_session.history)
            print("\n----------------\n")

        while True:
            try:
                user_input = input("> ")
                if user_input.lower() in ["quit", "exit"]: break
                if user_input.lower() == '/reset':
                    chat_session.reset()
                    print("\n--- New conversation started ---")
                    continue
                if not user_input.strip(): continue
                print("\n--- Gemini ---")
                await run_turn(user_input)
                if chat_session and config: config.get_logger().save_checkpoint(chat_session.history)
                print("\n----------------\n")
            except KeyboardInterrupt:
                print("\nUse 'quit' or 'exit' to end the session.")
                continue
    except KeyboardInterrupt:
        print("\n\nExiting application. Goodbye!")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
        traceback.print_exc()
    finally:
        if client:
            await client.aclose()

if __name__ == '__main__':
    asyncio.run(main())