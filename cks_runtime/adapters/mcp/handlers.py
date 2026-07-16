"""
MCP Handlers.

JSON‑RPC request handler for the CKS MCP Adapter.
Handles the transport layer for MCP requests over stdio.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Dict, Optional

from cks_runtime.runtime import Runtime
from cks_runtime.adapters.mcp.adapter import MCPAdapter


class MCPHandler:
    """Handles JSON‑RPC requests for MCP tools."""

    def __init__(self, runtime: Runtime) -> None:
        self._adapter = MCPAdapter(runtime)

    async def handle_request(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process a single JSON‑RPC request and return a response."""
        return self._adapter.handle_request(request)

    async def run(self) -> None:
        """Read JSON‑RPC requests from stdin and write responses to stdout."""
        reader = asyncio.StreamReader()
        await asyncio.get_event_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader),
            sys.stdin,
        )

        writer = asyncio.StreamWriter(sys.stdout.buffer, None, None, None)

        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                request = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError:
                continue

            response = await self.handle_request(request)
            if response is not None:
                writer.write(json.dumps(response).encode("utf-8") + b"\n")
                await writer.drain()


def main() -> None:
    """Entry point for the MCP handler."""
    from cks_runtime_core import CksCoreAdapter
    runtime = Runtime(core=CksCoreAdapter())
    handler = MCPHandler(runtime)
    asyncio.run(handler.run())


if __name__ == "__main__":
    main()