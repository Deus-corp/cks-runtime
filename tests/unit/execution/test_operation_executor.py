"""Unit tests for OperationExecutor."""

import pytest

from cks_runtime.core_api.validation_result import (
    RuntimeValidationResult,
)

from cks_runtime.execution.operation_executor import (
    ExecutionResult,
    Operation,
    OperationExecutor,
    OperationStatus,
)

from cks_runtime.session.session import (
    RuntimeSession,
)

from cks_runtime.diagnostics.diagnostic import (
    Diagnostic,
    DiagnosticSeverity,
    DiagnosticSource,
)


class _FakeCoreAdapter:
    """Minimal Core Adapter stub."""

    def validate(
        self,
        knowledge_structure,
    ):
        return RuntimeValidationResult(
            valid=True,
        )


class _SuccessfulOperation(Operation):
    """Operation that always succeeds."""

    def execute(
        self,
        session,
        executor,
    ):

        return ExecutionResult(
            operation_id=self.operation_id,
            status=OperationStatus.COMPLETED,
            payload={
                "message": "done",
            },
        )


class _DiagnosticOperation(Operation):
    """Operation that succeeds while emitting diagnostics."""

    def execute(
        self,
        session,
        executor,
    ):

        diagnostic = Diagnostic(
            message="operation warning",
            source=DiagnosticSource.RUNTIME,
            severity=DiagnosticSeverity.WARNING,
        )

        return ExecutionResult(
            operation_id=self.operation_id,
            status=OperationStatus.COMPLETED,
            diagnostics=(
                diagnostic,
            ),
        )


class _FailingOperation(Operation):
    """Operation that raises an exception."""

    def execute(
        self,
        session,
        executor,
    ):

        raise RuntimeError(
            "simulated failure",
        )


@pytest.fixture
def executor():

    return OperationExecutor(
        core_adapter=_FakeCoreAdapter(),
    )


@pytest.fixture
def session():

    return RuntimeSession(
        knowledge_structure={},
    )


def test_executor_exposes_core_adapter(
    executor,
):

    assert executor.core is not None


def test_execute_success(
    executor,
    session,
):

    operation = _SuccessfulOperation(
        "op-1",
    )

    result = executor.execute(
        operation,
        session,
    )

    assert result.succeeded

    assert not result.failed

    assert (
        result.status
        is OperationStatus.COMPLETED
    )

    assert result.payload == {
        "message": "done",
    }

    assert result.error is None

    assert result.diagnostics == ()

    assert len(
        session.diagnostics,
    ) == 0


def test_execute_failure(
    executor,
    session,
):

    operation = _FailingOperation(
        "op-2",
    )

    result = executor.execute(
        operation,
        session,
    )

    assert result.failed

    assert not result.succeeded

    assert (
        result.status
        is OperationStatus.FAILED
    )

    assert isinstance(
        result.error,
        RuntimeError,
    )

    assert "simulated failure" in str(
        result.error,
    )

    assert len(
        result.diagnostics,
    ) == 1

    diagnostic = result.diagnostics[0]

    assert (
        diagnostic.severity
        is DiagnosticSeverity.ERROR
    )

    assert diagnostic.message == "simulated failure"


def test_executor_appends_diagnostics_to_session(
    executor,
    session,
):

    operation = _DiagnosticOperation(
        "op-3",
    )

    before = len(
        session.diagnostics,
    )

    result = executor.execute(
        operation,
        session,
    )

    after = len(
        session.diagnostics,
    )

    assert result.succeeded

    assert after == before + 1

    assert (
        session.diagnostics[-1].message
        == "operation warning"
    )


def test_success_without_diagnostics_does_not_modify_session(
    executor,
    session,
):

    operation = _SuccessfulOperation(
        "op-4",
    )

    before = len(
        session.diagnostics,
    )

    executor.execute(
        operation,
        session,
    )

    after = len(
        session.diagnostics,
    )

    assert after == before