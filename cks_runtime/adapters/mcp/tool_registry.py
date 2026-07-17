"""
Runtime MCP Tool Registry.

Owns MCP tool registration.

The registry exposes tool descriptions and resolves
tool handlers.

The registry never performs transport or semantic work.
"""

from __future__ import annotations

import json
from typing import Any
from typing import Callable


RuntimeToolHandler = Callable[..., Any]


class ToolRegistry:
    """
    Registry of Runtime MCP tools.
    """

    def __init__(
        self,
    ) -> None:

        self._tools: dict[str, dict[str, Any]] = {}

        self._handlers: dict[
            str,
            RuntimeToolHandler,
        ] = {}

        self._register_builtin_tools()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list(
        self,
    ) -> list[dict[str, Any]]:
        """
        Return MCP tool descriptions.
        """

        return list(
            self._tools.values(),
        )

    def resolve(
        self,
        name: str,
    ) -> RuntimeToolHandler:
        """
        Resolve a Runtime MCP tool.
        """

        try:
            return self._handlers[name]

        except KeyError as exc:

            raise ValueError(
                f"Unknown MCP tool '{name}'."
            ) from exc

    def register(
        self,
        *,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: RuntimeToolHandler,
    ) -> None:
        """
        Register a new MCP tool.
        """

        if name in self._tools:

            raise ValueError(
                f"MCP tool '{name}' is already registered."
            )

        self._tools[name] = {
            "name": name,
            "description": description,
            "inputSchema": input_schema,
        }

        self._handlers[name] = handler

    # ------------------------------------------------------------------
    # Builtin tools
    # ------------------------------------------------------------------

    def _register_builtin_tools(
        self,
    ) -> None:

        self.register(
            name="validate_knowledge",
            description=(
                "Validate a Knowledge Structure."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "json_data": {
                        "type": "string",
                    },
                },
                "required": [
                    "json_data",
                ],
            },
            handler=self._validate_knowledge,
        )

        self.register(
            name="evolve_knowledge",
            description=(
                "Apply semantic evolution."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "json_data": {
                        "type": "string",
                    },
                    "operations": {
                        "type": "array",
                    },
                },
                "required": [
                    "json_data",
                    "operations",
                ],
            },
            handler=self._evolve_knowledge,
        )

        self.register(
            name="serialize_knowledge",
            description=(
                "Serialize a Knowledge Structure."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "json_data": {
                        "type": "string",
                    },
                },
                "required": [
                    "json_data",
                ],
            },
            handler=self._serialize_knowledge,
        )

        self.register(
            name="explain_knowledge",
            description=(
                "Explain a Knowledge Structure."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "json_data": {
                        "type": "string",
                    },
                },
                "required": [
                    "json_data",
                ],
            },
            handler=self._explain_knowledge,
        )

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_knowledge(
        *,
        runtime,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:

        structure = json.loads(
            arguments["json_data"],
        )

        result = runtime.core_bridge.validate(
            structure,
        )

        return {
            "valid": result.valid,
            "diagnostics": list(
                result.diagnostics,
            ),
            "metadata": dict(
                result.metadata,
            ),
        }

    @staticmethod
    def _evolve_knowledge(
        *,
        runtime,
        arguments: dict[str, Any],
    ) -> Any:

        structure = json.loads(
            arguments["json_data"],
        )

        operation = arguments["operations"]

        return runtime.core_bridge.evolve(
            structure,
            operation,
        )

    @staticmethod
    def _serialize_knowledge(
        *,
        runtime,
        arguments: dict[str, Any],
    ) -> str:

        structure = json.loads(
            arguments["json_data"],
        )

        return runtime.core_bridge.serialize(
            structure,
        )

    @staticmethod
    def _explain_knowledge(
        *,
        runtime,
        arguments: dict[str, Any],
    ) -> Any:

        structure = json.loads(
            arguments["json_data"],
        )

        return runtime.core_bridge.explain(
            structure,
        )