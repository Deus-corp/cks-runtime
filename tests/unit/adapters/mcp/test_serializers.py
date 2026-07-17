"""Unit tests for MCPSerializer."""

from __future__ import annotations

from dataclasses import dataclass

from cks_runtime.adapters.mcp.serializers import (
    MCPSerializer,
)
from cks_runtime.diagnostics.diagnostic import (
    Diagnostic,
    DiagnosticSeverity,
    DiagnosticSource,
)
from cks_runtime.events.runtime_event import (
    SessionCreated,
)
from cks_runtime.execution.operation_executor import (
    ExecutionResult,
    OperationStatus,
)


@dataclass
class FakeTool:
    name: str
    description: str
    input_schema: dict | None = None


def create_diagnostic() -> Diagnostic:
    return Diagnostic(
        message="Example diagnostic",
        severity=DiagnosticSeverity.WARNING,
        source=DiagnosticSource.RUNTIME,
        metadata={"example": True},
    )


def test_serialize_diagnostic():
    diagnostic = create_diagnostic()
    payload = MCPSerializer.diagnostic(diagnostic)
    assert payload == {
        "message": "Example diagnostic",
        "severity": "warning",
        "source": "runtime",
        "metadata": {"example": True},
    }


def test_serialize_execution_result():
    diagnostic = create_diagnostic()
    result = ExecutionResult(
        operation_id="runtime.validate",
        status=OperationStatus.COMPLETED,
        diagnostics=(diagnostic,),
        payload={"valid": True},
    )
    payload = MCPSerializer.execution_result(result)
    assert payload["operation_id"] == "runtime.validate"
    assert payload["status"] == "completed"
    assert payload["data"] == {"valid": True}       # сериализатор отдаёт payload как data
    assert payload["error"] is None
    assert len(payload["diagnostics"]) == 1
    assert payload["diagnostics"][0]["message"] == "Example diagnostic"


def test_serialize_tool():
    tool = FakeTool(
        name="validate",
        description="Validate Knowledge Structure",
        input_schema={"type": "object"},
    )
    payload = MCPSerializer.tool(tool)
    assert payload == {
        "name": "validate",
        "description": "Validate Knowledge Structure",
        "inputSchema": {"type": "object"},
    }


def test_serialize_tools():
    tools = (
        FakeTool(name="validate", description="Validate"),
        FakeTool(name="evolve", description="Evolve"),
    )
    payload = MCPSerializer.tools(tools)
    assert len(payload) == 2
    assert payload[0]["name"] == "validate"
    assert payload[1]["name"] == "evolve"


def test_serialize_event():
    event = SessionCreated(session_id="session-001")
    payload = MCPSerializer.event(event)
    assert payload["type"] == "SessionCreated"
    assert payload["session_id"] == "session-001"
    assert isinstance(payload["timestamp"], str)
    assert payload["metadata"] == {}


def test_serialize_multiple_events():
    events = (
        SessionCreated(session_id="s1"),
        SessionCreated(session_id="s2"),
    )
    payload = MCPSerializer.events(events)
    assert len(payload) == 2
    assert payload[0]["session_id"] == "s1"
    assert payload[1]["session_id"] == "s2"


def test_serializer_preserves_metadata():
    diagnostic = Diagnostic(
        message="Metadata test",
        severity=DiagnosticSeverity.INFO,
        source=DiagnosticSource.RUNTIME,
        metadata={"a": 1, "b": {"nested": True}},
    )
    payload = MCPSerializer.diagnostic(diagnostic)
    assert payload["metadata"]["a"] == 1
    assert payload["metadata"]["b"]["nested"] is True


def test_serializer_handles_empty_execution_result():
    result = ExecutionResult(
        operation_id="runtime.empty",
        status=OperationStatus.COMPLETED,
    )
    payload = MCPSerializer.execution_result(result)
    assert payload["operation_id"] == "runtime.empty"
    assert payload["status"] == "completed"
    assert payload["diagnostics"] == []
    assert payload["data"] is None
    assert payload["error"] is None