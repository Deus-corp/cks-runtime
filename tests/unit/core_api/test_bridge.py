"""Unit tests for CoreBridge."""

from __future__ import annotations

import pytest

from cks_runtime.core_api.bridge import CoreBridge
from cks_runtime.core_api.validation_result import (
    RuntimeValidationResult,
)
from cks_runtime.core_api.interfaces import CoreInterface
from cks_runtime.core_api.merge_conflict import (
    RuntimeMergeConflict,
    RuntimeMergeConflictError,
)


class _FakeCore(CoreInterface):
    """Minimal Core implementation for testing."""

    def validate(self, knowledge_structure, *, extra_constraints=None):
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

    def diff(self, source, target):
        return []

    def merge(self, base, branch_a, branch_b):
        return {"merged": [base, branch_a, branch_b]}

    def hash(self, knowledge_structure):
        return "fake-hash"


@pytest.fixture
def bridge():
    return CoreBridge(implementation=_FakeCore())


@pytest.fixture
def detached_bridge():
    return CoreBridge(implementation=None)


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
    assert isinstance(result, RuntimeValidationResult)
    assert result.valid is False
    assert result.diagnostics == ("problem",)
    assert result.metadata == {"source": "fake"}


def test_validate_without_core_returns_success(detached_bridge):
    result = detached_bridge.validate({})
    assert result.valid is True
    assert result.diagnostics == ()
    assert result.metadata == {}


def test_validate_passes_extra_constraints(bridge):
    result = bridge.validate({}, extra_constraints=["test"])
    assert isinstance(result, RuntimeValidationResult)


# ---------------------------------------------------------------------------
# Evolution
# ---------------------------------------------------------------------------


def test_evolve_delegates_to_core(bridge):
    ks = {"x": 1}
    op = {"add": "node"}
    result = bridge.evolve(ks, op)
    assert result["evolved"] is True
    assert result["knowledge_structure"] is ks
    assert result["operation"] is op


def test_evolve_without_core_returns_original_object(detached_bridge):
    ks = {"x": 1}
    result = detached_bridge.evolve(ks, {})
    assert result is ks


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_serialize_delegates_to_core(bridge):
    result = bridge.serialize({})
    assert result == '{"serialized": true}'


def test_serialize_without_core_raises(detached_bridge):
    with pytest.raises(RuntimeError, match="No Runtime Core implementation"):
        detached_bridge.serialize({})


# ---------------------------------------------------------------------------
# Explainability
# ---------------------------------------------------------------------------


def test_explain_delegates_to_core(bridge):
    result = bridge.explain({})
    assert result == {"summary": "fake explanation"}


def test_explain_without_core_returns_empty_dict(detached_bridge):
    result = detached_bridge.explain({})
    assert result == {}


# ---------------------------------------------------------------------------
# Structural Diff
# ---------------------------------------------------------------------------


def test_diff_delegates_to_core(bridge):
    result = bridge.diff({}, {})
    assert result == []


def test_diff_without_core_returns_empty_list(detached_bridge):
    result = detached_bridge.diff({}, {})
    assert result == []


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


def test_merge_delegates_to_core(bridge):
    result = bridge.merge("base", "a", "b")
    assert result == {"merged": ["base", "a", "b"]}


def test_merge_without_core_raises(detached_bridge):
    with pytest.raises(RuntimeError, match="No Runtime Core implementation"):
        detached_bridge.merge({}, {}, {})


def test_supports_merge_true(bridge):
    assert bridge.supports_merge is True


def test_supports_merge_false(detached_bridge):
    assert detached_bridge.supports_merge is False


# ---------------------------------------------------------------------------
# Content Hashing
# ---------------------------------------------------------------------------


def test_hash_delegates_to_core(bridge):
    result = bridge.hash({})
    assert result == "fake-hash"


def test_hash_without_core_raises(detached_bridge):
    with pytest.raises(RuntimeError, match="No Runtime Core implementation"):
        detached_bridge.hash({})


def test_supports_hash_true(bridge):
    assert bridge.supports_hash is True


def test_supports_hash_false(detached_bridge):
    assert detached_bridge.supports_hash is False