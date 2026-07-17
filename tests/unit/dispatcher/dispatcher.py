"""Unit tests for Runtime Dispatcher."""

from __future__ import annotations

import pytest

from cks_runtime.dispatcher.dispatcher import (
    DispatchRequest,
    Dispatcher,
)

from cks_runtime.execution.execution_context import (
    ExecutionContext,
)

from cks_runtime.execution.operation_executor import (
    OperationExecutor,
    ExecutionResult,
    Operation,
    OperationStatus,
)

from cks_runtime.operations.operation_registry import (
    OperationRegistry,
)

from cks_runtime.session.session import (
    RuntimeSession,
)

from cks_runtime.diagnostics.diagnostic import (
    DiagnosticSeverity,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeCoreAdapter:
    """Minimal Core Adapter stub."""

    pass


class _SuccessfulOperation(Operation):
    """Operation that always succeeds."""

    def execute(
        self,
        session,
        executor,
    ) -> ExecutionResult:

        return ExecutionResult(
            operation_id=self.operation_id,
            status=OperationStatus.COMPLETED,
            payload={
                "result": "ok",
            },
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def registry():

    registry = OperationRegistry()

    registry.register(
        _SuccessfulOperation(
            "validate",
        ),
    )

    return registry


@pytest.fixture
def executor():

    return OperationExecutor(
        core_adapter=_FakeCoreAdapter(),
    )


@pytest.fixture
def dispatcher(
    registry,
    executor,
):

    return Dispatcher(
        registry,
        executor,
    )


@pytest.fixture
def context(
    executor,
):

    session = RuntimeSession(
        knowledge_structure={},
    )

    return ExecutionContext(
        session=session,
        executor=executor,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_dispatcher_creation(
    dispatcher,
    registry,
    executor,
):

    assert dispatcher.registry is registry

    assert dispatcher.executor is executor


def test_dispatch_registered_operation(
    dispatcher,
    context,
):

    request = DispatchRequest(
        operation_id="validate",
    )

    result = dispatcher.dispatch(
        request,
        context,
    )

    assert result.succeeded

    assert not result.failed

    assert (
        result.status
        is OperationStatus.COMPLETED
    )

    assert result.payload == {
        "result": "ok",
    }

    assert result.error is None


def test_dispatch_unknown_operation(
    dispatcher,
    context,
):

    request = DispatchRequest(
        operation_id="unknown",
    )

    result = dispatcher.dispatch(
        request,
        context,
    )

    assert result.failed

    assert not result.succeeded

    assert (
        result.status
        is OperationStatus.FAILED
    )

    assert isinstance(
        result.error,
        LookupError,
    )

    assert len(
        result.diagnostics,
    ) == 1

    diagnostic = result.diagnostics[0]

    assert (
        diagnostic.severity
        is DiagnosticSeverity.ERROR
    )

    assert "unknown" in diagnostic.message


def test_dispatch_request_is_immutable():

    request = DispatchRequest(
        operation_id="validate",
    )

    with pytest.raises(
        Exception,
    ):
        request.operation_id = "other"


def test_dispatcher_uses_registry(
    dispatcher,
):

    assert dispatcher.registry.has(
        "validate",
    )

    assert not dispatcher.registry.has(
        "missing",
    )