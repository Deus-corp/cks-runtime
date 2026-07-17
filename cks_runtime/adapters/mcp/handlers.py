"""
MCP JSON-RPC Handler.

Provides stdio transport for the Runtime MCP adapter.

The handler owns only transport concerns:

- reading JSON-RPC messages;
- writing JSON-RPC responses;
- delegating requests to MCPAdapter.

It intentionally contains no Runtime logic.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from cks_runtime.runtime import Runtime

from cks_runtime.adapters.mcp.adapter import MCPAdapter
from cks_runtime.adapters.mcp.errors import (
    invalid_request,
    MCPError,
)


class MCPHandler:
    """
    JSON-RPC stdio handler.

    Owns transport only.

    Runtime orchestration belongs to MCPAdapter.
    """

    def __init__(
        self,
        runtime: Runtime,
    ) -> None:

        self._adapter = MCPAdapter(runtime)

    async def handle_request(
        self,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Handle a single JSON-RPC request.
        """

        return self._adapter.handle_request(request)

    async def _process_line(
        self,
        line: bytes,
    ) -> bytes | None:
        """
        Decode, execute and encode one request.
        """

        if not line.strip():
            return None

        try:
            request = json.loads(
                line.decode("utf-8"),
            )

        except json.JSONDecodeError as exc:
            error = invalid_request(str(exc))
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": error.as_dict(),
            }

            return (
                json.dumps(response).encode("utf-8")
                + b"\n"
            )

        response = await self.handle_request(
            request,
        )

        return (
            json.dumps(response).encode("utf-8")
            + b"\n"
        )

    async def run(self) -> None:
        """
        Run stdio event loop.
        """

        reader = asyncio.StreamReader()

        await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader),
            sys.stdin,
        )

        while True:

            line = await reader.readline()

            if not line:
                break

            payload = await self._process_line(
                line,
            )

            if payload is None:
                continue

            sys.stdout.buffer.write(payload)
            sys.stdout.buffer.flush()


def main() -> None:
    """
    CLI entry point.
    """

    from cks_runtime_plugins.cks_core import (
        CksCoreAdapter,
    )

    runtime = Runtime(
        core=CksCoreAdapter(),
    )

    asyncio.run(
        MCPHandler(runtime).run()
    )


if __name__ == "__main__":
    main()