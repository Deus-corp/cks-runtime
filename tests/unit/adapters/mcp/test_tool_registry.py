"""Unit tests for ToolRegistry."""

from __future__ import annotations

import pytest
from cks_runtime.adapters.mcp.tool_registry import ToolRegistry


def test_registry_contains_builtins():
    registry = ToolRegistry()
    tools = registry.list()
    assert len(tools) >= 4


def test_can_resolve_builtin_tool():
    registry = ToolRegistry()
    handler = registry.resolve("validate_knowledge")
    assert handler is not None


def test_register_new_tool():
    registry = ToolRegistry()
    def dummy_handler(runtime, arguments):
        return {"ok": True}
    registry.register(
        name="custom_tool",
        description="A custom tool",
        input_schema={"type": "object"},
        handler=dummy_handler,
    )
    tools = registry.list()
    assert any(t["name"] == "custom_tool" for t in tools)
    handler = registry.resolve("custom_tool")
    assert handler is dummy_handler


def test_register_duplicate_raises():
    registry = ToolRegistry()
    def handler1(runtime, arguments):
        pass
    registry.register(name="dup", description="desc", input_schema={}, handler=handler1)
    with pytest.raises(ValueError):
        registry.register(name="dup", description="desc", input_schema={}, handler=handler1)


def test_resolve_unknown_raises():
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="Unknown MCP tool 'nonexistent'"):
        registry.resolve("nonexistent")