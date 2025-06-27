#
# File: utils/errors.py
# Revision: 2
# Description: Converts the error handler to be async to correctly read
# the body of failed streaming HTTP responses.
#

import httpx
import json

class ForbiddenError(Exception):
    """Raised for 403 Forbidden errors."""
    pass

class UnauthorizedError(Exception):
    """Raised for 401 Unauthorized errors."""
    pass

class BadRequestError(Exception):
    """Raised for 400 Bad Request errors."""
    pass

def get_error_message(error: Exception) -> str:
    """Extracts a user-friendly error message from an exception."""
    try:
        return str(error)
    except Exception:
        return "Failed to get error details."

async def to_friendly_error(error: Exception) -> Exception:
    """
    Converts an httpx.HTTPStatusError into a more specific custom exception
    based on the HTTP status code. Now handles streaming responses.
    """
    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        message = ""
        try:
            # For streaming responses, we must read the body before accessing it.
            await error.response.aread()
            data = error.response.json()
            message = data.get("error", {}).get("message", error.response.text)
        except (httpx.ResponseNotRead, json.JSONDecodeError):
            # Fallback to plain text if it's not JSON or already read
            message = error.response.text
        except Exception as e:
            message = f"Failed to parse error response body: {e}"

        if status_code == 400:
            return BadRequestError(f"Bad Request (400): {message}")
        if status_code == 401:
            return UnauthorizedError(f"Unauthorized (401): {message}")
        if status_code == 403:
            return ForbiddenError(f"Forbidden (403): {message}")

    return error