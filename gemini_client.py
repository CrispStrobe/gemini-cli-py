#
# File: gemini_client.py
# Revision: 17
# Description: The definitive client for authentication, API communication, and tool-enabled chat sessions.
#

import os
import json
import webbrowser
import http.server
import socketserver
import threading
import secrets
import time
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
# No longer importing ToolScheduler here
from config import Config
from utils.retry import retry_with_backoff
from utils.errors import to_friendly_error
from turn import Turn

class Models:
    DEFAULT = "gemini-2.5-pro"
    FLASH = "gemini-2.5-flash"

    @classmethod
    def all(cls) -> List[str]:
        return [cls.DEFAULT, cls.FLASH]

class GeminiClient:
    # ... (All methods in GeminiClient remain unchanged)
    _OAUTH_CLIENT_ID = "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
    _OAUTH_CLIENT_SECRET = "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"
    _OAUTH_SCOPES = ["https://www.googleapis.com/auth/cloud-platform", "https://www.googleapis.com/auth/userinfo.email", "https://www.googleapis.com/auth/userinfo.profile"]
    _CREDENTIALS_FILENAME = "oauth_creds.json"
    _TOKEN_URI = "https://oauth2.googleapis.com/token"
    _AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
    _CODE_ASSIST_ENDPOINT = "https://cloudcode-pa.googleapis.com"
    _API_VERSION = "v1internal"
    _PLUGIN_VERSION = "1.0.0"

    def __init__(self, config: Config, credentials_dir: Path = None):
        self.config = config
        if credentials_dir is None:
            self.credentials_path = Path.home() / ".gemini" / self._CREDENTIALS_FILENAME
        else:
            self.credentials_path = credentials_dir / self._CREDENTIALS_FILENAME

        self.credentials = self._get_credentials()
        self.project_id = None
        self.http_client = httpx.AsyncClient(timeout=300.0)

    async def aclose(self):
        await self.http_client.aclose()

    async def initialize_user(self):
        self.project_id = await self._setup_user()
        print(f"User setup complete. Using Project ID: {self.project_id or 'N/A'}")

    def start_chat(self, config, model: str) -> 'ChatSession':
        return ChatSession(self, config, model)

    def _get_platform(self) -> str:
        system = platform.system().lower(); arch = platform.machine().lower()
        if system == "darwin": return f"DARWIN_{arch.upper()}"
        elif system == "linux": return f"LINUX_{arch.upper()}"
        elif system == "windows": return f"WINDOWS_{arch.upper()}"
        return "PLATFORM_UNSPECIFIED"

    def _get_client_metadata(self) -> Dict[str, Any]:
        return {'ideType': 'IDE_UNSPECIFIED','platform': self._get_platform(),'pluginType': 'GEMINI','pluginVersion': self._PLUGIN_VERSION,}

    def _get_credentials(self) -> Credentials:
        if self.credentials_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(self.credentials_path), self._OAUTH_SCOPES)
                if creds and creds.valid: return creds
                if creds and creds.expired and creds.refresh_token:
                    print("Credentials expired, refreshing..."); creds.refresh(Request()); self._save_credentials(creds); return creds
            except Exception as e:
                print(f"Could not load or refresh credentials, re-authenticating: {e}")
        return self._run_oauth_flow()

    def _save_credentials(self, creds: Credentials):
        self.credentials_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.credentials_path, 'w') as f: f.write(creds.to_json())
        print(f"Credentials saved to {self.credentials_path}")

    def _run_oauth_flow(self) -> Credentials:
        print("Gemini login required.")
        auth_code = None; server_thread = None; httpd = None; state = secrets.token_urlsafe(16)
        class OAuthCallbackHandler(http.server.SimpleHTTPRequestHandler):
            def do_GET(self):
                nonlocal auth_code; parsed_path = urllib.parse.urlparse(self.path); query_params = urllib.parse.parse_qs(parsed_path.query)
                if query_params.get('state', [None])[0] != state: self.send_response(400); self.end_headers(); self.wfile.write(b"State mismatch error."); return
                if 'code' in query_params: auth_code = query_params['code'][0]; self.send_response(200); self.end_headers(); self.wfile.write(b"<html><body><h1>Authentication successful!</h1><p>You can close this window.</p></body></html>")
                else: self.send_response(400); self.end_headers(); self.wfile.write(b"Authentication failed.")
        def start_server():
            nonlocal httpd
            with socketserver.TCPServer(("localhost", 0), OAuthCallbackHandler) as s: httpd = s; httpd.serve_forever()
        server_thread = threading.Thread(target=start_server); server_thread.daemon = True; server_thread.start()
        time.sleep(0.1)
        if not httpd: raise Exception("Failed to start local server for OAuth callback.")
        port = httpd.server_address[1]; redirect_uri = f'http://localhost:{port}/oauth2callback'
        params = {'response_type': 'code', 'client_id': self._OAUTH_CLIENT_ID, 'redirect_uri': redirect_uri, 'scope': ' '.join(self._OAUTH_SCOPES), 'state': state, 'access_type': 'offline', 'prompt': 'consent'}
        auth_url = f"{self._AUTH_URI}?{urllib.parse.urlencode(params)}"
        print(f"Attempting to open authentication page in your browser.\nIf it does not open, please navigate to this URL:\n\n{auth_url}\n")
        webbrowser.open(auth_url)
        while auth_code is None: time.sleep(0.5)
        httpd.shutdown(); httpd.server_close()
        token_data = {'code': auth_code, 'client_id': self._OAUTH_CLIENT_ID, 'client_secret': self._OAUTH_CLIENT_SECRET, 'redirect_uri': redirect_uri, 'grant_type': 'authorization_code'}
        response = httpx.post(self._TOKEN_URI, data=token_data); response.raise_for_status(); token_info = response.json()
        creds = Credentials(token=token_info['access_token'], refresh_token=token_info.get('refresh_token'), token_uri=self._TOKEN_URI, client_id=self._OAUTH_CLIENT_ID, client_secret=self._OAUTH_CLIENT_SECRET, scopes=token_info['scope'].split())
        print("Authentication successful."); self._save_credentials(creds)
        return creds

    async def _setup_user(self) -> str:
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
            print("Onboarding in progress, waiting 5 seconds..."); await asyncio.sleep(5)
            lro_res = await self._make_api_request(operation_name, http_method='GET')
        project_id = lro_res.get('response', {}).get('cloudaicompanionProject', {}).get('id', '')
        if not project_id: print("Warning: Onboarding complete but no project ID returned.")
        return project_id

    async def _make_api_request(self, endpoint: str, body: Dict[str, Any] = None, stream: bool = False, http_method: str = 'POST') -> Union[Dict[str, Any], httpx.Response]:
        async def api_call():
            if not self.credentials.valid: self.credentials.refresh(Request())
            client_metadata_str = ",".join([f"{k}={v}" for k, v in self._get_client_metadata().items()])
            headers = {'Authorization': f'Bearer {self.credentials.token}','Content-Type': 'application/json','User-Agent': f'GeminiCLI-Python-Client/{self._PLUGIN_VERSION}','Client-Metadata': client_metadata_str,}
            if endpoint.startswith('operations/'): url = f"{self._CODE_ASSIST_ENDPOINT}/{endpoint}"
            else: url = f"{self._CODE_ASSIST_ENDPOINT}/{self._API_VERSION}:{endpoint}"
            params = {'alt': 'sse'} if stream else {}
            logging.debug(f"Making API call: {http_method.upper()} {url}")
            if http_method.upper() == 'POST':
                request = self.http_client.build_request("POST", url, headers=headers, json=body, params=params)
            elif http_method.upper() == 'GET':
                request = self.http_client.build_request("GET", url, headers=headers, params=params)
            else:
                raise ValueError(f"Unsupported HTTP method: {http_method}")
            response = await self.http_client.send(request, stream=stream)
            response.raise_for_status()
            return response
        try:
            response = await retry_with_backoff(api_call)
            return response if stream else response.json()
        except httpx.HTTPStatusError as e:
            raise to_friendly_error(e) from e
        except Exception as e:
            raise e

class ChatSession:
    """Manages a single conversation, including history and tool use."""
    def __init__(self, client: GeminiClient, config: Config, model: str):
        self.client = client
        self.config = config
        self.model = model
        self.history: List[Dict[str, Any]] = []
        self.tool_registry = ToolRegistry(self.config)
        # The scheduler is now created inside the Turn, not here.
        self.system_instruction = self._initialize_chat_history()

    def _initialize_chat_history(self) -> Dict:
        system_prompt_text = (
            "You are an interactive CLI agent. Your primary goal is to help users safely "
            "and efficiently by utilizing your available tools."
        )
        self.history = [
            {"role": "user", "parts": [{"text": "Hello, let's get started."}]},
            {"role": "model", "parts": [{"text": "OK. I'm ready to help."}]}
        ]
        logging.info("Chat history initialized.")
        return {"role": "user", "parts": [{"text": system_prompt_text}]}

    async def send_message(self, prompt: str) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Creates and runs a new conversational turn, yielding events as they happen.
        """
        turn = Turn(session=self, prompt=prompt)
        async for event in turn.run():
            yield event