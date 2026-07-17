"""
MCP Contracts.

Canonical Runtime representation of JSON-RPC / MCP messages.

The Runtime never manipulates raw JSON dictionaries directly.

Transport layers convert JSON <-> contract objects.

The Adapter operates exclusively on these contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


#
# ----------------------------------------------------------------------
# Base
# ----------------------------------------------------------------------
#


JSONRPC_VERSION = "2.0"


RequestId = str | int


#
# ----------------------------------------------------------------------
# Requests
# ----------------------------------------------------------------------
#


@dataclass(frozen=True, slots=True)
class MCPRequest:
    """
    JSON-RPC request.

    Represents a tool invocation coming from an MCP client.
    """

    id: RequestId

    method: str

    params: dict[str, Any] = field(
        default_factory=dict,
    )

    jsonrpc: str = JSONRPC_VERSION


#
# ----------------------------------------------------------------------
# Notifications
# ----------------------------------------------------------------------
#


@dataclass(frozen=True, slots=True)
class MCPNotification:
    """
    JSON-RPC notification.

    Notifications never expect a response.
    """

    method: str

    params: dict[str, Any] = field(
        default_factory=dict,
    )

    jsonrpc: str = JSONRPC_VERSION


#
# ----------------------------------------------------------------------
# Responses
# ----------------------------------------------------------------------
#


@dataclass(frozen=True, slots=True)
class MCPResponse:
    """
    Successful JSON-RPC response.
    """

    id: RequestId

    result: dict[str, Any] = field(
        default_factory=dict,
    )

    jsonrpc: str = JSONRPC_VERSION


#
# ----------------------------------------------------------------------
# Error Responses
# ----------------------------------------------------------------------
#


@dataclass(frozen=True, slots=True)
class MCPErrorResponse:
    """
    JSON-RPC error response.
    """

    id: RequestId | None

    error: dict[str, Any]

    jsonrpc: str = JSONRPC_VERSION


#
# ----------------------------------------------------------------------
# Tool Contracts
# ----------------------------------------------------------------------
#


@dataclass(frozen=True, slots=True)
class MCPToolCall:
    """
    Runtime representation of a tool invocation.
    """

    tool: str

    arguments: dict[str, Any] = field(
        default_factory=dict,
    )


@dataclass(frozen=True, slots=True)
class MCPToolResult:
    """
    Result produced by a Runtime tool.
    """

    content: Any

    is_error: bool = False


#
# ----------------------------------------------------------------------
# Lifecycle
# ----------------------------------------------------------------------
#


@dataclass(frozen=True, slots=True)
class MCPInitializeRequest:
    """
    MCP initialize request.
    """

    protocol_version: str

    client_info: dict[str, Any]

    capabilities: dict[str, Any] = field(
        default_factory=dict,
    )


@dataclass(frozen=True, slots=True)
class MCPInitializeResponse:
    """
    MCP initialize response.
    """

    protocol_version: str

    server_info: dict[str, Any]

    capabilities: dict[str, Any] = field(
        default_factory=dict,
    )


#
# ----------------------------------------------------------------------
# Capabilities
# ----------------------------------------------------------------------
#


@dataclass(frozen=True, slots=True)
class MCPServerCapabilities:
    """
    Runtime capabilities exposed through MCP.
    """

    tools: bool = True

    resources: bool = False

    prompts: bool = False

    logging: bool = False

    completion: bool = False

    sampling: bool = False

    roots: bool = False

    experimental: dict[str, Any] = field(
        default_factory=dict,
    )

    def as_dict(self) -> dict[str, Any]:
        """
        Convert capabilities into MCP payload.
        """

        payload: dict[str, Any] = {}

        if self.tools:
            payload["tools"] = {}

        if self.resources:
            payload["resources"] = {}

        if self.prompts:
            payload["prompts"] = {}

        if self.logging:
            payload["logging"] = {}

        if self.completion:
            payload["completion"] = {}

        if self.sampling:
            payload["sampling"] = {}

        if self.roots:
            payload["roots"] = {}

        if self.experimental:
            payload["experimental"] = dict(
                self.experimental,
            )

        return payload