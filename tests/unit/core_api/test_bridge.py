"""Unit tests for CoreBridge."""

from __future__ import annotations

import pytest

from cks_runtime.core_api.bridge import CoreBridge
from cks_runtime.core_api.validation_result import (
    RuntimeValidationResult,
)
from cks_runtime.core_api.interfaces import CoreInterface


class _FakeCore(CoreInterface):
    """Minimal Core implementation for testing."""

    def validate(self, knowledge_structure):
        return RuntimeValidationResult(
            valid=False,
            diagnostics=("problem",),
            metadata={"source": "fake"},
        )

    def evolve(self, knowledge_structure, operation):
        return {
            "evolved": True,
            "knowledge_structure": knowledge_structure,
            "operation": operation,
        }

    def serialize(self, knowledge_structure):
        return '{"serialized": true}'

    def explain(self, knowledge_structure):
        return {
            "summary": "fake explanation",
        }


@pytest.fixture
def bridge():
    return CoreBridge(
        implementation=_FakeCore(),
    )


@pytest.fixture
def detached_bridge():
    return CoreBridge(
        implementation=None,
    )


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


def test_bridge_available(bridge):
    assert bridge.available is True


def test_bridge_unavailable(detached_bridge):
    assert detached_bridge.available is False


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_validate_delegates_to_core(bridge):
    result = bridge.validate({})

    assert isinstance(
        result,
        RuntimeValidationResult,
    )

    assert result.valid is False
    assert result.diagnostics == (
        "problem",
    )
    assert result.metadata == {
        "source": "fake",
    }


def test_validate_without_core_returns_success(
    detached_bridge,
):
    result = detached_bridge.validate({})

    assert result.valid is True
    assert result.diagnostics == ()
    assert result.metadata == {}


# ---------------------------------------------------------------------------
# Evolution
# ---------------------------------------------------------------------------


def test_evolve_delegates_to_core(bridge):
    ks = {"x": 1}
    op = {"add": "node"}

    result = bridge.evolve(
        ks,
        op,
    )

    assert result["evolved"] is True
    assert result["knowledge_structure"] is ks
    assert result["operation"] is op


def test_evolve_without_core_returns_original_object(
    detached_bridge,
):
    ks = {"x": 1}

    result = detached_bridge.evolve(
        ks,
        {},
    )

    assert result is ks


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_serialize_delegates_to_core(bridge):
    result = bridge.serialize({})

    assert result == '{"serialized": true}'


def test_serialize_without_core_returns_original(
    detached_bridge,
):
    ks = {"x": 1}
    with pytest.raises(RuntimeError, match="No Runtime Core implementation"):
        detached_bridge.serialize(ks)


# ---------------------------------------------------------------------------
# Explainability
# ---------------------------------------------------------------------------


def test_explain_delegates_to_core(bridge):
    result = bridge.explain({})

    assert result == {
        "summary": "fake explanation",
    }


def test_explain_without_core_returns_none(
    detached_bridge,
):
    assert detached_bridge.explain({}) == {}