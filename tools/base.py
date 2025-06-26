# tools/base.py

import abc
import json

class Tool(abc.ABC):
    """
    An abstract base class that defines the interface for all tools.
    This mirrors the concept of a tool from the TypeScript blueprint.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """The unique name of the tool."""
        pass

    @property
    @abc.abstractmethod
    def description(self) -> str:
        """A detailed description of what the tool does."""
        pass

    @property
    @abc.abstractmethod
    def schema(self) -> dict:
        """
        The JSON schema definition for the tool's parameters, matching the
        FunctionDeclaration format required by the Gemini API.
        """
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "OBJECT",
                "properties": {},
                "required": [],
            },
        }

    @abc.abstractmethod
    async def execute(self, **kwargs) -> dict:
        """
        Executes the tool with the given arguments.

        Returns:
            A dictionary representing the tool's output, which will be
            formatted into a `functionResponse` part for the model.
        """
        pass

    def to_dict(self):
        """Returns the FunctionDeclaration dictionary for the tool."""
        return self.schema

