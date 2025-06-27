# Gemini Agentic CLI in Python

This is a simple proof-of-concept how the functionality of google [gemini-cli](https://github.com/google-gemini/gemini-cli) could be implemented in python.
The interactive command-line interface (CLI) acts as an agentic AI assistant, powered by Google's Gemini models. It leverages an OAuth2 flow to securely authenticate with Google services and uses a suite of tools to understand and interact with your local development environment. The agent is capable of understanding natural language commands, executing shell commands, reading and writing files, and searching through a codebase to answer questions or perform tasks.

## Features

  * **Agentic Architecture**:

      * **Multi-Step Task Execution**: The agent can autonomously perform complex tasks that require multiple tool calls and reasoning steps without needing manual intervention for each step.
      * **Interactive REPL & Non-Interactive Mode**: Use it in a continuous, conversational REPL shell or pass a prompt directly from the command line for single-shot execution.
      * **Session Persistence**: Automatically saves your conversation state, allowing you to resume your session later. Includes a `/reset` command to start fresh.

  * **Comprehensive Tool Suite**: The agent is equipped with a set of tools to interact with your environment:

      * **Code Navigation**: `list_directory`, `glob`, and `search_file_content` (using `git grep`) to understand your codebase.
      * **File Operations**: `read_file`, `write_file`, and `replace_in_file` for targeted code edits.
      * **System Execution**: A `shell` tool to run commands, linters, or tests.
      * **Knowledge & Memory**: Built-in `Google Search` for up-to-date information and a `save_memory` tool to retain facts across sessions in a global `GEMINI.md` file.

  * **Robust Safety Mechanisms**:

      * **User Confirmation**: Prompts for user approval before any potentially destructive action (e.g., executing shell commands, overwriting files, or making edits).
      * **Diff Previews**: Before applying code changes with `replace_in_file`, the agent shows you a git-style diff of the proposed modifications.
      * **Automatic Project Snapshots**: Before making any changes, the agent uses a hidden, shadow git repository to automatically create a snapshot of your project, giving you a safety net.
      * **Scoped File Access**: Intelligently ignores files specified in your `.gitignore` and `.geminiignore` files.

  * **Resilience & Intelligence**:

      * **API Error Handling**: Features automatic retries with exponential backoff to gracefully handle transient API or network issues.
      * **Rate Limit Fallback**: To ensure high availability, the agent will automatically and temporarily switch from `gemini-2.5-pro` to the faster `gemini-2.5-flash` model if it detects persistent rate-limiting.

  * **Flexible Configuration**:

      * Load settings from command-line flags, `.env` files, a workspace `.gemini/settings.json`, and a global `~/.gemini/settings.json`.
      * Toggle verbose debug logging on the fly with a `--debug` flag or a `/debug` REPL command.

## Available Tools

| Tool                  | Description                                                  | Confirmation?     |
| --------------------- | ------------------------------------------------------------ | ----------------- |
| `shell`               | Executes a shell command in the project directory.           | **Yes** |
| `write_file`          | Writes content to a file, overwriting it if it exists.       | **Yes** |
| `replace_in_file`     | Replaces a specific string in a file.                        | **Yes (with Diff)** |
| `save_memory`         | Saves a fact to a global long-term memory file.              | **Yes** |
| `list_directory`      | Lists files and subdirectories, respecting ignore rules.     | No                |
| `glob`                | Finds files matching a glob pattern (e.g., `src/**/*.py`).   | No                |
| `search_file_content` | Searches for a regex pattern in all files using `git grep`.  | No                |
| `read_file`           | Reads the content of a specified file.                       | No                |
| `Google Search`       | Performs a web search for current events or information.     | No                |

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