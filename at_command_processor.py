#
# File: at_command_processor.py
# Revision: 1 (New)
# Description:
# This module handles the parsing and processing of `@<path>` commands,
# allowing file and directory content to be injected directly into the prompt context.
#

import logging
from pathlib import Path
from typing import List, Dict, Union, Literal

from config import Config
from services.file_discovery_service import FileDiscoveryService
from tool_registry import ToolRegistry

# A structured representation of the parsed prompt
AtCommandPart = Dict[Literal["type", "content"], str]
PromptPart = Dict[Literal["text"], str]
ProcessedPrompt = List[PromptPart]

async def handle_at_command(
    prompt: str, config: Config, tool_registry: ToolRegistry
) -> ProcessedPrompt:
    """
    Parses a prompt for @-commands, reads the files, and returns a structured
    list of parts for the model.
    """
    if '@' not in prompt:
        return [{"text": prompt}]

    logging.info(f"Processing @-commands in prompt: {prompt[:80]}...")
    
    # This is a simplified parser. A more robust one would handle escaped @'s.
    parts: List[AtCommandPart] = []
    current_index = 0
    for match in re.finditer(r'@(\S+)', prompt):
        start, end = match.span()
        # Add text before the @-command
        if start > current_index:
            parts.append({"type": "text", "content": prompt[current_index:start]})
        
        # Add the @-command path
        parts.append({"type": "at_path", "content": match.group(1)})
        current_index = end

    # Add any remaining text after the last @-command
    if current_index < len(prompt):
        parts.append({"type": "text", "content": prompt[current_index:]})

    processed_parts: ProcessedPrompt = []
    initial_text_parts = []
    
    read_file_tool = tool_registry.get_tool("read_file")
    if not read_file_tool:
        logging.error("read_file tool not found, cannot process @-commands.")
        return [{"text": prompt}]

    for part in parts:
        if part["type"] == "text":
            initial_text_parts.append(part["content"])
        elif part["type"] == "at_path":
            path_str = part["content"]
            initial_text_parts.append(f"@{path_str}") # Add the original reference to the main prompt
            
            logging.debug(f"Reading content for @{path_str}")
            try:
                # Use the read_file tool's logic to read the file
                result = await read_file_tool.execute(path=path_str)
                if "error" in result:
                    content = f"Error reading {path_str}: {result['error']}"
                else:
                    content = result.get("content", f"Error: No content found for {path_str}")
                
                # Add the file content as a separate, clearly marked part
                processed_parts.append({"text": f"\n\n--- Content from @{path_str} ---\n{content}"})

            except Exception as e:
                logging.error(f"Failed to process @{path_str}: {e}")
                processed_parts.append({"text": f"\n\n--- Error reading @{path_str}: {e} ---"})

    final_prompt = "".join(initial_text_parts)
    return [{"text": final_prompt}] + processed_parts