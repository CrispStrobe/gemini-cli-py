#
# File: turn.py
# Revision: 15
# Description: Simplifies the main execution loop. After handling user
# confirmations, it now explicitly fetches all executed tool results
# from the stateful scheduler before continuing the turn.
#

import json
import logging
import asyncio
from typing import AsyncGenerator, Dict, Any, List, Literal, TYPE_CHECKING

from core_tool_scheduler import CoreToolScheduler
from tools.tool_io import ToolConfirmationOutcome

if TYPE_CHECKING:
    from gemini_client import ChatSession

class Turn:
    def __init__(self, session: 'ChatSession', prompt: str):
        self._session = session
        self._prompt = prompt
        self._turn_history: List[Dict[str, Any]] = []
        self._scheduler = CoreToolScheduler(self._session.tool_registry, self)
        self._confirmation_events: Dict[str, asyncio.Event] = {}
        self._confirmation_outcomes: Dict[str, ToolConfirmationOutcome] = {}

    async def run(self) -> AsyncGenerator[Dict[str, Any], None]:
        logging.info(f"Starting new turn with prompt: {self._prompt[:80]}...")
        self._turn_history = self._session.history + [{"role": "user", "parts": [{"text": self._prompt}]}]

        while True:
            tools = self._session.tool_registry.get_declarations()
            request_body = {'contents': self._turn_history}
            if tools: 
                request_body['tools'] = tools
            
            final_payload = {
                "model": self._session.model, 
                "project": self._session.client.project_id, 
                "request": request_body
            }

            try:
                response_stream = await self._session.client._make_api_request(
                    'streamGenerateContent', body=final_payload, stream=True, chat_session=self._session
                )
                
                function_calls, model_response_text, citations = [], "", None
                
                async for line in response_stream.aiter_lines():
                    if not line.startswith('data: '): continue
                    try:
                        data = json.loads(line[6:])
                        if 'groundingMetadata' in data.get('response', {}):
                            citations = {'type': 'citations', 'value': data['response']['groundingMetadata']}
                        
                        part = data.get('response', {}).get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0]
                        
                        if 'functionCall' in part:
                            original_function_call = part['functionCall']
                            function_calls.append({
                                'callId': f"{original_function_call.get('name', 'unknown')}-{len(function_calls)}",
                                'name': original_function_call.get('name'),
                                'args': original_function_call.get('args', {})
                            })
                        elif 'text' in part:
                            text = part.get('text', '')
                            model_response_text += text
                            yield {'type': 'content', 'value': text}
                            
                    except (json.JSONDecodeError, KeyError, IndexError):
                        logging.warning(f"Could not parse stream chunk: {line}")
                
                if citations: yield citations
                
                if function_calls:
                    model_tool_request_parts = [{'functionCall': {'name': fc['name'], 'args': fc['args']}} for fc in function_calls]
                    self._turn_history.append({"role": "model", "parts": model_tool_request_parts})
                    
                    # Schedule tools, which executes auto-approved ones.
                    tool_results = await self._scheduler.schedule(function_calls)
                    
                    # Handle any tools that require user confirmation.
                    awaiting_approval_calls = tool_results.get("awaiting_approval", [])
                    if awaiting_approval_calls:
                        for call in awaiting_approval_calls:
                            yield {'type': 'confirmation_request', 'value': call}
                            # The main loop will call provide_confirmation_response, which now executes the tool.
                    
                    # --- CRITICAL FIX ---
                    # After handling confirmations, get all executed results from the stateful scheduler.
                    executed_results = await self._scheduler.get_executed_results()
                    
                    if executed_results:
                        self._turn_history.append({"role": "user", "parts": executed_results})
                        for result in executed_results: 
                            yield {'type': 'tool_call_response', 'value': result}
                        logging.debug("Tool calls processed. Continuing turn to send results to model.")
                        continue # Continue the loop to send results back to the model.

                # If we get here, the turn is complete.
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
        # This method in Turn now just acts as a passthrough to the scheduler
        asyncio.create_task(
             self._scheduler.handle_confirmation_response(call_value["request"], outcome)
        )