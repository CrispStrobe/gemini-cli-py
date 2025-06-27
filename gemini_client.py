#
# File: gemini_client.py
# Revision: 25
# Description: Refactors _make_api_request to be retry-safe for model fallbacks.
# It now accepts `request_components` and builds the final payload within the
# retry loop, ensuring the correct model is used after a 429-triggered fallback.
#

import os
import json
import time
import secrets
import threading
import webbrowser
import http.server
import socketserver
import urllib.parse
import asyncio
import logging
import platform
from pathlib import Path
from typing import Union, List, Dict, Any, AsyncGenerator

import httpx
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError

from tool_registry import ToolRegistry
from config import Config
from utils.retry import retry_with_backoff, RetryOptions
from utils.errors import to_friendly_error
from turn import Turn
from prompts import get_core_system_prompt
from services.memory_discovery import load_memory
from utils.next_speaker_checker import check_next_speaker, NextSpeaker

class Models:
    """Available Gemini model variants with their identifiers."""
    DEFAULT = "gemini-2.5-pro"  # Default high-capability model
    FLASH = "gemini-2.5-flash"  # Faster, more efficient model for rate-limit fallback
    
    @classmethod
    def all(cls) -> List[str]:
        """Returns a list of all available model identifiers."""
        return [cls.DEFAULT, cls.FLASH]

