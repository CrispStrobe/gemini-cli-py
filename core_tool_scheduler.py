#
# File: core_tool_scheduler.py
# Revision: 5
# Description: The tool call is now executed immediately within 
# handle_confirmation_response after receiving user approval. This simplifies
# the control flow and ensures confirmed tools are always run.
#

import asyncio
import json
import logging
from typing import List, Dict, Any, Literal, TypedDict, TYPE_CHECKING

from tool_registry import ToolRegistry
from tools.tool_io import ToolConfirmationOutcome

if TYPE_CHECKING:
    from turn import Turn

# --- Type Definitions for Tool Call States ---

ToolCallStatus = Literal["validating", "awaiting_approval", "executing", "success", "error", "cancelled"]

class BaseToolCall(TypedDict, total=False):
    request: Dict[str, Any]
    status: ToolCallStatus
    tool: Any # A Tool object
    confirmation_details: Dict[str, Any]
    response: Dict[str, Any]

class CoreToolScheduler:
    """
    Manages the lifecycle of tool calls, including a user confirmation step.
    """
    def __init__(self, tool_registry: ToolRegistry, turn: 'Turn'):
        self._tool_registry = tool_registry
        self._turn = turn
        self._tool_calls: List[BaseToolCall] = []
        logging.info("CoreToolScheduler initialized.")

    def clear_state(self):
        """Clears the internal state for a new turn."""
        self._tool_calls = []

    async def schedule(self, function_calls: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Schedules and processes tool calls."""
        self.clear_state() # Ensure we start fresh for each schedule call
        for fc in function_calls:
            tool_name = fc.get("name")
            tool = self._tool_registry.get_tool(tool_name)
            self._tool_calls.append({"request": fc, "status": "validating", "tool": tool})
        
        return await self._process_tool_calls()

    async def _process_tool_calls(self) -> Dict[str, Any]:
        """Processes the list of tool calls, handling validation and auto-execution."""
        execution_tasks = []
        for call in self._tool_calls:
            if call["status"] == "validating":
                tool = call["tool"]
                if not tool:
                    call["status"] = "error"
                    call["response"] = self._format_error_response(call["request"], "Tool not found.")
                    continue

                confirmation_details = await tool.should_confirm_execute(**call["request"].get("args", {}))
                if confirmation_details:
                    call["status"] = "awaiting_approval"
                    call["confirmation_details"] = confirmation_details
                else:
                    call["status"] = "executing"
            
            if call["status"] == "executing":
                execution_tasks.append(self._execute_call(call))

        if execution_tasks:
            await asyncio.gather(*execution_tasks)

        awaiting_approval_calls = [call for call in self._tool_calls if call["status"] == "awaiting_approval"]
        executed_results = [call["response"] for call in self._tool_calls if "response" in call]
        
        return {"awaiting_approval": awaiting_approval_calls, "executed_results": executed_results}

    async def handle_confirmation_response(self, call_request: Dict, outcome: ToolConfirmationOutcome):
        """Updates a tool call's state based on user confirmation and executes if approved."""
        for call in self._tool_calls:
            if call["request"] == call_request and call["status"] == "awaiting_approval":
                tool = call["tool"]
                if hasattr(tool, 'handle_confirmation_response'):
                    await tool.handle_confirmation_response(
                        call['confirmation_details'].get('root_command', ''),
                        outcome
                    )

                if outcome in [ToolConfirmationOutcome.PROCEED_ONCE, ToolConfirmationOutcome.PROCEED_ALWAYS]:
                    logging.info(f"User approved tool call: {call_request['name']}")
                    call["status"] = "executing"
                    # --- CRITICAL FIX ---
                    # Execute the tool immediately after its status is updated.
                    await self._execute_call(call)
                else:
                    logging.info(f"User cancelled tool call: {call_request['name']}")
                    call["status"] = "cancelled"
                    call["response"] = self._format_error_response(call_request, "Tool call was cancelled by the user.")
                return

    async def get_executed_results(self) -> List[Dict[str, Any]]:
        """Returns all results from tools that have been executed."""
        return [call["response"] for call in self._tool_calls if "response" in call and call["status"] != 'awaiting_approval']

    async def _execute_call(self, call: BaseToolCall):
        """Executes a single tool call, preventing re-execution."""
        if call.get("status") != "executing" or "response" in call:
            return

        tool = call["tool"]
        tool_name = call["request"]["name"]
        tool_args = call["request"].get("args", {})
        
        logging.info(f"Executing tool '{tool_name}' with args: {tool_args}")
        try:
            result = await tool.execute(**tool_args)
            call["status"] = "success"
            call["response"] = self._format_success_response(call["request"], result)
        except Exception as e:
            logging.error(f"Tool '{tool_name}' execution failed: {e}", exc_info=True)
            call["status"] = "error"
            call["response"] = self._format_error_response(call["request"], str(e))

    def _format_success_response(self, request: Dict, result: Dict[str, Any]) -> Dict[str, Any]:
        """Formats a successful tool execution response."""
        return {"functionResponse": {"name": request["name"], "response": {"content": result}}}

    def _format_error_response(self, request: Dict, error_message: str) -> Dict[str, Any]:
        """Formats a failed tool execution response."""
        return {"functionResponse": {"name": request["name"], "response": {"error": error_message}}}