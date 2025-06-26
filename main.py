#
# File: main.py
# Revision: 9 (Implements an interactive REPL)
# Description: Main application entry point for the Gemini CLI.
#

import argparse
import asyncio
import traceback
import logging

from gemini_client import GeminiClient, Models
from config import load_final_config, validate_auth, Config

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

async def main():
    """Parses arguments and runs the interactive chat REPL."""
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

        # If an initial prompt was provided, run it first.
        if args.prompt:
            print(f"\n> {args.prompt}")
            print("\n--- Gemini ---")
            async for event in chat_session.send_message(args.prompt):
                if event['type'] == 'content': print(event['value'], end='', flush=True)
                elif event['type'] == 'tool_call_request': print(f"\n[AGENT] Calling Tool: {event['value']['name']} with args: {event['value'].get('args', {})}")
                elif event['type'] == 'tool_call_response': print(f"\n[AGENT] Got Result from {event['value']['functionResponse']['name']}: {event['value']['functionResponse']['response'].get('content', {})}")
                elif event['type'] == 'error': print(f"\n[ERROR] An error occurred: {event['value']}")
            print("\n----------------\n")

        # --- REPL (Read-Eval-Print Loop) ---
        while True:
            try:
                user_input = input("> ")
                if user_input.lower() in ["quit", "exit"]:
                    print("Ending chat session. Goodbye!")
                    break
                
                if not user_input.strip():
                    continue

                print("\n--- Gemini ---")
                async for event in chat_session.send_message(user_input):
                    if event['type'] == 'content':
                        print(event['value'], end='', flush=True)
                    elif event['type'] == 'tool_call_request':
                        tool_name = event['value']['name']
                        tool_args = event['value'].get('args', {})
                        print(f"\n[AGENT] Calling Tool: {tool_name} with args: {tool_args}")
                    elif event['type'] == 'tool_call_response':
                        result_name = event['value']['functionResponse']['name']
                        result_content = event['value']['functionResponse']['response'].get('content', {})
                        print(f"\n[AGENT] Got Result from {result_name}: {result_content}")
                    elif event['type'] == 'error':
                        print(f"\n[ERROR] An error occurred: {event['value']}")
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

if __name__ == '__main__':
    asyncio.run(main())