#
# File: main.py
# Revision: 12
# Description: Updates imports to use the new tool_io.py file.
#

import argparse
import asyncio
import traceback
import logging

from gemini_client import GeminiClient, Models
from config import load_final_config, validate_auth, Config
from tools.tool_io import ToolConfirmationOutcome 

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def prompt_for_confirmation(confirmation_details: dict) -> ToolConfirmationOutcome:
    """Displays a confirmation prompt to the user and gets a response."""
    details_type = confirmation_details.get("type")
    
    if details_type == "exec":
        prompt_str = f"Execute shell command: `{confirmation_details['command']}`?\n"
    elif details_type == "write":
        prompt_str = f"Write/overwrite file: `{confirmation_details['path']}`?\n"
    else:
        prompt_str = f"Proceed with tool call: {confirmation_details}?\n"
        
    prompt_str += "  (y)es, (n)o, (a)lways"
    
    while True:
        response = input(f"{prompt_str}\n> ").lower().strip()
        if response in ['y', 'yes']:
            return ToolConfirmationOutcome.PROCEED_ONCE
        if response in ['n', 'no']:
            return ToolConfirmationOutcome.CANCEL
        if response in ['a', 'always']:
            return ToolConfirmationOutcome.PROCEED_ALWAYS
        print("Invalid input. Please enter 'y', 'n', or 'a'.")


async def main():
    # ... (arg parsing and client setup is the same)
    parser = argparse.ArgumentParser(description="A command-line interface for Google Gemini using OAuth.")
    parser.add_argument("prompt", nargs='?', default=None, help="The initial prompt to send to the model before starting the interactive session.")
    parser.add_argument("-m", "--model", default=Models.DEFAULT, choices=Models.all(), help="The model to use.")
    args = parser.parse_args()

    client = None
    try:
        logging.info("Loading configuration...")
        config = load_final_config()

        error_message = validate_auth(config)
        if error_message:
            raise ValueError(error_message)

        logging.info("Initializing Gemini OAuth Client...")
        client = GeminiClient(config)
        await client.initialize_user()

        chat_session = client.start_chat(config, args.model)
        logging.info("Client initialized successfully!")

        print(f"\n--- Starting Interactive Chat (Model: {args.model}) ---")
        print("Type 'quit' or 'exit' to end the session.")

        # --- Main REPL Loop ---
        active_turn = None
        
        # Function to handle the message sending and event loop
        async def handle_send_message(prompt):
            nonlocal active_turn
            # The patched ChatSession will set its own current_turn
            turn_generator = chat_session.send_message(prompt)
            async for event in turn_generator:
                if event['type'] == 'content':
                    print(event['value'], end='', flush=True)
                elif event['type'] == 'tool_call_request':
                    print(f"\n[AGENT] Requested Tool: {event['value']['name']} with args: {event['value'].get('args', {})}")
                elif event['type'] == 'tool_call_response':
                    # This might be too verbose, can be simplified later
                    result_name = event['value']['functionResponse']['name']
                    result_content = event['value']['functionResponse']['response'].get('content', {})
                    print(f"\n[AGENT] Got Result from {result_name}: {result_content.get('stdout', result_content)}")
                elif event['type'] == 'error':
                    print(f"\n[ERROR] An error occurred: {event['value']}")
                elif event['type'] == 'confirmation_request':
                    print() # Newline for the prompt
                    outcome = prompt_for_confirmation(event['value']['confirmation_details'])
                    # Use the reference to the current turn on the session
                    if chat_session.current_turn:
                        chat_session.current_turn.provide_confirmation_response(event['value'], outcome)

        # Handle initial prompt if provided
        if args.prompt:
            print(f"\n> {args.prompt}")
            print("\n--- Gemini ---")
            await handle_send_message(args.prompt)
            print("\n----------------\n")

        while True:
            try:
                user_input = input("> ")
                if user_input.lower() in ["quit", "exit"]:
                    print("Ending chat session. Goodbye!")
                    break
                if not user_input.strip():
                    continue

                print("\n--- Gemini ---")
                await handle_send_message(user_input)
                print("\n----------------\n")

            except KeyboardInterrupt:
                print("\nUse 'quit' or 'exit' to end the session.")
                continue

    except KeyboardInterrupt:
        print("\n\nExiting application.")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
        traceback.print_exc()
    finally:
        if client:
            await client.aclose()

# This is a bit of a hack to avoid circular dependencies.
# We patch the ChatSession class definition before it's used.
from turn import Turn
from typing import List, Dict, Any, AsyncGenerator
# We must patch the class on the module where it's defined
import gemini_client

class PatchedChatSession:
    def __init__(self, client: GeminiClient, config: Config, model: str):
        self.client = client
        self.config = config
        self.model = model
        self.history: List[Dict[str, Any]] = []
        self.tool_registry = client.config.get_tool_registry() # Get it from config
        self.system_instruction = self._initialize_chat_history()
        self.current_turn: Turn | None = None

    def _initialize_chat_history(self) -> Dict:
        system_prompt_text = (
            "You are an interactive CLI agent. Your primary goal is to help users safely "
            "and efficiently by utilizing your available tools."
        )
        self.history = [
            {"role": "user", "parts": [{"text": "Hello, let's get started."}]},
            {"role": "model", "parts": [{"text": "OK. I'm ready to help."}]}
        ]
        logging.info("Chat history initialized.")
        return {"role": "user", "parts": [{"text": system_prompt_text}]}

    async def send_message(self, prompt: str) -> AsyncGenerator[Dict[str, Any], None]:
        self.current_turn = Turn(session=self, prompt=prompt)
        async for event in self.current_turn.run():
            yield event

gemini_client.ChatSession = PatchedChatSession
# We also need to patch the config to have get_tool_registry
from tool_registry import ToolRegistry
def get_tool_registry(self):
    if not hasattr(self, '_tool_registry'):
        self._tool_registry = ToolRegistry(self)
    return self._tool_registry
Config.get_tool_registry = get_tool_registry


if __name__ == '__main__':
    asyncio.run(main())