class GeminiClient:
    """
    Main client for interacting with the Gemini API through Google Cloud's Code Assist endpoint.
    
    Handles OAuth authentication, user onboarding, and provides factory methods for chat sessions.
    Uses the cloudcode-pa.googleapis.com endpoint for API communication.
    """
    
    # OAuth 2.0 configuration constants
    _OAUTH_CLIENT_ID = "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
    _OAUTH_CLIENT_SECRET = "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"
    _OAUTH_SCOPES = [
        "https://www.googleapis.com/auth/cloud-platform", 
        "https://www.googleapis.com/auth/userinfo.email", 
        "https://www.googleapis.com/auth/userinfo.profile"
    ]
    
    # File and endpoint configuration
    _CREDENTIALS_FILENAME = "oauth_creds.json"
    _TOKEN_URI = "https://oauth2.googleapis.com/token"
    _AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
    _CODE_ASSIST_ENDPOINT = "https://cloudcode-pa.googleapis.com"
    _API_VERSION = "v1internal"
    _PLUGIN_VERSION = "1.0.0"

    def __init__(self, config: Config, credentials_dir: Path = None):
        """
        Initialize the Gemini client with configuration and credentials.
        
        Args:
            config: Application configuration object
            credentials_dir: Optional custom directory for storing OAuth credentials.
                             Defaults to ~/.gemini/
        """
        self.config = config
        
        # Set up credentials file path
        if credentials_dir is None:
            self.credentials_path = Path.home() / ".gemini" / self._CREDENTIALS_FILENAME
        else:
            self.credentials_path = credentials_dir / self._CREDENTIALS_FILENAME

        # Initialize OAuth credentials and HTTP client
        self.credentials = self._get_credentials()
        self.project_id = None  # Will be set during user initialization
        self.http_client = httpx.AsyncClient(timeout=300.0)  # 5-minute timeout for long operations

    async def aclose(self):
        """Properly close the HTTP client to prevent resource leaks."""
        await self.http_client.aclose()

    async def initialize_user(self):
        """
        Perform user onboarding with Google Cloud Code Assist.
        
        This sets up the user's project and tier configuration required for API access.
        Must be called before creating chat sessions.
        """
        self.project_id = await self._setup_user()
        print(f"User setup complete. Using Project ID: {self.project_id or 'N/A'}")

    def start_chat(self, config, model: str) -> 'ChatSession':
        """
        Factory method to create a new chat session.
        
        Args:
            config: Configuration object for the chat session
            model: Model identifier (use Models.DEFAULT or Models.FLASH)
            
        Returns:
            ChatSession: New chat session instance
        """
        return ChatSession(self, config, model)

    def _get_platform(self) -> str:
        """
        Detect the current platform and architecture for client metadata.
        
        Returns:
            str: Platform identifier in format "PLATFORM_ARCHITECTURE"
        """
        system = platform.system().lower()
        arch = platform.machine().lower()
        
        if system == "darwin": 
            return f"DARWIN_{arch.upper()}"
        elif system == "linux": 
            return f"LINUX_{arch.upper()}"
        elif system == "windows": 
            return f"WINDOWS_{arch.upper()}"
        return "PLATFORM_UNSPECIFIED"

    def _get_client_metadata(self) -> Dict[str, Any]:
        """
        Generate client metadata required by the Code Assist API.
        
        Returns:
            dict: Metadata dictionary containing platform, plugin info, etc.
        """
        return {
            'ideType': 'IDE_UNSPECIFIED',
            'platform': self._get_platform(),
            'pluginType': 'GEMINI',
            'pluginVersion': self._PLUGIN_VERSION,
        }

    def _get_credentials(self) -> Credentials:
        """
        Load or obtain OAuth 2.0 credentials for API authentication.
        
        Attempts to load existing credentials from file, refresh if expired,
        or initiate new OAuth flow if needed.
        
        Returns:
            Credentials: Valid OAuth 2.0 credentials
        """
        # Try to load existing credentials
        if self.credentials_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(
                    str(self.credentials_path), 
                    self._OAUTH_SCOPES
                )
                
                # Return if valid
                if creds and creds.valid: 
                    return creds
                
                # Attempt to refresh if expired but refresh token available
                if creds and creds.expired and creds.refresh_token:
                    print("Credentials expired, refreshing...")
                    creds.refresh(Request())
                    self._save_credentials(creds)
                    return creds
            except Exception as e:
                print(f"Could not load or refresh credentials, re-authenticating: {e}")
        
        # Start new OAuth flow if no valid credentials found
        return self._run_oauth_flow()

    def _save_credentials(self, creds: Credentials):
        """
        Save OAuth credentials to file for future use.
        
        Args:
            creds: Credentials object to save
        """
        # Ensure directory exists
        self.credentials_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write credentials as JSON
        with open(self.credentials_path, 'w') as f: 
            f.write(creds.to_json())
        print(f"Credentials saved to {self.credentials_path}")

    def _run_oauth_flow(self) -> Credentials:
        """
        Execute the OAuth 2.0 authorization code flow.
        
        Starts a local HTTP server to handle the OAuth callback, opens the user's
        browser for authentication, and exchanges the authorization code for tokens.
        
        Returns:
            Credentials: New OAuth 2.0 credentials
        """
        print("Gemini login required.")
        
        # Generate secure state parameter for CSRF protection
        auth_code = None
        server_thread = None
        httpd = None
        state = secrets.token_urlsafe(16)
        
        class OAuthCallbackHandler(http.server.SimpleHTTPRequestHandler):
            """HTTP handler for OAuth callback endpoint."""
            
            def do_GET(self):
                nonlocal auth_code
                parsed_path = urllib.parse.urlparse(self.path)
                query_params = urllib.parse.parse_qs(parsed_path.query)
                
                # Verify state parameter to prevent CSRF attacks
                if query_params.get('state', [None])[0] != state: 
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"State mismatch error.")
                    return
                
                # Handle successful authorization
                if 'code' in query_params: 
                    auth_code = query_params['code'][0]
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"<html><body><h1>Authentication successful!</h1><p>You can close this window.</p></body></html>")
                else: 
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Authentication failed.")
        
        def start_server():
            """Start the local OAuth callback server."""
            nonlocal httpd
            with socketserver.TCPServer(("localhost", 0), OAuthCallbackHandler) as s: 
                httpd = s
                httpd.serve_forever()
        
        # Start callback server in background thread
        server_thread = threading.Thread(target=start_server)
        server_thread.daemon = True
        server_thread.start()
        time.sleep(0.1)  # Give server time to start
        
        if not httpd: 
            raise Exception("Failed to start local server for OAuth callback.")
        
        # Build authorization URL
        port = httpd.server_address[1]
        redirect_uri = f'http://localhost:{port}/oauth2callback'
        params = {
            'response_type': 'code', 
            'client_id': self._OAUTH_CLIENT_ID, 
            'redirect_uri': redirect_uri, 
            'scope': ' '.join(self._OAUTH_SCOPES), 
            'state': state, 
            'access_type': 'offline',  # Request refresh token
            'prompt': 'consent'  # Force consent screen to ensure refresh token
        }
        auth_url = f"{self._AUTH_URI}?{urllib.parse.urlencode(params)}"
        
        # Open browser and wait for authorization
        print(f"Attempting to open authentication page in your browser.\n"
              f"If it does not open, please navigate to this URL:\n\n{auth_url}\n")
        webbrowser.open(auth_url)
        
        # Wait for authorization code
        while auth_code is None: 
            time.sleep(0.5)
        
        # Clean up server
        httpd.shutdown()
        httpd.server_close()
        
        # Exchange authorization code for tokens
        token_data = {
            'code': auth_code, 
            'client_id': self._OAUTH_CLIENT_ID, 
            'client_secret': self._OAUTH_CLIENT_SECRET, 
            'redirect_uri': redirect_uri, 
            'grant_type': 'authorization_code'
        }
        response = httpx.post(self._TOKEN_URI, data=token_data)
        response.raise_for_status()
        token_info = response.json()
        
        # Create credentials object
        creds = Credentials(
            token=token_info['access_token'], 
            refresh_token=token_info.get('refresh_token'), 
            token_uri=self._TOKEN_URI, 
            client_id=self._OAUTH_CLIENT_ID, 
            client_secret=self._OAUTH_CLIENT_SECRET, 
            scopes=token_info['scope'].split()
        )
        
        print("Authentication successful.")
        self._save_credentials(creds)
        return creds

    async def _setup_user(self) -> str:
        """
        Perform user onboarding with Google Cloud Code Assist.
        
        This involves loading the Code Assist configuration and onboarding the user
        to a specific tier and project.
        
        Returns:
            str: Project ID for the onboarded user
        """
        print("Performing user onboarding...")
        
        # Check for existing project ID in environment
        initial_project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
        client_metadata = self._get_client_metadata()
        
        if initial_project_id: 
            client_metadata['duetProject'] = initial_project_id
        
        # Load Code Assist configuration
        load_assist_req = {'metadata': client_metadata}
        if initial_project_id: 
            load_assist_req['cloudaicompanionProject'] = initial_project_id
        
        load_res = await self._make_api_request('loadCodeAssist', body=load_assist_req)
        
        # Determine the tier to use (prefer default tier)
        default_tier = next(
            (t for t in load_res.get('allowedTiers', []) if t.get('isDefault')), 
            None
        )
        onboard_tier_id = default_tier['id'] if default_tier else 'legacy-tier'
        onboard_project_id = load_res.get('cloudaicompanionProject') or initial_project_id
        
        # Start onboarding process
        onboard_req = {
            'tierId': onboard_tier_id, 
            'metadata': client_metadata
        }
        if onboard_project_id: 
            onboard_req['cloudaicompanionProject'] = onboard_project_id
        
        lro_res = await self._make_api_request('onboardUser', body=onboard_req)
        
        # Handle Long Running Operation (LRO) response
        operation_name = lro_res.get('name')
        if not operation_name:
            # Operation completed immediately
            if lro_res.get('done') and 'response' in lro_res:
                return lro_res.get('response', {}).get('cloudaicompanionProject', {}).get('id', '')
            raise Exception(f"Failed to start onboarding: {lro_res}")
        
        # Poll for operation completion
        while not lro_res.get('done', False):
            print("Onboarding in progress, waiting 5 seconds...")
            await asyncio.sleep(5)
            lro_res = await self._make_api_request(operation_name, http_method='GET')
        
        # Extract project ID from completed operation
        project_id = lro_res.get('response', {}).get('cloudaicompanionProject', {}).get('id', '')
        if not project_id: 
            print("Warning: Onboarding complete but no project ID returned.")
        
        return project_id

    async def _make_api_request(
        self, 
        endpoint: str, 
        body: Dict[str, Any] = None,
        stream: bool = False, 
        http_method: str = 'POST',
        chat_session: 'ChatSession' = None,
        request_components: Dict[str, Any] = None
    ) -> Union[Dict[str, Any], httpx.Response]:
        """
        Make an authenticated API request to the Code Assist endpoint.
        
        Args:
            endpoint: API endpoint to call
            body: Request body for POST requests (legacy, for non-chat calls)
            stream: Whether to return a streaming response
            http_method: HTTP method to use ('POST' or 'GET')
            chat_session: Optional chat session for error handling callbacks
            request_components: Raw request parts for retry-safe payload building
            
        Returns:
            Union[Dict, httpx.Response]: JSON response dict or raw response for streaming
        """

        async def api_call():
            """Inner function that performs the actual API call."""
            # Ensure credentials are valid
            if not self.credentials.valid: 
                self.credentials.refresh(Request())
            
            # Build client metadata header
            client_metadata_str = ",".join([
                f"{k}={v}" for k, v in self._get_client_metadata().items()
            ])
            
            # Set up request headers
            headers = {
                'Authorization': f'Bearer {self.credentials.token}',
                'Content-Type': 'application/json',
                'User-Agent': f'GeminiCLI-Python-Client/{self._PLUGIN_VERSION}',
                'Client-Metadata': client_metadata_str,
            }
            
            # Build URL based on endpoint type
            if endpoint.startswith('operations/'): 
                url = f"{self._CODE_ASSIST_ENDPOINT}/{endpoint}"
            else: 
                url = f"{self._CODE_ASSIST_ENDPOINT}/{self._API_VERSION}:{endpoint}"
            
            # Set up streaming parameters if needed
            params = {'alt': 'sse'} if stream else {}
            
            final_body = None
            if request_components and chat_session:
                # New, preferred way for calls that can be retried with model fallback.
                # The payload is built here to ensure it uses the *current* model.
                final_body = request_components.copy()
                final_body["model"] = chat_session.model
            elif body:
                # Old way, for calls that don't need model fallback (e.g., onboarding).
                final_body = body

            logging.debug(f"Making API call: {http_method.upper()} {url} with model {final_body.get('model', 'N/A')}")
            
            # Build and send request
            if http_method.upper() == 'POST':
                request = self.http_client.build_request(
                    "POST", url, headers=headers, json=final_body, params=params
                )
            elif http_method.upper() == 'GET':
                request = self.http_client.build_request(
                    "GET", url, headers=headers, params=params
                )
            else:
                raise ValueError(f"Unsupported HTTP method: {http_method}")
            
            response = await self.http_client.send(request, stream=stream)
            response.raise_for_status()
            return response

        # Set up retry options with optional fallback handler
        retry_options = RetryOptions(
            on_persistent_429=chat_session._handle_flash_fallback if chat_session else None
        )

        try:
            response = await retry_with_backoff(api_call, options=retry_options)
            
            # --- CORRECTED LOGIC ---
            if stream:
                return response
            else:
                # For non-streaming, parse the JSON and return the dictionary.
                # No 'await' is needed for the synchronous .json() method.
                return response.json()
                
        except httpx.HTTPStatusError as e:
            # Convert HTTP errors to friendly user messages
            raise await to_friendly_error(e) from e
        except Exception as e:
            # Re-raise other exceptions unchanged
            raise e


