"""
MCP Error Model.

Defines Runtime-side JSON-RPC / MCP errors.

This module contains no transport logic.

It only defines the canonical error objects used by
the MCP Adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any


class MCPErrorCode(IntEnum):
    """
    Standard JSON-RPC / MCP error codes.
    """

    #
    # JSON parsing
    #

    PARSE_ERROR = -32700

    #
    # Request validation
    #

    INVALID_REQUEST = -32600

    #
    # Method dispatch
    #

    METHOD_NOT_FOUND = -32601

    #
    # Parameter validation
    #

    INVALID_PARAMS = -32602

    #
    # Internal execution
    #

    INTERNAL_ERROR = -32603

    #
    # Runtime-specific server errors.
    #

    SERVER_ERROR = -32000

    RESOURCE_NOT_FOUND = -32002

    REQUEST_TIMEOUT = -32001


@dataclass(frozen=True, slots=True)
class MCPError:
    """
    Runtime representation of a JSON-RPC error.
    """

    code: MCPErrorCode

    message: str

    data: Any | None = None

    def as_dict(self) -> dict[str, Any]:
        """
        Serialize into JSON-RPC error object.
        """

        payload = {
            "code": int(self.code),
            "message": self.message,
        }

        if self.data is not None:
            payload["data"] = self.data

        return payload


#
# ------------------------------------------------------------------
# Convenience constructors
# ------------------------------------------------------------------
#

def parse_error() -> MCPError:

    return MCPError(
        MCPErrorCode.PARSE_ERROR,
        "Parse error",
    )


def invalid_request(
    message: str = "Invalid request",
) -> MCPError:

    return MCPError(
        MCPErrorCode.INVALID_REQUEST,
        message,
    )


def method_not_found(
    method: str,
) -> MCPError:

    return MCPError(
        MCPErrorCode.METHOD_NOT_FOUND,
        f"Method '{method}' not found.",
    )


def invalid_params(
    message: str,
) -> MCPError:

    return MCPError(
        MCPErrorCode.INVALID_PARAMS,
        message,
    )


def internal_error(
    exc: Exception | str,
) -> MCPError:

    return MCPError(
        MCPErrorCode.INTERNAL_ERROR,
        str(exc),
    )


def resource_not_found(
    resource: str,
) -> MCPError:

    return MCPError(
        MCPErrorCode.RESOURCE_NOT_FOUND,
        f"Resource '{resource}' not found.",
    )


def timeout(
    operation: str,
) -> MCPError:

    return MCPError(
        MCPErrorCode.REQUEST_TIMEOUT,
        f"Operation '{operation}' timed out.",
    )