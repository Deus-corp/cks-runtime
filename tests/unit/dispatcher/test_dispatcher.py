"""
Tests for Dispatcher.
"""

import pytest

from cks_runtime.dispatcher.dispatcher import Dispatcher, DispatchRequest
from cks_runtime.execution.execution_context import ExecutionContext
from cks_runtime.execution.operation_executor import (
    ExecutionResult,
    Operation,
    OperationExecutor,
    OperationStatus,
)
from cks_runtime.operations.operation_registry import OperationRegistry
from cks_runtime.session.session import RuntimeSession

pytestmark = pytest.mark.asyncio


class _SuccessfulOperation(Operation):
    operation_id: str = "test_op"

    def __init__(self, operation_id: str = "test_op", **kwargs) -> None:
        super().__init__(operation_id)

    async def execute(self, session, executor) -> ExecutionResult:
        return ExecutionResult(
            operation_id=self.operation_id,
            status=OperationStatus.COMPLETED,
        )


@pytest.fixture
def registry():
    reg = OperationRegistry()
    reg.register(_SuccessfulOperation)
    return reg


@pytest.fixture
def executor():
    from cks_runtime.core_api.bridge import CoreBridge
    return OperationExecutor(core_adapter=CoreBridge())


@pytest.fixture
def session():
    return RuntimeSession(knowledge_structure={})


async def test_dispatch_creates_and_executes_operation(registry, executor, session):
    dispatcher = Dispatcher(registry=registry, executor=executor)
    context = ExecutionContext(session=session, executor=executor)

    request = DispatchRequest(operation_id="test_op", parameters={})
    result = await dispatcher.dispatch(request, context)

    assert result.status == OperationStatus.COMPLETED
    assert result.operation_id == "test_op"


async def test_dispatch_unknown_operation_returns_error(registry, executor, session):
    dispatcher = Dispatcher(registry=registry, executor=executor)
    context = ExecutionContext(session=session, executor=executor)

    request = DispatchRequest(operation_id="nonexistent", parameters={})
    result = await dispatcher.dispatch(request, context)

    assert result.status == OperationStatus.FAILED
    assert isinstance(result.error, LookupError)


class _StrictOperation(Operation):
    """
    Unlike _SuccessfulOperation above, __init__ does not swallow
    arbitrary kwargs -- this is what most real Operation subclasses in
    cks_runtime.operations.operation_types look like, and is needed to
    exercise what happens when request.parameters doesn't match.
    """

    operation_id: str = "strict_op"

    def __init__(self, operation_id: str = "strict_op", *, required_field: str) -> None:
        super().__init__(operation_id)
        self.required_field = required_field

    async def execute(self, session, executor) -> ExecutionResult:
        return ExecutionResult(
            operation_id=self.operation_id,
            status=OperationStatus.COMPLETED,
        )


async def test_dispatch_malformed_parameters_returns_error_instead_of_raising(
    executor, session
):
    """
    request.parameters typically comes from outside the process (an
    API/MCP request body) -- a misspelled or missing key raises
    TypeError from the Operation's __init__. This used to propagate
    straight out of dispatch() uncaught, unlike the unknown-operation
    case just above, which means ExecutionPipeline's rollback (see
    _handle_result) never ran. Must come back as a graceful FAILED
    result instead, the same as any other dispatch failure.
    """
    registry = OperationRegistry()
    registry.register(_StrictOperation)
    dispatcher = Dispatcher(registry=registry, executor=executor)
    context = ExecutionContext(session=session, executor=executor)

    # Typo'd keyword, and the real required_field is missing entirely.
    request = DispatchRequest(operation_id="strict_op", parameters={"wrong_field": "x"})
    result = await dispatcher.dispatch(request, context)

    assert result.status == OperationStatus.FAILED
    assert isinstance(result.error, TypeError)
    assert result.diagnostics
    assert "strict_op" in result.diagnostics[0].message
