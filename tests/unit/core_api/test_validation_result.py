"""Tests for RuntimeValidationResult."""

import pytest

from cks_runtime.core_api.validation_result import RuntimeValidationResult


def test_default_validation_result():
    result = RuntimeValidationResult(valid=True)
    assert result.valid is True
    assert not result.has_diagnostics
    assert result.diagnostic_count == 0


def test_validation_result_with_diagnostics():
    result = RuntimeValidationResult(valid=False, diagnostics=("error1", "error2"))
    assert result.valid is False
    assert result.has_diagnostics
    assert result.diagnostic_count == 2


def test_validation_result_is_immutable():
    result = RuntimeValidationResult(valid=True)
    with pytest.raises(Exception):
        result.valid = False