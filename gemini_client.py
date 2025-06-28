#
# File: gemini_client.py
# Revision: 30
# Description: Corrects the construction of the API request
# payload inside _make_api_request. The previous version incorrectly
# extracted the nested 'request' object, breaking the JSON structure expected
# by the API. This version now sends the complete, correctly-structured body.
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
from typing import Union, List, Dict, Any, TYPE_CHECKING

import httpx
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError

from config import Config
from utils.retry import retry_with_backoff, RetryOptions
from utils.errors import to_friendly_error

if TYPE_CHECKING:
    from chat_session import ChatSession # Use TYPE_CHECKING to avoid circular import

class Models:
    """Available Gemini model variants with their identifiers."""
    DEFAULT = "gemini-2.5-pro"
    FLASH = "gemini-2.5-flash"

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

    def start_chat(self, config: Config, model: str) -> 'ChatSession':
        """
        Factory method to create a new chat session.

        Args:
            config: Configuration object for the chat session
            model: Model identifier (use Models.DEFAULT or Models.FLASH)

        Returns:
            ChatSession: New chat session instance
        """
        # This now imports dynamically to avoid circular dependency issues at module load time
        from chat_session import ChatSession
        return ChatSession(self, config, model)

    def _get_platform(self) -> str:
        """
        Detect the current platform and architecture for client metadata.

        Returns:
            str: Platform identifier in format "PLATFORM_ARCHITECTURE"
        """
        system = platform.system().lower()
        arch = platform.machine().lower()

        if system == "darwin": return f"DARWIN_{arch.upper()}"
        if system == "linux": return f"LINUX_{arch.upper()}"
        if system == "windows": return f"WINDOWS_{arch.upper()}"
        return "PLATFORM_UNSPECIFIED"

    def _get_client_metadata(self) -> Dict[str, Any]:
        """
        Generate client metadata required by the Code Assist API.

        Returns:
            dict: Metadata dictionary containing platform, plugin info, etc.
        """
        return {
            'ideType': 'IDE_UNSPECIFIED', 'platform': self._get_platform(),
            'pluginType': 'GEMINI', 'pluginVersion': self._PLUGIN_VERSION,
        }

    def _get_credentials(self) -> Credentials:
        """
        Load or obtain OAuth 2.0 credentials for API authentication.
        """
        if self.credentials_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(self.credentials_path), self._OAUTH_SCOPES)
                if creds and creds.valid: return creds
                if creds and creds.expired and creds.refresh_token:
                    print("Credentials expired, refreshing...")
                    creds.refresh(Request())
                    self._save_credentials(creds)
                    return creds
            except Exception as e:
                print(f"Could not load or refresh credentials, re-authenticating: {e}")
        return self._run_oauth_flow()

    def _save_credentials(self, creds: Credentials):
        """
        Save OAuth credentials to file for future use.
        """
        self.credentials_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.credentials_path, 'w') as f:
            f.write(creds.to_json())
        print(f"Credentials saved to {self.credentials_path}")

    def _run_oauth_flow(self) -> Credentials:
        """
        Execute the OAuth 2.0 authorization code flow.
        """
        print("Gemini login required.")
        auth_code, server_thread, httpd, state = None, None, None, secrets.token_urlsafe(16)
        class OAuthCallbackHandler(http.server.SimpleHTTPRequestHandler):
            def do_GET(self):
                nonlocal auth_code
                parsed_path = urllib.parse.urlparse(self.path)
                query_params = urllib.parse.parse_qs(parsed_path.query)
                if query_params.get('state', [None])[0] != state:
                    self.send_response(400); self.end_headers(); self.wfile.write(b"State mismatch error.")
                    return
                if 'code' in query_params:
                    auth_code = query_params['code'][0]
                    self.send_response(200); self.end_headers(); self.wfile.write(b"<html><body><h1>Authentication successful!</h1><p>You can close this window.</p></body></html>")
                else:
                    self.send_response(400); self.end_headers(); self.wfile.write(b"Authentication failed.")
        def start_server():
            nonlocal httpd
            with socketserver.TCPServer(("localhost", 0), OAuthCallbackHandler) as s:
                httpd = s
                httpd.serve_forever()
        server_thread = threading.Thread(target=start_server)
        server_thread.daemon = True
        server_thread.start()
        time.sleep(0.1)
        if not httpd: raise Exception("Failed to start local server for OAuth callback.")
        port = httpd.server_address[1]
        redirect_uri = f'http://localhost:{port}/oauth2callback'
        params = {'response_type': 'code', 'client_id': self._OAUTH_CLIENT_ID, 'redirect_uri': redirect_uri, 'scope': ' '.join(self._OAUTH_SCOPES), 'state': state, 'access_type': 'offline', 'prompt': 'consent'}
        auth_url = f"{self._AUTH_URI}?{urllib.parse.urlencode(params)}"
        print(f"Attempting to open authentication page in your browser.\nIf it does not open, please navigate to this URL:\n\n{auth_url}\n")
        webbrowser.open(auth_url)
        while auth_code is None: time.sleep(0.5)
        httpd.shutdown(); httpd.server_close()
        token_data = {'code': auth_code, 'client_id': self._OAUTH_CLIENT_ID, 'client_secret': self._OAUTH_CLIENT_SECRET, 'redirect_uri': redirect_uri, 'grant_type': 'authorization_code'}
        response = httpx.post(self._TOKEN_URI, data=token_data)
        response.raise_for_status()
        token_info = response.json()
        creds = Credentials(token=token_info['access_token'], refresh_token=token_info.get('refresh_token'), token_uri=self._TOKEN_URI, client_id=self._OAUTH_CLIENT_ID, client_secret=self._OAUTH_CLIENT_SECRET, scopes=token_info['scope'].split())
        print("Authentication successful.")
        self._save_credentials(creds)
        return creds

    async def _setup_user(self) -> str:
        """
        Perform user onboarding with Google Cloud Code Assist.
        """
        print("Performing user onboarding...")
        initial_project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
        client_metadata = self._get_client_metadata()
        if initial_project_id: client_metadata['duetProject'] = initial_project_id
        load_assist_req = {'metadata': client_metadata}
        if initial_project_id: load_assist_req['cloudaicompanionProject'] = initial_project_id
        load_res = await self._make_api_request('loadCodeAssist', body=load_assist_req)
        default_tier = next((t for t in load_res.get('allowedTiers', []) if t.get('isDefault')), None)
        onboard_tier_id = default_tier['id'] if default_tier else 'legacy-tier'
        onboard_project_id = load_res.get('cloudaicompanionProject') or initial_project_id
        onboard_req = {'tierId': onboard_tier_id, 'metadata': client_metadata}
        if onboard_project_id: onboard_req['cloudaicompanionProject'] = onboard_project_id
        lro_res = await self._make_api_request('onboardUser', body=onboard_req)
        operation_name = lro_res.get('name')
        if not operation_name:
            if lro_res.get('done') and 'response' in lro_res:
                return lro_res.get('response', {}).get('cloudaicompanionProject', {}).get('id', '')
            raise Exception(f"Failed to start onboarding: {lro_res}")
        while not lro_res.get('done', False):
            print("Onboarding in progress, waiting 5 seconds...")
            await asyncio.sleep(5)
            lro_res = await self._make_api_request(operation_name, http_method='GET')
        project_id = lro_res.get('response', {}).get('cloudaicompanionProject', {}).get('id', '')
        if not project_id: print("Warning: Onboarding complete but no project ID returned.")
        return project_id

    async def _make_api_request(self, endpoint: str, body: Dict[str, Any] = None, stream: bool = False, http_method: str = 'POST', chat_session: 'ChatSession' = None, request_components: Dict[str, Any] = None) -> Union[Dict[str, Any], httpx.Response]:
        """
        Make an authenticated API request to the Code Assist endpoint.
        """
        async def api_call():
            """Inner function that performs the actual API call."""
            if not self.credentials.valid: self.credentials.refresh(Request())
            client_metadata_str = ",".join([f"{k}={v}" for k, v in self._get_client_metadata().items()])
            headers = {'Authorization': f'Bearer {self.credentials.token}', 'Content-Type': 'application/json', 'User-Agent': f'GeminiCLI-Python-Client/{self._PLUGIN_VERSION}', 'Client-Metadata': client_metadata_str}
            url = f"{self._CODE_ASSIST_ENDPOINT}/{endpoint}" if endpoint.startswith('operations/') else f"{self._CODE_ASSIST_ENDPOINT}/{self._API_VERSION}:{endpoint}"
            params = {'alt': 'sse'} if stream else {}
            
            final_body = None
            if request_components and chat_session:
                # FIX: Use the entire request_components dictionary as the base,
                # then add the current model to it. This preserves the required
                # {"project": ..., "request": {...}} structure.
                final_body = request_components.copy()
                final_body["model"] = chat_session.model
            elif body:
                final_body = body

            logging.debug(f"Making API call: {http_method.upper()} {url} with payload: {json.dumps(final_body, indent=2)}")
            
            if http_method.upper() == 'POST':
                request = self.http_client.build_request("POST", url, headers=headers, json=final_body, params=params)
            elif http_method.upper() == 'GET':
                request = self.http_client.build_request("GET", url, headers=headers, params=params)
            else:
                raise ValueError(f"Unsupported HTTP method: {http_method}")
            
            response = await self.http_client.send(request, stream=stream)
            response.raise_for_status()
            return response

        retry_options = RetryOptions(on_persistent_429=chat_session._handle_flash_fallback if chat_session else None)
        try:
            response = await retry_with_backoff(api_call, options=retry_options)
            return response if stream else response.json()
        except httpx.HTTPStatusError as e:
            raise await to_friendly_error(e) from e
        except Exception as e:
            raise e