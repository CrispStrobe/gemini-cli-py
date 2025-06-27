#
# File: utils/next_speaker_checker.py
# Revision: 2
# Description: Fixed the issue where empty parts were being sent to the API.
# Added proper validation to ensure all API requests have valid content.
#

import logging
from typing import Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from gemini_client import ChatSession

NextSpeaker = Literal["user", "model"]

async def check_next_speaker(session: 'ChatSession') -> NextSpeaker:
    """
    Determines who should speak next in the conversation by asking the model.
    
    Args:
        session: The current chat session
        
    Returns:
        NextSpeaker: Either "user" or "model"
    """
    logging.info("Checking for next speaker...")
    
    try:
        # Validate that we have a valid conversation history
        if not session.history:
            logging.info("No conversation history, defaulting to 'user'.")
            return "user"
        
        # Check if the last message has valid parts
        last_message = session.history[-1]
        if not last_message.get("parts") or len(last_message["parts"]) == 0:
            logging.info("Last message has no parts, defaulting to 'user'.")
            return "user"
        
        # Create a system prompt to ask the model who should speak next
        next_speaker_prompt = """
Analyze the conversation and determine who should speak next: 'user' or 'model'.

Rules:
- If the model just completed a task and is waiting for the user's next request, answer 'user'
- If the model is in the middle of a multi-step task that requires continuation, answer 'model'
- If the model made tool calls but hasn't provided a final response about the results, answer 'model'
- If the conversation is at a natural stopping point, answer 'user'

Respond with only one word: either 'user' or 'model', followed by a brief reason.
Format: [user/model]: [reason]
"""
        
        # Build the request with the conversation history plus the next speaker prompt
        request_contents = session.history + [
            {"role": "user", "parts": [{"text": next_speaker_prompt}]}
        ]
        
        # Validate that all messages have valid parts
        for i, message in enumerate(request_contents):
            if not message.get("parts") or len(message["parts"]) == 0:
                logging.warning(f"Message at index {i} has no parts, removing it")
                # Skip messages without parts
                continue
            
            # Validate each part has content
            valid_parts = []
            for part in message["parts"]:
                if part.get("text") or part.get("functionCall") or part.get("functionResponse"):
                    valid_parts.append(part)
            
            if not valid_parts:
                logging.warning(f"Message at index {i} has no valid parts")
                continue
            
            message["parts"] = valid_parts
        
        # Filter out any messages that ended up with no valid parts
        request_contents = [msg for msg in request_contents if msg.get("parts") and len(msg["parts"]) > 0]
        
        if not request_contents:
            logging.error("No valid messages found for next speaker check, defaulting to 'user'")
            return "user"
        
        # Build the API request
        request_body = {'contents': request_contents}
        final_payload = {
            "model": session.model,
            "project": session.client.project_id,
            "request": request_body
        }
        
        # Make the API call
        response = await session.client._make_api_request(
            'generateContent', 
            body=final_payload, 
            stream=False
        )
        
        # Parse the response
        if not response or 'candidates' not in response:
            logging.error("Invalid response from next speaker check, defaulting to 'user'")
            return "user"
        
        candidate = response['candidates'][0]
        if 'content' not in candidate or 'parts' not in candidate['content']:
            logging.error("No content in next speaker response, defaulting to 'user'")
            return "user"
        
        response_text = candidate['content']['parts'][0].get('text', '').strip().lower()
        
        # Parse the response
        if response_text.startswith('model'):
            # Extract reason if present
            parts = response_text.split(':', 1)
            reason = parts[1].strip() if len(parts) > 1 else "Model should continue"
            logging.info(f"Model decided next speaker is 'model'. Reason: {reason}")
            return "model"
        else:
            # Default to user for any other response
            parts = response_text.split(':', 1)
            reason = parts[1].strip() if len(parts) > 1 else "Conversation complete, waiting for user"
            logging.info(f"Model decided next speaker is 'user'. Reason: {reason}")
            return "user"
            
    except Exception as e:
        logging.error(f"Error checking for next speaker: {e}. Defaulting to 'user'.")
        return "user"