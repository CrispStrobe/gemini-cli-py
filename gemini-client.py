import os
import json
import webbrowser
import http.server
import socketserver
import threading
import secrets
import urllib.parse
import requests
import time
import argparse
from pathlib import Path
from typing import Union
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError

class Models:
    DEFAULT = "gemini-2.5-pro"
    FLASH = "gemini-2.5-flash"
    
    @classmethod
    def all(cls):
        """Returns a list of all available generation models."""
        return [cls.DEFAULT, cls.FLASH]

class Token:
    DEFAULT_LIMIT = 1_048_576

    @classmethod
    def limit(cls, model_name: str) -> int:
        """Returns the token limit for a given model."""
        if model_name in [Models.DEFAULT, Models.FLASH]:
            return 1_048_576
        return cls.DEFAULT_LIMIT

class GeminiClient:
    """
    Handles authentication, user onboarding, and low-level API requests.
    """
    _OAUTH_CLIENT_ID = "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
    _OAUTH_CLIENT_SECRET = "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"
    _OAUTH_SCOPES = ["https://www.googleapis.com/auth/cloud-platform", "https://www.googleapis.com/auth/userinfo.email", "https://www.googleapis.com/auth/userinfo.profile"]
    _CREDENTIALS_FILENAME = "oauth_creds.json"
    _TOKEN_URI = "https://oauth2.googleapis.com/token"
    _AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
    _CODE_ASSIST_ENDPOINT = "https://cloudcode-pa.googleapis.com"
    _API_VERSION = "v1internal"

    def __init__(self, credentials_dir: Path = None):
        if credentials_dir is None:
            self.credentials_path = Path.home() / ".gemini" / self._CREDENTIALS_FILENAME
        else:
            self.credentials_path = credentials_dir / self._CREDENTIALS_FILENAME

        self.credentials = self._get_credentials()
        self.project_id = self._setup_user()
        print(f"User setup complete. Using Project ID: {self.project_id}")

    def start_chat(self, model: str) -> 'ChatSession':
        """Starts a new chat session."""
        return ChatSession(self, model)

    def _get_credentials(self) -> Credentials:
        if self.credentials_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(self.credentials_path, self._OAUTH_SCOPES)
                if creds and creds.valid: return creds
                if creds and creds.expired and creds.refresh_token:
                    print("Credentials expired, refreshing...")
                    creds.refresh(Request())
                    self._save_credentials(creds)
                    return creds
            except (ValueError, RefreshError, Exception) as e:
                print(f"Could not load or refresh credentials, re-authenticating: {e}")
        return self._run_oauth_flow()

    def _save_credentials(self, creds: Credentials):
        self.credentials_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.credentials_path, 'w') as f:
            f.write(creds.to_json())
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
        response = requests.post(self._TOKEN_URI, data=token_data); response.raise_for_status(); token_info = response.json()
        creds = Credentials(token=token_info['access_token'], refresh_token=token_info.get('refresh_token'), token_uri=self._TOKEN_URI, client_id=self._OAUTH_CLIENT_ID, client_secret=self._OAUTH_CLIENT_SECRET, scopes=token_info['scope'].split())
        print("Authentication successful."); self._save_credentials(creds)
        return creds

    def _setup_user(self) -> str:
        print("Performing user onboarding..."); initial_project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
        client_metadata = {'ideType': 'IDE_UNSPECIFIED', 'platform': 'PLATFORM_UNSPECIFIED', 'pluginType': 'GEMINI', 'duetProject': initial_project_id}
        load_assist_req = {'cloudaicompanionProject': initial_project_id, 'metadata': client_metadata}
        # This call now correctly receives a dictionary
        load_res = self._make_api_request('loadCodeAssist', load_assist_req)
        default_tier = next((t for t in load_res.get('allowedTiers', []) if t.get('isDefault')), None)
        onboard_tier_id = default_tier['id'] if default_tier else 'legacy-tier'
        onboard_req = {'tierId': onboard_tier_id, 'cloudaicompanionProject': load_res.get('cloudaicompanionProject') or initial_project_id or '', 'metadata': client_metadata}
        lro_res = self._make_api_request('onboardUser', onboard_req)
        while not lro_res.get('done', False):
            print("Onboarding in progress, waiting 5 seconds..."); time.sleep(5)
            # This call also now correctly receives a dictionary
            lro_res = self._make_api_request('onboardUser', onboard_req)
        return lro_res.get('response', {}).get('cloudaicompanionProject', {}).get('id', '')
    
    def _make_api_request(self, method: str, body: dict, stream: bool = False) -> Union[dict, requests.Response]:
        """
        Centralized method for making raw API requests.
        Returns a dictionary for non-streaming requests, and a Response object for streaming requests.
        """
        self.credentials.refresh(Request())
        headers = {'Authorization': f'Bearer {self.credentials.token}', 'Content-Type': 'application/json', 'User-Agent': 'GeminiCLI-Python-Client/1.1'}
        url = f"{self._CODE_ASSIST_ENDPOINT}/{self._API_VERSION}:{method}"
        params = {'alt': 'sse'} if stream else {}
        
        response = requests.post(url, headers=headers, json=body, stream=stream, timeout=300, params=params)
        response.raise_for_status()
        if not stream:
            return response.json()
        return response

    def count_tokens(self, model: str, contents: list) -> int:
        """Calls the countTokens endpoint."""
        body = {'request': {'model': f'models/{model}', 'contents': contents}}
        if self.project_id: body['project'] = self.project_id
        try:
            # This call now correctly receives a dictionary
            response_json = self._make_api_request('countTokens', body)
            return response_json.get('totalTokens', 0)
        except requests.exceptions.HTTPError as e:
            print(f"\n[Error counting tokens: {e}]")
            return 0
            
