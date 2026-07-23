"""
Tests for Dispatcher.
"""

import pytest
from cks_runtime.dispatcher.dispatcher import Dispatcher, DispatchRequest
from cks_runtime.execution.execution_context import ExecutionContext
from cks_runtime.execution.operation_executor import (
    OperationExecutor,
    Operation,
    ExecutionResult,
    OperationStatus,
)
from cks_runtime.operations.operation_registry import OperationRegistry
from cks_runtime.session.session import RuntimeSession


class _SuccessfulOperation(Operation):
    operation_id: str = "test_op"

    def __init__(self, operation_id: str = "test_op", **kwargs) -> None:
        super().__init__(operation_id)

    def execute(self, session, executor) -> ExecutionResult:
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


def test_dispatch_creates_and_executes_operation(registry, executor, session):
    dispatcher = Dispatcher(registry=registry, executor=executor)
    context = ExecutionContext(session=session, executor=executor)

    request = DispatchRequest(operation_id="test_op", parameters={})
    result = dispatcher.dispatch(request, context)

    assert result.status == OperationStatus.COMPLETED
    assert result.operation_id == "test_op"


def test_dispatch_unknown_operation_returns_error(registry, executor, session):
    dispatcher = Dispatcher(registry=registry, executor=executor)
    context = ExecutionContext(session=session, executor=executor)

    request = DispatchRequest(operation_id="nonexistent", parameters={})
    result = dispatcher.dispatch(request, context)

    assert result.status == OperationStatus.FAILED
    assert isinstance(result.error, LookupError)