#
# File: core_tool_scheduler.py
# Revision: 6
# Description: Finalized the response formatting to pass a dictionary
# directly in the 'content' field, preventing downstream parsing errors.
#

import asyncio
import json
import logging
from typing import List, Dict, Any, Literal, TypedDict, TYPE_CHECKING

from tool_registry import ToolRegistry
from tools.tool_io import ToolConfirmationOutcome

if TYPE_CHECKING:
    from turn import Turn

ToolCallStatus = Literal["validating", "awaiting_approval", "executing", "success", "error", "cancelled"]

class BaseToolCall(TypedDict, total=False):
    request: Dict[str, Any]
    status: ToolCallStatus
    tool: Any
    confirmation_details: Dict[str, Any]
    response: Dict[str, Any]

class CoreToolScheduler:
    """Manages the lifecycle of tool calls, including user confirmation."""
    def __init__(self, tool_registry: ToolRegistry):
        self._tool_registry = tool_registry
        self._tool_calls: List[BaseToolCall] = []
        logging.debug("CoreToolScheduler initialized.")

    def clear_state(self):
        self._tool_calls = []

    async def schedule(self, function_calls: List[Dict[str, Any]]) -> Dict[str, Any]:
        self.clear_state()
        for fc in function_calls:
            tool_name = fc.get("name")
            tool = self._tool_registry.get_tool(tool_name)
            self._tool_calls.append({"request": fc, "status": "validating", "tool": tool})
        return await self._process_tool_calls()

    async def _process_tool_calls(self) -> Dict[str, Any]:
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
        awaiting_approval_calls = [c for c in self._tool_calls if c["status"] == "awaiting_approval"]
        executed_results = [c["response"] for c in self._tool_calls if "response" in c]
        return {"awaiting_approval": awaiting_approval_calls, "executed_results": executed_results}

    async def handle_confirmation_and_execute(self, call_request: Dict, outcome: ToolConfirmationOutcome):
        for call in self._tool_calls:
            if call["request"] == call_request and call["status"] == "awaiting_approval":
                if outcome in [ToolConfirmationOutcome.PROCEED_ONCE, ToolConfirmationOutcome.PROCEED_ALWAYS]:
                    logging.debug(f"User approved tool call: {call_request['name']}")
                    call["status"] = "executing"
                    await self._execute_call(call)
                else:
                    logging.debug(f"User cancelled tool call: {call_request['name']}")
                    call["status"] = "cancelled"
                    call["response"] = self._format_error_response(call_request, "Tool call was cancelled by the user.")
                return

    async def get_executed_results(self) -> List[Dict[str, Any]]:
        return [c["response"] for c in self._tool_calls if "response" in c and c["status"] != 'awaiting_approval']

    async def _execute_call(self, call: BaseToolCall):
        if call.get("status") != "executing" or "response" in call:
            return
        tool, name, args = call["tool"], call["request"]["name"], call["request"].get("args", {})
        logging.debug(f"Executing tool '{name}' with args: {args}")
        try:
            result = await tool.execute(**args)
            call["status"], call["response"] = "success", self._format_success_response(call["request"], result)
        except Exception as e:
            call["status"], call["response"] = "error", self._format_error_response(call["request"], str(e))

    def _format_success_response(self, request: Dict, result: Dict) -> Dict:
        return {"functionResponse": {"name": request["name"], "response": {"content": result}}}

    def _format_error_response(self, request: Dict, error: str) -> Dict:
        return {"functionResponse": {"name": request["name"], "response": {"error": error}}}