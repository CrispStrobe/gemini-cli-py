#
# File: utils/next_speaker_checker.py
# Revision: 4
# Description: Updates the call to _make_api_request to use `request_components`
# instead of a pre-built `body`, enabling the retry-safe model fallback logic.
#

import logging
import json
from typing import Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from gemini_client import ChatSession

NextSpeaker = Literal["user", "model"]

# This is the JSON schema the model must follow for its response.
NEXT_SPEAKER_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "speaker": {
            "type": "STRING",
            "description": "The next speaker, which must be 'user' or 'model'.",
            "enum": ["user", "model"]
        },
        "reason": {
            "type": "STRING",
            "description": "A brief justification for why that speaker should go next."
        }
    },
    "required": ["speaker", "reason"]
}

NEXT_SPEAKER_PROMPT = """
Analyze the conversation and determine who should speak next: 'user' or the 'model'.

Rules:
1. If the model just completed a task and is waiting for the user's next request, answer 'user'.
2. If the model is in the middle of a multi-step task (e.g., it used a tool and needs to process the result), answer 'model'.
3. If the model made tool calls but hasn't provided a final response about the results, answer 'model'.
4. If the conversation is at a natural stopping point, answer 'user'.

Your response MUST be a valid JSON object by calling the `determine_next_speaker` function with the correct arguments.
"""

async def check_next_speaker(session: 'ChatSession') -> NextSpeaker:
    """Calls the Gemini API with a special prompt to determine who should speak next."""
    logging.debug("Checking for next speaker...")
    
    try:
        # Build the request with the conversation history plus the next speaker prompt
        request_contents = session.history + [{"role": "user", "parts": [{"text": NEXT_SPEAKER_PROMPT}]}]
        
        # Build the API request with a tool that forces a JSON response
        request_body = {
            'contents': request_contents,
            'tools': [{
                "functionDeclarations": [{
                    "name": "determine_next_speaker",
                    "description": "Determines the next speaker in the conversation.",
                    "parameters": NEXT_SPEAKER_SCHEMA
                }]
            }],
            'toolConfig': {
                "functionCallingConfig": {
                    "mode": "ANY",
                    "allowedFunctionNames": ["determine_next_speaker"]
                }
            }
        }
        
        request_components = {
            "project": session.client.project_id,
            "request": request_body
        }
        
        # Make the API call
        response_json = await session.client._make_api_request(
            'generateContent', 
            request_components=request_components,
            stream=False, 
            chat_session=session
        )
        
        # Extract the JSON arguments from the tool call in the response
        part = response_json.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0]
        if 'functionCall' in part and part['functionCall'].get('name') == 'determine_next_speaker':
            args = part['functionCall'].get('args', {})
            speaker = args.get('speaker', 'user')
            logging.debug(f"Model decided next speaker is '{speaker}'. Reason: {args.get('reason', 'N/A')}")
            return speaker
        else:
            logging.warning("Could not determine next speaker from model response. Defaulting to 'user'.")
            return "user"
            
    except Exception as e:
        logging.error(f"Error checking for next speaker: {e}. Defaulting to 'user'.")
        return "user"