"""
MCP Serialization Utilities.

Provides conversion helpers between Runtime objects
and JSON-compatible MCP payloads.

This module intentionally contains no transport logic,
no Runtime orchestration and no business rules.

It is purely responsible for serialization.
"""

from __future__ import annotations

from typing import Any

from cks_runtime.execution.operation_executor import (
    ExecutionResult,
)
from cks_runtime.events.runtime_event import (
    RuntimeEvent,
)


class MCPSerializer:
    """
    Runtime ↔ MCP serializer.

    Produces JSON-compatible structures suitable
    for JSON-RPC responses.
    """

    #
    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    #

    @staticmethod
    def execution_result(result):
        return {
            "operation_id": result.operation_id,
            "status": result.status.value,
            "data": result.payload,          # было result.data
            "error": result.error,
            "diagnostics": [MCPSerializer.diagnostic(d) for d in result.diagnostics],
        }

    #
    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------
    #

    @staticmethod
    def diagnostic(
        diagnostic: Any,
    ) -> dict[str, Any]:
        """
        Serialize a Runtime diagnostic.
        """

        return {
            "message": diagnostic.message,
            "severity": diagnostic.severity.value,
            "source": diagnostic.source.value,
            "metadata": dict(diagnostic.metadata),
        }

    #
    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    #

    @staticmethod
    def event(event: RuntimeEvent) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": event.__class__.__name__,
            "timestamp": event.created_at.isoformat(),
            "metadata": dict(event.metadata),
        }
        # copy remaining public dataclass fields (compatible with slots)
        for field_name in event.__dataclass_fields__:
            if field_name in ("event_id", "created_at", "metadata"):
                continue
            payload[field_name] = getattr(event, field_name)
        return payload

    #
    # ------------------------------------------------------------------
    # Tool discovery
    # ------------------------------------------------------------------
    #

    @staticmethod
    def tool(
        tool: Any,
    ) -> dict[str, Any]:
        """
        Serialize an MCP tool description.
        """

        return {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.input_schema,
        }

    #
    # ------------------------------------------------------------------
    # Generic helpers
    # ------------------------------------------------------------------
    #

    @staticmethod
    def tools(
        tools: tuple[Any, ...],
    ) -> list[dict[str, Any]]:
        """
        Serialize multiple tools.
        """

        return [
            MCPSerializer.tool(tool)
            for tool in tools
        ]

    @staticmethod
    def events(
        events: tuple[RuntimeEvent, ...],
    ) -> list[dict[str, Any]]:
        """
        Serialize multiple Runtime events.
        """

        return [
            MCPSerializer.event(event)
            for event in events
        ]