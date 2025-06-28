#
# File: turn.py
# Revision: 18
# Description: 
# The Turn class is updated to accept a structured prompt (a list of parts)
# from the at_command_processor, in addition to a simple string.
#

import json
import logging
import asyncio
from typing import AsyncGenerator, Dict, Any, List, Literal, TYPE_CHECKING, Union

from core_tool_scheduler import CoreToolScheduler
from tools.tool_io import ToolConfirmationOutcome

if TYPE_CHECKING:
    from gemini_client import ChatSession

PromptType = Union[str, List[Dict[str, str]]]

class Turn:
    def __init__(self, session: 'ChatSession', prompt: PromptType):
        self._session = session
        self._prompt = prompt
        self._turn_history: List[Dict[str, Any]] = []
        self._scheduler = CoreToolScheduler(self._session.tool_registry)
        # Event to signal when a confirmation has been received from the user
        self._confirmation_received_event = asyncio.Event()
        self._pending_confirmation: Dict[str, Any] | None = None
        self._confirmation_outcome: ToolConfirmationOutcome | None = None

    async def run(self) -> AsyncGenerator[Dict[str, Any], None]:
        prompt_str_for_log = str(self._prompt)[:80]
        logging.info(f"Starting new turn with prompt: {prompt_str_for_log}...")
        
        if isinstance(self._prompt, str):
            user_parts = [{"text": self._prompt}]
        else: # It's a list of parts
            user_parts = self._prompt

        self._turn_history = self._session.history + [{"role": "user", "parts": user_parts}]

        while True:
            tools = self._session.tool_registry.get_declarations()
            request_body = {'contents': self._turn_history}
            if tools:
                request_body['tools'] = tools

            # This payload is now built inside the retry-safe _make_api_request
            request_components = {"project": self._session.client.project_id, "request": request_body}

            try:
                response_stream = await self._session.client._make_api_request(
                    'streamGenerateContent',
                    request_components=request_components,
                    stream=True,
                    chat_session=self._session
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
                    self._turn_history.append({"role": "model", "parts": [{'functionCall': fc} for fc in function_calls]})
                    tool_results = await self._scheduler.schedule(function_calls)
                    awaiting_approval = tool_results.get("awaiting_approval", [])

                    if awaiting_approval:
                        # For simplicity, we handle one confirmation at a time.
                        call_to_confirm = awaiting_approval[0]
                        self._pending_confirmation = call_to_confirm
                        self._confirmation_received_event.clear()
                        yield {'type': 'confirmation_request', 'value': call_to_confirm}
                        await self._confirmation_received_event.wait() # Pause here
                        await self._scheduler.handle_confirmation_and_execute(
                            self._pending_confirmation['request'], self._confirmation_outcome
                        )

                    executed_results = await self._scheduler.get_executed_results()
                    if executed_results:
                        self._turn_history.append({"role": "user", "parts": executed_results})
                        for result in executed_results:
                            yield {'type': 'tool_call_response', 'value': result}
                        logging.debug("Tool calls processed. Continuing turn.")
                        continue

                self._session.history = self._turn_history
                if model_response_text.strip():
                    self._session.history.append({"role": "model", "parts": [{"text": model_response_text}]})
                logging.info("Turn finished.")
                break
            except Exception as e:
                logging.error(f"Error during turn: {e}", exc_info=True)
                yield {'type': 'error', 'value': str(e)}
                break

    def provide_confirmation_response(self, call_value: Dict, outcome: ToolConfirmationOutcome):
        if self._pending_confirmation and self._pending_confirmation == call_value:
            self._confirmation_outcome = outcome
            self._confirmation_received_event.set() # Resume the run loop