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
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError

class GeminiOauthClient:
    _OAUTH_CLIENT_ID = "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
    _OAUTH_CLIENT_SECRET = "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"
    _OAUTH_SCOPES = [
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
    ]
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

        self.history = []
        self.credentials = self._get_credentials()
        self.project_id = self._setup_user()
        print(f"User setup complete. Using Project ID: {self.project_id}")

    def _get_credentials(self) -> Credentials:
        if self.credentials_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(self.credentials_path, self._OAUTH_SCOPES)
                if creds and creds.valid:
                    return creds
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
        
        auth_code = None
        server_thread = None
        httpd = None
        state = secrets.token_urlsafe(16)

        class OAuthCallbackHandler(http.server.SimpleHTTPRequestHandler):
            def do_GET(self):
                nonlocal auth_code
                parsed_path = urllib.parse.urlparse(self.path)
                query_params = urllib.parse.parse_qs(parsed_path.query)
                
                if query_params.get('state', [None])[0] != state:
                    self.send_response(400); self.end_headers()
                    self.wfile.write(b"State mismatch error.")
                    return

                if 'code' in query_params:
                    auth_code = query_params['code'][0]
                    self.send_response(200); self.end_headers()
                    self.wfile.write(b"<html><body><h1>Authentication successful!</h1><p>You can close this window.</p></body></html>")
                else:
                    self.send_response(400); self.end_headers()
                    self.wfile.write(b"Authentication failed.")

        def start_server():
            nonlocal httpd
            with socketserver.TCPServer(("localhost", 0), OAuthCallbackHandler) as s:
                httpd = s
                httpd.serve_forever()

        server_thread = threading.Thread(target=start_server); server_thread.daemon = True; server_thread.start()
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
        response = requests.post(self._TOKEN_URI, data=token_data); response.raise_for_status()
        token_info = response.json()

        creds = Credentials(token=token_info['access_token'], refresh_token=token_info.get('refresh_token'), token_uri=self._TOKEN_URI, client_id=self._OAUTH_CLIENT_ID, client_secret=self._OAUTH_CLIENT_SECRET, scopes=token_info['scope'].split())
        print("Authentication successful."); self._save_credentials(creds)
        return creds

    def _make_api_request(self, method: str, body: dict) -> dict:
        """Helper to make authenticated POST requests to the API."""
        self.credentials.refresh(Request())
        headers = {'Authorization': f'Bearer {self.credentials.token}', 'Content-Type': 'application/json'}
        url = f"{self._CODE_ASSIST_ENDPOINT}/{self._API_VERSION}:{method}"
        response = requests.post(url, headers=headers, json=body); response.raise_for_status()
        return response.json()

    def _setup_user(self) -> str:
        """mandatory user setup flow"""
        print("Performing user onboarding...")
        initial_project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
        client_metadata = {'ideType': 'IDE_UNSPECIFIED', 'platform': 'PLATFORM_UNSPECIFIED', 'pluginType': 'GEMINI', 'duetProject': initial_project_id}
        load_assist_req = {'cloudaicompanionProject': initial_project_id, 'metadata': client_metadata}
        load_res = self._make_api_request('loadCodeAssist', load_assist_req)
        default_tier = next((t for t in load_res.get('allowedTiers', []) if t.get('isDefault')), None)
        onboard_tier_id = default_tier['id'] if default_tier else 'legacy-tier'
        onboard_req = {'tierId': onboard_tier_id, 'cloudaicompanionProject': load_res.get('cloudaicompanionProject') or initial_project_id or '', 'metadata': client_metadata}
        lro_res = self._make_api_request('onboardUser', onboard_req)
        while not lro_res.get('done', False):
            print("Onboarding in progress, waiting 5 seconds...")
            time.sleep(5)
            lro_res = self._make_api_request('onboardUser', onboard_req)
        final_project_id = lro_res.get('response', {}).get('cloudaicompanionProject', {}).get('id', '')
        return final_project_id

    def send_message(self, prompt: str, model: str) -> str:
        """Sends a message, maintains history, and returns the streamed response."""
        self.history.append({'role': 'user', 'parts': [{'text': prompt}]})
        
        body = {'model': model, 'request': {'contents': self.history}}
        if self.project_id:
            body['project'] = self.project_id

        self.credentials.refresh(Request())
        headers = {'Authorization': f'Bearer {self.credentials.token}', 'Content-Type': 'application/json', 'User-Agent': 'GeminiCLI-Python-Client/0.3'}
        url = f"{self._CODE_ASSIST_ENDPOINT}/{self._API_VERSION}:streamGenerateContent?alt=sse"
        
        full_response_text = ""
        try:
            with requests.post(url, headers=headers, json=body, stream=True, timeout=300) as response:
                response.raise_for_status()
                buffer = ""
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
        except requests.exceptions.HTTPError as e:
            print(f"\n\nHTTP Error: {e.response.status_code}\nResponse Body: {e.response.text}")
            self.history.pop() # Remove the failed user prompt from history
            return f"[ERROR: Request failed with status {e.response.status_code}]"
        
        print() # Final newline after streaming
        self.history.append({'role': 'model', 'parts': [{'text': full_response_text}]})
        return full_response_text

def main():
    """Main function to handle CLI arguments and application flow."""
    parser = argparse.ArgumentParser(
        description="A command-line interface for Google Gemini using OAuth.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "prompt",
        nargs='?',
        default=None,
        help="The prompt to send to the model. If omitted, starts an interactive chat session."
    )
    parser.add_argument(
        "-m", "--model",
        default="gemini-2.5-pro",
        help="The model to use for generation (e.g., gemini-2.5-pro)."
    )
    parser.add_argument(
        "-c", "--chat",
        action="store_true",
        help="Force an interactive chat session, even if an initial prompt is provided."
    )
    args = parser.parse_args()

    try:
        print("Initializing Gemini OAuth Client...")
        client = GeminiOauthClient()
        print("\nClient initialized successfully!")
        
        # Decide mode: one-shot or interactive chat
        is_interactive = args.chat or (args.prompt is None)

        if not is_interactive:
            # --- One-Shot Mode ---
            print(f"\n--- Sending Prompt (Model: {args.model}) ---\n'{args.prompt}'\n\n--- Gemini Response ---")
            client.send_message(args.prompt, args.model)
            print("-----------------------")
        else:
            # --- Interactive Chat Mode ---
            print(f"\n--- Starting Interactive Chat (Model: {args.model}) ---")
            print("Type 'quit' or 'exit' to end the session.")
            
            if args.prompt:
                print(f"\n> {args.prompt}")
                print("\n--- Gemini ---")
                client.send_message(args.prompt, args.model)

            while True:
                user_input = input("\n> ")
                if user_input.lower() in ["quit", "exit"]:
                    print("Ending chat session. Goodbye!")
                    break
                print("\n--- Gemini ---")
                client.send_message(user_input, args.model)

    except Exception as e:
        import traceback
        print(f"\nAn unexpected error occurred: {e}")
        traceback.print_exc()

if __name__ == '__main__':
    main()
