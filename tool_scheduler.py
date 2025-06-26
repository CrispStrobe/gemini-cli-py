#
# File: tool_scheduler.py
# Revision: 1
# Description: Handles the execution of a tool call requested by the model
# and formats the result for the API.
#

import json
import logging
from tool_registry import ToolRegistry

class ToolScheduler:
    """
    Handles the execution of a tool call requested by the model.
    This is a simplified version of the CoreToolScheduler from the blueprint.
    It does not yet handle user confirmation, which will be added later.
    """
    def __init__(self, tool_registry: ToolRegistry):
        self._tool_registry = tool_registry
        logging.info("ToolScheduler initialized.")

    async def dispatch_tool_call(self, function_call: dict) -> dict:
        """
        Executes a tool based on the model's function call request and
        formats the result into a functionResponse part.

        Args:
            function_call: The functionCall object from the Gemini API response.

        Returns:
            A dictionary formatted as a `functionResponse` part for the API.
        """
        tool_name = function_call.get("name")
        tool_args = function_call.get("args", {})

        if not tool_name:
            logging.error("Received a function call with no name.")
            return self._format_error_response(tool_name, "Function call is missing a name.")

        logging.info(f"Dispatching tool call for '{tool_name}' with args: {tool_args}")

        tool = self._tool_registry.get_tool(tool_name)
        if not tool:
            logging.error(f"Tool '{tool_name}' not found in registry.")
            return self._format_error_response(tool_name, f"Tool '{tool_name}' is not available.")

        try:
            # Execute the tool's method with the provided arguments
            execution_result = await tool.execute(**tool_args)

            # Format the successful result for the API
            return {
                "functionResponse": {
                    "name": tool_name,
                    "response": {
                        # The API expects the result to be a JSON object,
                        # so we package the tool's dictionary output.
                        "content": execution_result
                    }
                }
            }
        except Exception as e:
            logging.error(f"An exception occurred while executing tool '{tool_name}': {e}", exc_info=True)
            return self._format_error_response(tool_name, f"Execution failed with error: {str(e)}")

    def _format_error_response(self, tool_name: str, error_message: str) -> dict:
        """Creates a standardized error response for the model."""
        return {
            "functionResponse": {
                "name": tool_name,
                "response": {
                    "content": {"error": error_message}
                }
            }
        }
