"""Basic tests for operation types existence."""

from cks_runtime.operations.operation_types import (
    ExplainOperation,
    EvolveOperation,
    SerializeOperation,
    ValidateOperation,
)

def test_validate_operation_exists():
    assert ValidateOperation is not None

def test_evolve_operation_exists():
    assert EvolveOperation is not None

def test_serialize_operation_exists():
    assert SerializeOperation is not None

def test_explain_operation_exists():
    assert ExplainOperation is not None