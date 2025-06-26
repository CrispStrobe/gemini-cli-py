#
# File: api_client.py
# Revision: 2 (fixed circular import)
# Description: Implements the low-level CodeAssistServer for direct API communication,
# including retry logic and handling of streaming (SSE) and non-streaming endpoints.
#

import asyncio
import httpx
import json
import logging
import os
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# ---
# Constants (from core/code_assist/server.ts)
# ---
CODE_ASSIST_ENDPOINT = os.environ.get('CODE_ASSIST_ENDPOINT', 'https://cloudcode-pa.googleapis.com')
CODE_ASSIST_API_VERSION = 'v1internal'

class CodeAssistServer:
    """
    Handles making authenticated requests to the Code Assist API.
    This is a direct Python replica of the CodeAssistServer class from server.ts.
    """
    def __init__(self, credentials: Credentials, project_id: str | None, http_options: dict | None = None):
        self.credentials = credentials
        self.project_id = project_id
        self.http_options = http_options or {}
        # Use a longer timeout to handle potentially slow model responses
        self.session = httpx.AsyncClient(timeout=120.0)

    async def _get_auth_headers(self) -> dict:
        """Refreshes token if needed and returns auth headers."""
        # This import is moved here to break the circular dependency.
        from auth import cache_credentials
        
        if self.credentials.expired and self.credentials.refresh_token:
            logging.info("Credentials expired, refreshing token...")
            self.credentials.refresh(Request())
            cache_credentials(self.credentials)
        return {'Authorization': f'Bearer {self.credentials.token}'}

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type(httpx.HTTPStatusError),
        reraise=True
    )
    async def _make_api_request(self, method: str, request_body: dict, stream: bool = False):
        """A robust, retry-enabled internal method for making API calls."""
        # Correct URL format for direct REST calls from Python
        url = f"{CODE_ASSIST_ENDPOINT}/{CODE_ASSIST_API_VERSION}/{method}"
        headers = {'Content-Type': 'application/json', **(await self._get_auth_headers())}
        params = {'alt': 'sse'} if stream else None

        logging.debug(f"Making API call to: {url}")
        logging.debug(f"Request Body: {json.dumps(request_body, indent=2)}")

        if stream:
            # This block needs to be an async generator itself
            async def stream_generator():
                async with self.session.stream("POST", url, json=request_body, headers=headers, params=params) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_text():
                        for line in chunk.splitlines():
                            if line.startswith('data: '):
                                try:
                                    yield json.loads(line[6:])
                                except json.JSONDecodeError:
                                    logging.warning(f"Could not decode JSON from stream line: {line}")
            return stream_generator()
        else:
            response = await self.session.post(url, json=request_body, headers=headers, params=params)
            response.raise_for_status()
            return response.json()

    def stream_endpoint(self, method: str, request_body: dict):
        """Makes a streaming API call."""
        return self._make_api_request(method, request_body, stream=True)

    def call_endpoint(self, method: str, request_body: dict):
        """Makes a standard (non-streaming) API call."""
        return self._make_api_request(method, request_body, stream=False)

    async def close(self):
        """Closes the underlying HTTP session."""
        if not self.session.is_closed:
            await self.session.aclose()