#
# File: auth.py
# Revision: 1
# Description: Handles all user authentication (OAuth2) and backend
# user setup/onboarding logic.
#

import asyncio
import httpx
import json
import logging
import secrets
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# This import will work after we create the next file.
from api_client import CodeAssistServer

# ---
# Constants (from core/code_assist/oauth2.ts)
# ---
OAUTH_CLIENT_ID = '681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com'
OAUTH_CLIENT_SECRET = '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'
OAUTH_SCOPE = [
    'https://www.googleapis.com/auth/cloud-platform',
    'https://www.googleapis.com/auth/userinfo.email',
    'https://www.googleapis.com/auth/userinfo.profile',
    'openid'
]
TOKEN_URI = "https://oauth2.googleapis.com/token"
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
SIGN_IN_SUCCESS_URL = 'https://developers.google.com/gemini-code-assist/auth_success_gemini'
SETTINGS_DIRECTORY_NAME = '.gemini' # Also needed here for credential path

# ---
# OAuth Client and Credential Management
# ---

def get_cached_credential_path() -> Path:
    """Gets the path to the cached credential file."""
    return Path.home() / SETTINGS_DIRECTORY_NAME / 'oauth_creds.json'

def cache_credentials(credentials: Credentials):
    """Saves credentials to a file."""
    file_path = get_cached_credential_path()
    file_path.parent.mkdir(parents=True, exist_ok=True)
    logging.debug(f"Caching credentials to {file_path}")
    with open(file_path, 'w') as f:
        f.write(credentials.to_json())

def load_cached_credentials() -> Credentials | None:
    """Loads credentials from a file if they exist and are valid."""
    creds_path = get_cached_credential_path()
    if creds_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(creds_path), OAUTH_SCOPE)
            if creds and creds.valid:
                if creds.expired and creds.refresh_token:
                    logging.info("Cached credentials expired, refreshing...")
                    creds.refresh(Request())
                    cache_credentials(creds)
                logging.info("Successfully loaded valid cached credentials.")
                return creds
        except Exception as e:
            logging.error(f"Failed to load cached credentials: {e}", exc_info=True)
    return None

def run_manual_oauth_flow() -> Credentials:
    """
    Performs the low-level manual OAuth flow using a local server to
    capture the auth code. Replicates oauth2.ts.
    """
    port = get_available_port()
    redirect_uri = f'http://localhost:{port}/oauth2callback'
    state = secrets.token_urlsafe(32)

    auth_params = {
        'redirect_uri': redirect_uri, 'client_id': OAUTH_CLIENT_ID,
        'access_type': 'offline', 'response_type': 'code',
        'scope': ' '.join(OAUTH_SCOPE), 'state': state
    }
    auth_url = f"{AUTH_URI}?{urlencode(auth_params)}"

    auth_code_event = threading.Event()
    auth_code, server_error = None, None

    class OAuthCallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal auth_code, server_error
            query = parse_qs(urlparse(self.path).query)
            if query.get('state', [None])[0] != state:
                server_error = Exception("State mismatch error (possible CSRF).")
            elif 'code' in query:
                auth_code = query['code'][0]
                self.send_response(301, "Redirect")
                self.send_header('Location', SIGN_IN_SUCCESS_URL)
                self.end_headers()
            else:
                server_error = Exception(f"OAuth error in callback: {query.get('error')}")
            auth_code_event.set()
        def log_message(self, format, *args):
            logging.debug(f"HTTP Server: {args[0]}")

    server = HTTPServer(('localhost', port), OAuthCallbackHandler)
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.daemon = True
    server_thread.start()

    logging.info(f"\nPlease visit this URL to authorize this application:\n\n{auth_url}\n")
    webbrowser.open(auth_url)

    auth_code_event.wait()
    server.shutdown()

    if server_error: raise server_error
    if not auth_code: raise Exception("Failed to retrieve authorization code.")

    logging.info("Authorization code received. Exchanging for access token...")
    token_request_body = {
        'code': auth_code, 'client_id': OAUTH_CLIENT_ID, 'client_secret': OAUTH_CLIENT_SECRET,
        'redirect_uri': redirect_uri, 'grant_type': 'authorization_code'
    }

    with httpx.Client() as client:
        response = client.post(TOKEN_URI, data=token_request_body)
        response.raise_for_status()
        token_data = response.json()

    creds = Credentials(
        token=token_data['access_token'], refresh_token=token_data.get('refresh_token'),
        token_uri=TOKEN_URI, client_id=OAUTH_CLIENT_ID, client_secret=OAUTH_CLIENT_SECRET,
        scopes=token_data['scope'].split()
    )
    cache_credentials(creds)
    logging.info("Authentication successful. Tokens cached.")
    return creds

def get_available_port():
    """Finds and returns an available port on localhost."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('localhost', 0))
    port = sock.getsockname()[1]
    sock.close()
    logging.debug(f"Found available port: {port}")
    return port

def get_oauth_client() -> Credentials:
    """
    Main entry point for authentication. Returns cached credentials or
    initiates the full OAuth flow.
    """
    creds = load_cached_credentials()
    return creds or run_manual_oauth_flow()

# ---
# User Onboarding (replicating core/code_assist/setup.ts)
# ---

async def setup_user(credentials: Credentials, project_id: str | None) -> str:
    """
    Orchestrates the user setup and onboarding process against the backend.
    """
    ca_server = CodeAssistServer(credentials, project_id)
    client_metadata = { 'pluginType': 'GEMINI', 'duetProject': project_id }
    logging.info("Loading Code Assist configuration...")
    load_req = {'cloudaicompanionProject': project_id, 'metadata': client_metadata}
    try:
        load_res = await ca_server.call_endpoint('loadCodeAssist', load_req)
        
        allowed_tiers = load_res.get('allowedTiers', []) or []
        default_tier = next((t['id'] for t in allowed_tiers if t.get('isDefault')), 'legacy-tier')
        
        logging.info(f"Onboarding user to tier: {default_tier}...")
        onboard_req = {
            'tierId': default_tier,
            'cloudaicompanionProject': load_res.get('cloudaicompanionProject') or project_id or '',
            'metadata': client_metadata,
        }
        lro_res = await ca_server.call_endpoint('onboardUser', onboard_req)
        
        while not lro_res.get('done'):
            logging.info("Onboarding in progress, waiting 5 seconds...")
            await asyncio.sleep(5)
            # In a real scenario, a get LRO status endpoint might be called.
            # Here we re-call as per the blueprint's polling logic.
            lro_res = await ca_server.call_endpoint('onboardUser', onboard_req)
            
        logging.info("User onboarding complete.")
        return lro_res.get('response', {}).get('cloudaicompanionProject', {}).get('id', '')
    finally:
        await ca_server.close()
