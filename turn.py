#
# File: turn.py
# Revision: 4
# Description: Updates imports to use the new tool_io.py file.
#

import json
import logging
from typing import AsyncGenerator, Dict, Any, List

from core_tool_scheduler import CoreToolScheduler
from tools.tool_io import ToolConfirmationOutcome

class Turn:
    """
    Manages the lifecycle of a single conversational turn with the Gemini model.
    """
    def __init__(self, session: 'ChatSession', prompt: str):
        # Forward-declare ChatSession to avoid circular import
        self._session = session
        self._prompt = prompt
        self._turn_history: List[Dict[str, Any]] = []
        self._scheduler = CoreToolScheduler(self._session.tool_registry, self)
        # Event to signal when user confirmation is received
        self._confirmation_events: Dict[str, asyncio.Event] = {}
        self._confirmation_outcomes: Dict[str, ToolConfirmationOutcome] = {}

    async def run(self) -> AsyncGenerator[Dict[str, Any], None]:
        """Executes the turn and yields events, pausing for confirmation if needed."""
        # ... (initial history setup is the same)
        logging.info(f"Starting new turn with prompt: {self._prompt[:80]}...")
        self._turn_history = self._session.history + [{"role": "user", "parts": [{"text": self._prompt}]}]

        while True:
            # ... (API call setup is the same)
            request_body = {
                'contents': self._turn_history,
                'systemInstruction': self._session.system_instruction,
                'tools': [{'functionDeclarations': self._session.tool_registry.get_declarations()}]
            }
            final_payload = {
                "model": self._session.model,
                "project": self._session.client.project_id,
                "request": request_body
            }

            try:
                response_stream = await self._session.client._make_api_request(
                    'streamGenerateContent',
                    body=final_payload,
                    stream=True
                )
                function_calls = []
                model_response_text = ""
                # ... (stream parsing is the same)
                async for line in response_stream.aiter_lines():
                    if not line.startswith('data: '):
                        continue
                    try:
                        data = json.loads(line[6:])
                        part = data.get('response', {}).get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0]
                        if 'functionCall' in part:
                            fc = part['functionCall']
                            function_calls.append(fc)
                            # Yield the request event to the UI
                            yield {'type': 'tool_call_request', 'value': fc}
                        elif 'text' in part:
                            text = part.get('text', '')
                            model_response_text += text
                            yield {'type': 'content', 'value': text}
                    except (json.JSONDecodeError, KeyError, IndexError):
                        logging.warning(f"Could not parse stream chunk: {line}")
                
                if function_calls:
                    logging.info(f"Model requested {len(function_calls)} tool call(s).")
                    model_tool_request_parts = [{'functionCall': fc} for fc in function_calls]
                    self._turn_history.append({"role": "model", "parts": model_tool_request_parts})

                    # The scheduler will now handle the lifecycle, including pausing
                    # for confirmation via the `request_user_confirmations` method.
                    tool_results = await self._scheduler.schedule(function_calls)

                    self._turn_history.append({"role": "user", "parts": tool_results})
                    for result in tool_results:
                        yield {'type': 'tool_call_response', 'value': result}
                    continue
                else:
                    self._session.history = self._turn_history
                    self._session.history.append({"role": "model", "parts": [{"text": model_response_text}]})
                    logging.info("Turn finished with a text response.")
                    break
            except Exception as e:
                logging.error(f"Error during turn: {e}", exc_info=True)
                yield {'type': 'error', 'value': str(e)}
                break

    async def request_user_confirmations(self, calls_to_confirm: List[Dict]):
        """Yields confirmation requests to the UI and waits for responses."""
        for call in calls_to_confirm:
            call_id = call["request"]["name"] + str(call["request"].get("args", {}))
            self._confirmation_events[call_id] = asyncio.Event()
            
            yield {'type': 'confirmation_request', 'value': call}
            
            # Pause execution of this turn until the event is set
            await self._confirmation_events[call_id].wait()
            
            outcome = self._confirmation_outcomes[call_id]
            await self._scheduler.handle_confirmation_response(call["request"], outcome)

    def provide_confirmation_response(self, call_value: Dict, outcome: ToolConfirmationOutcome):
        """Receives confirmation from the UI and unpauses the turn."""
        call_id = call_value["request"]["name"] + str(call_value["request"].get("args", {}))
        if call_id in self._confirmation_events:
            self._confirmation_outcomes[call_id] = outcome
            self._confirmation_events[call_id].set()