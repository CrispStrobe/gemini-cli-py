#
# File: chat_session.py
# Revision: 2
# Description: Fixes the construction of the request_components dictionary
# to no longer include the 'model' key, as this is now handled dynamically
# inside GeminiClient._make_api_request to enable model fallback on retry.
#

import json
import logging
import asyncio
from typing import AsyncGenerator, Dict, Any, List, Union, TYPE_CHECKING

from config import Config
from tool_registry import ToolRegistry
from core_tool_scheduler import CoreToolScheduler
from tools.tool_io import ToolConfirmationOutcome
from prompts import get_core_system_prompt
from services.memory_discovery import load_memory
from utils.next_speaker_checker import check_next_speaker, NextSpeaker

if TYPE_CHECKING:
    from gemini_client import GeminiClient

# The prompt can be a simple string or a list of content parts from the @-processor
PromptType = Union[str, List[Dict[str, str]]]

class ChatSession:
    """
    Manages a single, stateful conversation with the Gemini model.
    """
    def __init__(self, client: 'GeminiClient', config: Config, model: str):
        self.client = client
        self.config = config
        self.model = model
        self.history: List[Dict[str, Any]] = []
        self.tool_registry = ToolRegistry(self.config)
        self._scheduler = CoreToolScheduler(self.tool_registry)

        # State for handling user confirmations for tool calls
        self._pending_confirmation: Dict[str, Any] | None = None
        self._confirmation_received_event = asyncio.Event()
        self._confirmation_outcome: ToolConfirmationOutcome | None = None
        
        self._initialize_chat_context()

    def _initialize_chat_context(self):
        logging.debug("Initializing chat context...")
        target_dir = self.config.get_target_dir()
        core_prompt = get_core_system_prompt(target_dir)
        memory_content = load_memory(target_dir)
        full_prompt_text = core_prompt
        if memory_content:
            full_prompt_text += "\n\n# User-Provided Context\n"
            full_prompt_text += "You MUST use the following context to augment your knowledge and follow any directives given.\n"
            full_prompt_text += memory_content
        self.history = [
            {"role": "user", "parts": [{"text": full_prompt_text}]},
            {"role": "model", "parts": [{"text": "Understood. I will follow these instructions and use my tools to assist you."}]}
        ]
        logging.debug("Chat context initialized successfully.")

    async def send_message_stream(self, prompt: PromptType) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Handles a single conversational turn, including tool calls and responses.
        This method replaces the logic previously in the Turn class.
        """
        if isinstance(prompt, str):
            user_parts = [{"text": prompt}]
        else:
            user_parts = prompt

        turn_history = self.history + [{"role": "user", "parts": user_parts}]

        while True:
            # FIX: Build request_components without the model. The model will be added
            # inside _make_api_request to ensure the correct one is used on retries.
            request_components = {"project": self.client.project_id, "request": {'contents': turn_history}}
            if tools := self.tool_registry.get_declarations():
                request_components["request"]['tools'] = tools

            try:
                response_stream = await self.client._make_api_request(
                    'streamGenerateContent', request_components=request_components,
                    stream=True, chat_session=self
                )
                function_calls, model_response_text = [], ""
                async for line in response_stream.aiter_lines():
                    if not line.startswith('data: '): continue
                    try:
                        data = json.loads(line[6:])
                        part = data.get('response', {}).get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0]
                        if 'functionCall' in part:
                            function_calls.append(part['functionCall'])
                        elif 'text' in part:
                            text = part.get('text', '')
                            model_response_text += text
                            yield {'type': 'content', 'value': text}
                    except (json.JSONDecodeError, KeyError, IndexError):
                        logging.warning(f"Could not parse stream chunk: {line}")

                if function_calls:
                    turn_history.append({"role": "model", "parts": [{'functionCall': fc} for fc in function_calls]})
                    tool_results = await self._scheduler.schedule(function_calls)
                    awaiting_approval = tool_results.get("awaiting_approval", [])

                    if awaiting_approval:
                        call_to_confirm = awaiting_approval[0]
                        self._pending_confirmation = call_to_confirm
                        self._confirmation_received_event.clear()
                        yield {'type': 'confirmation_request', 'value': call_to_confirm}
                        await self._confirmation_received_event.wait()
                        await self._scheduler.handle_confirmation_and_execute(
                            self._pending_confirmation['request'], self._confirmation_outcome
                        )

                    executed_results = await self._scheduler.get_executed_results()
                    if executed_results:
                        turn_history.append({"role": "user", "parts": executed_results})
                        for result in executed_results:
                            yield {'type': 'tool_call_response', 'value': result}
                        continue # Continue the loop to get the model's final response

                self.history = turn_history
                if model_response_text.strip():
                    self.history.append({"role": "model", "parts": [{"text": model_response_text}]})
                break
            except Exception as e:
                logging.error(f"Error during turn: {e}", exc_info=True)
                yield {'type': 'error', 'value': str(e)}
                break

    def provide_confirmation_response(self, call_value: Dict, outcome: ToolConfirmationOutcome):
        """Called by the UI to provide the user's confirmation for a tool call."""
        if self._pending_confirmation and self._pending_confirmation == call_value:
            self._confirmation_outcome = outcome
            self._confirmation_received_event.set()

    async def check_next_speaker(self) -> NextSpeaker:
        if not self.history: return "user"
        last_message = self.history[-1]
        if (last_message.get("role") == "user" and any("functionResponse" in p for p in last_message.get("parts",[]))):
            return "model"
        return await check_next_speaker(self)

    def reset(self):
        logging.info("Resetting chat session history.")
        self._initialize_chat_context()

    def get_stats(self) -> Dict[str, Any]:
        return {"history_length": len(self.history)}

    async def _handle_flash_fallback(self) -> bool:
        from gemini_client import Models
        if self.model == Models.FLASH: return False
        print(f"\n[INFO] ⚡ Persistent rate-limiting detected. Temporarily switching from {self.model} to {Models.FLASH}.")
        logging.warning(f"Switching model from {self.model} to {Models.FLASH} due to 429 errors.")
        self.model = Models.FLASH
        return True