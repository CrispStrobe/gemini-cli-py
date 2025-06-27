#
# File: core_tool_scheduler.py
# Revision: 3
# Description: Updates imports to use the new tool_io.py file.
#

import asyncio
import logging
from typing import List, Dict, Any, Literal, TypedDict

from tool_registry import ToolRegistry
from tools.tool_io import ToolConfirmationOutcome # <-- Import from new location

# ... (rest of the file is unchanged)
# --- Type Definitions for Tool Call States ---

ToolCallStatus = Literal["validating", "awaiting_approval", "executing", "success", "error", "cancelled"]

class BaseToolCall(TypedDict):
    request: Dict[str, Any]
    status: ToolCallStatus
    tool: Any # A Tool object

class AwaitingApprovalToolCall(BaseToolCall):
    status: Literal["awaiting_approval"]
    confirmation_details: Dict[str, Any]

class CoreToolScheduler:
    """
    Manages the lifecycle of tool calls, including a user confirmation step.
    """
    def __init__(self, tool_registry: ToolRegistry, turn: 'Turn'):
        self._tool_registry = tool_registry
        self._turn = turn
        self._tool_calls: List[BaseToolCall] = []
        logging.info("CoreToolScheduler initialized.")

    async def schedule(self, function_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Schedules tool calls. If confirmation is needed, it will yield control
        to the parent Turn to get user input.
        """
        self._tool_calls = []
        for fc in function_calls:
            tool_name = fc.get("name")
            tool = self._tool_registry.get_tool(tool_name)
            if not tool:
                logging.error(f"Tool '{tool_name}' not found in registry.")
                # We will handle this error case properly during execution phase
            self._tool_calls.append({"request": fc, "status": "validating", "tool": tool})

        await self._process_tool_calls()

        # Return the final results
        return [call["response"] for call in self._tool_calls if "response" in call]

    async def _process_tool_calls(self):
        """Processes the list of tool calls, handling confirmation and execution."""
        # 1. Validation and Confirmation Phase
        for call in self._tool_calls:
            if call["status"] == "validating":
                tool = call["tool"]
                if not tool:
                    call["status"] = "error"
                    call["response"] = self._format_error_response(call["request"]["name"], "Tool not found.")
                    continue

                confirmation_details = await tool.should_confirm_execute(**call["request"].get("args", {}))
                if confirmation_details:
                    call["status"] = "awaiting_approval"
                    call["confirmation_details"] = confirmation_details
                else:
                    call["status"] = "executing" # Auto-approved

        # 2. Handle all calls awaiting approval
        awaiting_approval_calls = [call for call in self._tool_calls if call["status"] == "awaiting_approval"]
        if awaiting_approval_calls:
            await self._turn.request_user_confirmations(awaiting_approval_calls)

        # 3. Execution Phase
        execution_tasks = []
        for call in self._tool_calls:
            if call["status"] == "executing":
                execution_tasks.append(self._execute_call(call))
        
        if execution_tasks:
            await asyncio.gather(*execution_tasks)

    async def handle_confirmation_response(self, call_request: Dict, outcome: ToolConfirmationOutcome):
        """Updates a tool call's state based on user confirmation."""
        for call in self._tool_calls:
            if call["request"] == call_request and call["status"] == "awaiting_approval":
                tool = call["tool"]
                if hasattr(tool, 'handle_confirmation_response'):
                    # Let the tool handle whitelisting, etc.
                    await tool.handle_confirmation_response(
                        call['confirmation_details']['root_command'],
                        outcome
                    )

                if outcome in [ToolConfirmationOutcome.PROCEED_ONCE, ToolConfirmationOutcome.PROCEED_ALWAYS]:
                    call["status"] = "executing"
                else:
                    call["status"] = "cancelled"
                    call["response"] = self._format_error_response(call["request"]["name"], "Tool call was cancelled by the user.")
                return

    async def _execute_call(self, call: BaseToolCall):
        """Executes a single, approved tool call."""
        tool = call["tool"]
        tool_name = call["request"]["name"]
        tool_args = call["request"].get("args", {})
        
        logging.info(f"Executing tool '{tool_name}' with args: {tool_args}")
        try:
            result = await tool.execute(**tool_args)
            call["status"] = "success"
            call["response"] = self._format_success_response(tool_name, result)
        except Exception as e:
            logging.error(f"Tool '{tool_name}' execution failed: {e}", exc_info=True)
            call["status"] = "error"
            call["response"] = self._format_error_response(tool_name, str(e))

    def _format_success_response(self, tool_name: str, result: Dict[str, Any]) -> Dict[str, Any]:
        return {"functionResponse": {"name": tool_name, "response": {"content": result}}}

    def _format_error_response(self, tool_name: str, error_message: str) -> Dict[str, Any]:
        return {"functionResponse": {"name": tool_name, "response": {"content": {"error": error_message}}}}