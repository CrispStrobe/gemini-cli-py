
# Gemini Agentic CLI in Python

This is a simple proof-of-concept how the functionality of google [gemini-cli](https://github.com/google-gemini/gemini-cli) could be done in python.
The interactive command-line interface (CLI) acts as an agentic AI assistant, powered by Google's Gemini models. It leverages an OAuth2 flow to securely authenticate with Google services and uses a suite of file system tools to understand and interact with your local development environment. The agent is capable of understanding natural language commands, executing shell commands, reading and writing files, and searching through a codebase to answer questions or perform tasks.

## Features

  * **Interactive REPL:** A Read-Eval-Print Loop allows for continuous, conversational interaction with the agent.
  * **Secure Authentication:** Uses a standard OAuth2 flow with a local server to securely authenticate the user, caching credentials for future sessions.
  * **Agentic Tool Use:** The agent can autonomously decide to use tools to fulfill requests. The full agentic loop is implemented:
    1.  The model receives a prompt and tool definitions.
    2.  It can request one or more tool calls in a single turn.
    3.  The client executes these tools (e.g., running a shell command).
    4.  The results are sent back to the model for processing.
    5.  The model formulates a final, natural language response based on the tool output.
  * **File System Tools:**
      * `shell`: Executes shell commands within the project's directory.
      * `list_directory`: Lists files and subdirectories at a given path.
      * `glob`: Finds files matching a specific glob pattern (e.g., `**/*.py`).
      * `search_file_content`: Performs a fast, regular expression search within project files using `git grep`.
      * `read_file`: Reads the entire content of a specific file.
      * `write_file`: Writes content to a file, creating it if necessary or overwriting it.
  * **Context-Aware File Filtering:** The agent is aware of your version control setup. It automatically respects rules in `.gitignore` and a custom `.geminiignore` file, ensuring it doesn't interact with unintended files.
  * **Resilient API Communication:** Includes an exponential backoff and retry mechanism to gracefully handle transient API errors like rate limiting (`429 Too Many Requests`).

## Setup and Installation

1.  **Clone the Repository:**

    ```bash
    git clone https://github.com/CrispStrobe/gemini-cli-py/
    cd gemini-cli-py
    ```

2.  **Install Dependencies:** This project requires Python 3.11+ and a few external libraries.

    ```bash
    pip install httpx "google-auth-oauthlib>=1.2.0" pathspec python-dotenv
    ```

3.  **Environment Configuration (Optional):**
    If you are using an Enterprise account, you may need to specify your Google Cloud Project ID. You can do this by creating a `.env` file in the project root:

    ```
    # .env
    GOOGLE_CLOUD_PROJECT="your-gcp-project-id"
    ```

## Usage

Run the client from your terminal.

### Interactive Mode (REPL)

To start the interactive shell, simply run the script:

```bash
python ./main.py
```

The application will guide you through a one-time browser-based authentication flow. After that, you'll be dropped into the REPL, ready to chat.

**Example Session:**

```
> use the shell tool to list files in the current directory

--- Gemini ---

[AGENT] Calling Tool: shell with args: {'command': 'ls'}
[AGENT] Got Result from shell: {'stdout': 'config.py\ngemini_client.py\nmain.py...', 'stderr': '', 'returncode': 0}
OK. I see the following files and directories: `config.py`, `gemini_client.py`, `main.py`, `services`, `tools`, and `utils`.

What would you like to do next?
----------------

> find all python files

--- Gemini ---

[AGENT] Calling Tool: glob with args: {'pattern': '**/*.py'}
[AGENT] Got Result from glob: {'files': ['main.py', 'tools/core_tools.py', ...]}
I found the following Python files: `main.py`, `tools/core_tools.py`, `config.py`, `gemini_client.py`, and more.

Let me know if you need anything else.
----------------
```

To exit the session, type `quit` or `exit` and press Enter.

### Non-Interactive Mode

You can also pass an initial prompt directly from the command line for a single-shot execution.

```bash
python ./main.py "search for 'class GeminiClient' in all python files"
```