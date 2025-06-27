#
# File: tools/tool_io.py
# Revision: 1
# Description: Defines shared input/output data structures for tools,
# such as Enums, to prevent circular import issues.
#

from enum import Enum

class ToolConfirmationOutcome(Enum):
    """Enumerates the possible outcomes of a user confirmation prompt."""
    PROCEED_ONCE = "proceed_once"
    PROCEED_ALWAYS = "proceed_always"
    CANCEL = "cancel"