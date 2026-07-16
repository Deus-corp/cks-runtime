"""
CKS Runtime — MCP Adapter.

This package contains the MCP (Model Context Protocol) adapter for
CKS Runtime.  The adapter translates JSON‑RPC requests from MCP
clients into Runtime API calls, providing a transport‑independent
bridge between the MCP protocol and the CKS operational layer.

Public API:
    MCPAdapter   — translates MCP requests into Runtime operations.
    MCPHandler   — JSON‑RPC handler for stdio transport.
"""

from .adapter import MCPAdapter
from .handlers import MCPHandler

__all__ = [
    "MCPAdapter",
    "MCPHandler",
]