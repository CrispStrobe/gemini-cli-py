#
# File: utils/retry.py
# Revision: 1
# Description: A generic retry utility with exponential backoff and jitter.
#

import asyncio
import logging
import random
from typing import Callable, Awaitable, TypeVar

import httpx

T = TypeVar('T')

class RetryOptions:
    def __init__(self, max_attempts: int = 5, initial_delay_s: float = 1.0, max_delay_s: float = 30.0):
        self.max_attempts = max_attempts
        self.initial_delay_s = initial_delay_s
        self.max_delay_s = max_delay_s

def should_retry(error: Exception) -> bool:
    """Determines if a retry should be attempted based on the error."""
    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        # Retry on rate limiting (429) and server errors (5xx)
        return status_code == 429 or 500 <= status_code < 600
    return False

async def retry_with_backoff(
    fn: Callable[[], Awaitable[T]],
    options: RetryOptions = RetryOptions()
) -> T:
    """
    Retries an asynchronous function with exponential backoff and jitter.

    Args:
        fn: The asynchronous function to retry.
        options: Configuration for the retry behavior.

    Returns:
        The result of the function if successful.

    Raises:
        The last error encountered if all attempts fail.
    """
    attempt = 0
    current_delay_s = options.initial_delay_s
    last_exception = None

    while attempt < options.max_attempts:
        attempt += 1
        try:
            return await fn()
        except Exception as e:
            last_exception = e
            if not should_retry(e):
                logging.debug(f"Error is not retryable. Raising immediately: {e}")
                raise e

            if attempt >= options.max_attempts:
                break

            # Add jitter: +/- 50% of currentDelay
            jitter_s = current_delay_s * 0.5 * (random.random() * 2 - 1)
            delay_with_jitter_s = max(0, current_delay_s + jitter_s)

            logging.warning(
                f"Attempt {attempt} failed with a retryable error: {e}. "
                f"Retrying in {delay_with_jitter_s:.2f}s..."
            )
            await asyncio.sleep(delay_with_jitter_s)
            current_delay_s = min(options.max_delay_s, current_delay_s * 2)

    logging.error(f"All {options.max_attempts} retry attempts failed.")
    raise last_exception