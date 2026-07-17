"""Unit tests for RuntimeValidationResult."""

from __future__ import annotations

import pytest

from cks_runtime.core_api.validation_result import (
    RuntimeValidationResult,
)


def test_success_factory():
    result = RuntimeValidationResult.success()
    assert result.valid is True
    assert result.has_diagnostics is False
    assert result.diagnostic_count == 0


def test_failure_factory():
    result = RuntimeValidationResult.failure(("error-1", "error-2"))
    assert result.valid is False
    assert result.has_diagnostics is True
    assert result.diagnostic_count == 2
    assert result.diagnostics == ("error-1", "error-2")


def test_default_metadata_is_empty():
    result = RuntimeValidationResult.success()
    assert dict(result.metadata) == {}


def test_validation_result_is_frozen():
    result = RuntimeValidationResult.success()
    with pytest.raises(Exception):
        result.valid = False


def test_metadata_is_mutable_dict():
    """metadata is now a plain dict and can be mutated (but should not be)."""
    result = RuntimeValidationResult.success()
    # It is a dict, not a mappingproxy
    assert isinstance(result.metadata, dict)
    # Mutation is technically possible
    result.metadata["x"] = 1
    assert result.metadata["x"] == 1


def test_has_diagnostics_property():
    result = RuntimeValidationResult(valid=True, diagnostics=("warning",))
    assert result.has_diagnostics is True


def test_diagnostic_count_property():
    result = RuntimeValidationResult(valid=False, diagnostics=("a", "b", "c"))
    assert result.diagnostic_count == 3