class ChatSession:
    """
    Manages a single conversation with the Gemini model.
    
    Handles chat history, tool integration, system prompts, and turn management.
    The system prompt is integrated directly into the chat history as the first
    user message, followed by a model acknowledgment.
    """
    
    def __init__(self, client: GeminiClient, config: Config, model: str):
        """
        Initialize a new chat session.
        
        Args:
            client: GeminiClient instance for API communication
            config: Configuration object
            model: Model identifier to use for this session
        """
        self.client = client
        self.config = config
        self.model = model
        self.history: List[Dict[str, Any]] = []  # Chat message history
        self.tool_registry = ToolRegistry(self.config)  # Available tools
        self.current_turn: Turn | None = None  # Currently executing turn
        
        # Initialize chat context with system prompt in history
        self._initialize_chat_context()

    def _initialize_chat_context(self):
        """
        Initialize the chat session with system prompt and context.
        """
        logging.debug("Initializing chat context...")
        
        # Get target directory and build system prompt
        target_dir = self.config.get_target_dir()
        core_prompt = get_core_system_prompt(target_dir)
        memory_content = load_memory(target_dir)
        
        # Combine core prompt with any user-provided context
        full_prompt_text = core_prompt
        if memory_content:
            full_prompt_text += "\n\n# User-Provided Context\n"
            full_prompt_text += "You MUST use the following context to augment your knowledge and follow any directives given.\n"
            full_prompt_text += memory_content

        # We add the system prompt as the first
        # user message in the history, followed by a model acknowledgment.
        # This ensures proper turn order and compatibility with the API.
        self.history = [
            {
                "role": "user", 
                "parts": [{"text": full_prompt_text}]
            },
            {
                "role": "model", 
                "parts": [{"text": "Understood. I will follow these instructions and use my tools to assist you."}]
            }
        ]
        
        logging.debug("Chat context initialized successfully.")

    async def send_message(self, prompt: str) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Send a message to the model and stream the response.
        
        Args:
            prompt: User message to send
            
        Yields:
            Dict: Stream events from the model response
        """
        # Create and execute a new turn
        self.current_turn = Turn(session=self, prompt=prompt)
        async for event in self.current_turn.run():
            yield event

    async def check_next_speaker(self) -> NextSpeaker:
        """
        Determine who should speak next in the conversation.
        
        Returns:
            NextSpeaker: Either "user" or "model"
        """
        if not self.history:
            return "user"
        
        # Check if the last message was a tool response, which means model must respond
        last_message = self.history[-1]
        if (last_message.get("role") == "user" and 
            any("functionResponse" in part for part in last_message.get("parts", []))):
            logging.debug("Last message was a tool response, model must be the next speaker.")
            return "model"
        
        # Use external logic for other cases
        return await check_next_speaker(self)

    def reset(self):
        """
        Reset the chat session to its initial state.
        
        Clears the conversation history and reinitializes with system prompt.
        """
        logging.info("Resetting chat session history.")
        self._initialize_chat_context()

    async def _handle_flash_fallback(self) -> bool:
        """
        Handle persistent rate limiting by switching to the Flash model.
        
        This is called automatically by the retry mechanism when encountering
        persistent 429 (rate limit) errors.
        
        Returns:
            bool: True if fallback was performed, False if already using Flash
        """
        # If already using Flash model, can't fallback further
        if self.model == Models.FLASH:
            return False

        # Switch to Flash model and inform user
        print(f"\n[INFO] ⚡ Persistent rate-limiting detected. "
              f"Temporarily switching from {self.model} to {Models.FLASH} "
              f"to complete the request.")
        
        logging.warning(f"Switching model from {self.model} to {Models.FLASH} due to persistent 429 errors.")
        self.model = Models.FLASH
        logging.info(f"Model switched to {self.model}.")
        
        return True