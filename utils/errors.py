#
# File: utils/errors.py
# Revision: 1
# Description: Custom exceptions and error handling utilities.
#

import httpx

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

def to_friendly_error(error: Exception) -> Exception:
    """
    Converts an httpx.HTTPStatusError into a more specific custom exception
    based on the HTTP status code.
    """
    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        try:
            # Try to parse the JSON body for a more specific message
            data = error.response.json()
            message = data.get("error", {}).get("message", error.response.text)
        except Exception:
            message = error.response.text

        if status_code == 400:
            return BadRequestError(f"Bad Request (400): {message}")
        if status_code == 401:
            return UnauthorizedError(f"Unauthorized (401): {message}")
        if status_code == 403:
            return ForbiddenError(f"Forbidden (403): {message}")

    # Return the original error if it's not a handled HTTPStatusError
    return error