class ChatSession:
    """Manages a single conversation, including history and advanced features."""
    def __init__(self, client: GeminiClient, model: str):
        self.client = client
        self.model = model
        self.history = []

    def _check_and_compress_chat_if_needed(self):
        if not self.history: return
        token_limit = Token.limit(self.model)
        if self.client.count_tokens(self.model, self.history) > token_limit * 0.9:
            print("\n[INFO] Context window is getting full. Summarizing conversation to save tokens...")
            summarization_prompt = "You are a helpful assistant. Summarize our conversation up to this point. The summary should be a concise yet comprehensive overview of all key topics, questions, and answers. This summary will replace the current chat history to conserve tokens, so it must capture everything essential to continue our conversation effectively as if no information was lost."
            temp_history = self.history + [{'role': 'user', 'parts': [{'text': summarization_prompt}]}]
            body = {'model': self.model, 'request': {'contents': temp_history}}
            if self.client.project_id: body['project'] = self.client.project_id
            try:
                # This call now correctly receives a dictionary
                response_json = self.client._make_api_request('generateContent', body)
                summary_text = response_json.get('response', {}).get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
                if summary_text:
                    self.history = [{'role': 'user', 'parts': [{'text': "Previous conversation summary:"}]}, {'role': 'model', 'parts': [{'text': summary_text}]}]
                    print("[INFO] Conversation summarized successfully.\n")
                else:
                    print("[WARN] Failed to summarize conversation, history may be lost on next turn.\n")
            except (requests.exceptions.HTTPError, KeyError, IndexError) as e:
                print(f"[ERROR] Could not summarize conversation: {e}\n")

    def _handle_flash_fallback(self):
        if self.model == Models.FLASH: return
        print(f"\n[INFO] ⚡ Persistent rate limiting detected. Automatically switching from {self.model} to {Models.FLASH} for this session to improve responsiveness.")
        self.model = Models.FLASH
    
    def send_message(self, prompt: str) -> str:
        self._check_and_compress_chat_if_needed()
        self.history.append({'role': 'user', 'parts': [{'text': prompt}]})
        
        max_retries = 3; rate_limit_errors = 0
        while True:
            body = {'model': self.model, 'request': {'contents': self.history}}
            if self.client.project_id: body['project'] = self.client.project_id
            try:
                # This call correctly receives a raw Response object because stream=True
                response = self.client._make_api_request('streamGenerateContent', body, stream=True)
                return self._process_stream(response)
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429:
                    rate_limit_errors += 1
                    if rate_limit_errors >= max_retries:
                        self._handle_flash_fallback()
                        rate_limit_errors = 0; continue
                    else:
                        wait_time = 2 ** rate_limit_errors
                        print(f"\n[WARN] Rate limit exceeded. Retrying in {wait_time} seconds... ({rate_limit_errors}/{max_retries})")
                        time.sleep(wait_time)
                else:
                    print(f"\n\nHTTP Error: {e.response.status_code}\nResponse Body: {e.response.text}")
                    self.history.pop(); return f"[ERROR: Request failed with status {e.response.status_code}]"
        
    def _process_stream(self, response: requests.Response) -> str:
        full_response_text = ""; buffer = ""
        for line in response.iter_lines():
            line_str = line.decode('utf-8')
            if not line_str:
                if not buffer: continue
                try:
                    json_data = json.loads(buffer)
                    candidate = json_data.get('response', {}).get('candidates', [{}])[0]
                    part = candidate.get('content', {}).get('parts', [{}])[0]
                    text_part = part.get('text', '')
                    if text_part:
                        print(text_part, end='', flush=True)
                        full_response_text += text_part
                except (json.JSONDecodeError, KeyError, IndexError): pass
                finally: buffer = ""
            elif line_str.startswith('data: '):
                buffer += line_str[6:]
        print()
        self.history.append({'role': 'model', 'parts': [{'text': full_response_text}]})
        return full_response_text

def main():
    parser = argparse.ArgumentParser(description="A command-line interface for Google Gemini using OAuth.", formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("prompt", nargs='?', default=None, help="The prompt to send to the model. If omitted, starts an interactive chat session.")
    parser.add_argument("-m", "--model", default=Models.DEFAULT, choices=Models.all(), help=f"The model to use for generation (default: {Models.DEFAULT}).")
    parser.add_argument("-c", "--chat", action="store_true", help="Force an interactive chat session, even if an initial prompt is provided.")
    args = parser.parse_args()
    try:
        print("Initializing Gemini OAuth Client...")
        client = GeminiClient()
        chat_session = client.start_chat(args.model)
        print("\nClient initialized successfully!")
        is_interactive = args.chat or (args.prompt is None)
        if not is_interactive:
            print(f"\n--- Sending Prompt (Model: {args.model}) ---\n'{args.prompt}'\n\n--- Gemini Response ---")
            chat_session.send_message(args.prompt)
            print("-----------------------")
        else:
            print(f"\n--- Starting Interactive Chat (Model: {args.model}) ---")
            print("Type 'quit' or 'exit' to end the session.")
            if args.prompt:
                print(f"\n> {args.prompt}")
                print("\n--- Gemini ---")
                chat_session.send_message(args.prompt)
            while True:
                user_input = input("\n> ")
                if user_input.lower() in ["quit", "exit"]:
                    print("Ending chat session. Goodbye!")
                    break
                print("\n--- Gemini ---")
                chat_session.send_message(user_input)
    except (KeyboardInterrupt):
        print("\n\nExiting application.")
    except Exception as e:
        import traceback
        print(f"\nAn unexpected error occurred: {e}")
        traceback.print_exc()

if __name__ == '__main__':
    main()
