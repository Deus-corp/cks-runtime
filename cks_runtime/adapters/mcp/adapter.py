"""
Runtime MCP Adapter.

Bridges the Model Context Protocol (MCP)
and the Runtime public API.

The adapter owns only transport translation.

Semantic behaviour belongs to Runtime and
CKS Core plugins.
"""

from __future__ import annotations

from typing import Any

from cks_runtime.runtime import Runtime

from .tool_registry import ToolRegistry


class MCPAdapter:
    """
    Runtime MCP adapter.

    Responsible only for translating
    MCP requests into Runtime API calls.
    """

    def __init__(
        self,
        runtime: Runtime,
    ) -> None:

        self._runtime = runtime

        self._tools = ToolRegistry()

    #
    # Public API
    #

    def list_tools(
        self,
    ) -> list[dict[str, Any]]:
        """
        Return MCP tool descriptions.
        """

        return self._tools.list()

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> Any:
        """
        Execute an MCP tool.
        """

        handler = self._tools.resolve(
            name,
        )

        return handler(
            runtime=self._runtime,
            arguments=arguments,
        )