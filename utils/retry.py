#
# File: utils/retry.py
# Revision: 2
# Description: Adds a callback mechanism to handle persistent 429 errors,
# enabling features like model fallback.
#

import asyncio
import logging
import random
import datetime # Added import for datetime
from typing import Callable, Awaitable, TypeVar

import httpx

T = TypeVar('T')

class RetryOptions:
    def __init__(
        self,
        max_attempts: int = 5,
        initial_delay_s: float = 1.0,
        max_delay_s: float = 30.0,
        on_persistent_429: Callable[[], Awaitable[bool]] | None = None
    ):
        self.max_attempts = max_attempts
        self.initial_delay_s = initial_delay_s
        self.max_delay_s = max_delay_s
        self.on_persistent_429 = on_persistent_429

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
    """
    attempt = 0
    consecutive_429_count = 0
    current_delay_s = options.initial_delay_s
    last_exception = None

    while attempt < options.max_attempts:
        attempt += 1
        try:
            result = await fn()
            return result
        except Exception as e:
            last_exception = e

            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429:
                consecutive_429_count += 1
                logging.debug(f"429 error. Consecutive 429 count: {consecutive_429_count}")
            else:
                consecutive_429_count = 0
                logging.debug("Non-429 error. Resetting consecutive 429 count.")

            # After 2 consecutive 429s, trigger the fallback if it exists
            logging.debug(f"Checking on_persistent_429 condition: consecutive_429_count={consecutive_429_count}, on_persistent_429={options.on_persistent_429 is not None}")
            if consecutive_429_count >= 2 and options.on_persistent_429:
                logging.warning("Persistent 429 errors detected. Triggering fallback.")
                try:
                    if await options.on_persistent_429():
                        logging.info("Fallback handler executed successfully. Resetting retry counters.")
                        attempt = 0
                        consecutive_429_count = 0
                        current_delay_s = options.initial_delay_s # Reset delay as well
                        continue
                except Exception as fallback_e:
                    logging.error(f"Error during fallback execution: {fallback_e}", exc_info=True)
                    # If fallback fails, continue with original error handling (i.e., break or re-raise)

            if not should_retry(e) or attempt >= options.max_attempts:
                logging.debug(f"Not retrying. Should retry: {should_retry(e)}, Attempt: {attempt}, Max attempts: {options.max_attempts}")
                break

            delay_s = current_delay_s
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429:
                retry_after = e.response.headers.get("Retry-After")
                logging.debug(f"Retry-After header: {retry_after}")
                if retry_after:
                    try:
                        # Try to parse as seconds
                        delay_s = int(retry_after)
                        logging.debug(f"Parsed Retry-After as seconds: {delay_s}")
                    except ValueError:
                        # Try to parse as HTTP-date
                        try:
                            from email.utils import parsedate_to_datetime
                            dt = parsedate_to_datetime(retry_after)
                            delay_s = (dt - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
                            logging.debug(f"Parsed Retry-After as HTTP-date: {delay_s}")
                        except Exception:
                            logging.warning(f"Could not parse Retry-After header: {retry_after}. Using exponential backoff.")
                            pass # Fallback to exponential backoff
                else:
                    logging.debug("No Retry-After header found. Using exponential backoff.")

            jitter_s = delay_s * 0.5 * (random.random() * 2 - 1)
            delay_with_jitter_s = max(0, delay_s + jitter_s)
            
            logging.warning(f"Attempt {attempt} failed. Retrying in {delay_with_jitter_s:.2f}s...")
            await asyncio.sleep(delay_with_jitter_s)
            current_delay_s = min(options.max_delay_s, current_delay_s * 2)

    logging.error(f"All {options.max_attempts} retry attempts failed.")
    raise last_exception