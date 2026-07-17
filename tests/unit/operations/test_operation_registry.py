"""Unit tests for OperationRegistry."""

import pytest

from cks_runtime.execution.operation_executor import (
    ExecutionResult,
    Operation,
    OperationStatus,
)
from cks_runtime.operations.operation_registry import (
    OperationRegistry,
)


class DummyOperation(Operation):
    """
    Test Runtime Operation.
    """

    operation_id = "dummy"

    def execute(
        self,
        session,
        executor,
    ) -> ExecutionResult:

        return ExecutionResult(
            operation_id=self.operation_id,
            status=OperationStatus.COMPLETED,
        )


class AnotherOperation(Operation):

    operation_id = "another"

    def execute(
        self,
        session,
        executor,
    ) -> ExecutionResult:

        return ExecutionResult(
            operation_id=self.operation_id,
            status=OperationStatus.COMPLETED,
        )


@pytest.fixture
def registry():

    return OperationRegistry()


def test_register_and_retrieve(registry):

    registry.register(
        DummyOperation,
    )

    assert registry.has("dummy")

    assert registry.get("dummy") is DummyOperation


def test_register_duplicate_raises(registry):

    registry.register(
        DummyOperation,
    )

    with pytest.raises(
        ValueError,
        match="already registered",
    ):

        registry.register(
            DummyOperation,
        )


def test_register_many(registry):

    registry.register_many(
        [
            DummyOperation,
            AnotherOperation,
        ]
    )

    assert len(
        registry.list_all()
    ) == 2


def test_create_operation(registry):

    registry.register(
        DummyOperation,
    )

    operation = registry.create("dummy", "dummy-op-id")

    assert isinstance(
        operation,
        DummyOperation,
    )


def test_create_unknown_operation(registry):

    with pytest.raises(
        KeyError,
        match="Unknown operation",
    ):

        registry.create(
            "missing",
        )


def test_clear(registry):

    registry.register(
        DummyOperation,
    )

    registry.clear()

    assert len(
        registry
    ) == 0


def test_get_missing(registry):

    assert registry.get(
        "missing",
    ) is None


def test_has_missing(registry):

    assert registry.has(
        "missing",
    ) is False