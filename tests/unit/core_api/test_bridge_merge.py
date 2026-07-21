"""
Unit tests for CoreBridge.merge() / supports_merge.

NOTE: tests/unit/core_api/test_bridge.py already exists but (pre-existing,
unrelated to this change) contains a duplicate of the CoreBridge source
itself rather than test functions, so new CoreBridge tests are added here
instead to avoid colliding with that file.
"""

from __future__ import annotations

from typing import Any

import pytest

from cks_runtime.core_api.bridge import CoreBridge
from cks_runtime.core_api.interfaces import CoreInterface
from cks_runtime.core_api.merge_conflict import (
    RuntimeMergeConflict,
    RuntimeMergeConflictError,
)
from cks_runtime.core_api.validation_result import RuntimeValidationResult


class _MergingCore(CoreInterface):
    """CoreInterface implementation that overrides merge()."""

    def validate(self, knowledge_structure: Any) -> RuntimeValidationResult:
        return RuntimeValidationResult.success()

    def evolve(self, knowledge_structure: Any, operation: Any) -> Any:
        return knowledge_structure

    def serialize(self, knowledge_structure: Any) -> str:
        return "{}"

    def explain(self, knowledge_structure: Any) -> Any:
        return {}

    def diff(self, source: Any, target: Any) -> list[Any]:
        return []

    def merge(self, base: Any, branch_a: Any, branch_b: Any) -> Any:
        return {"merged": [base, branch_a, branch_b]}


class _NonMergingCore(CoreInterface):
    """CoreInterface implementation that does NOT override merge()."""

    def validate(self, knowledge_structure: Any) -> RuntimeValidationResult:
        return RuntimeValidationResult.success()

    def evolve(self, knowledge_structure: Any, operation: Any) -> Any:
        return knowledge_structure

    def serialize(self, knowledge_structure: Any) -> str:
        return "{}"

    def explain(self, knowledge_structure: Any) -> Any:
        return {}

    def diff(self, source: Any, target: Any) -> list[Any]:
        return []


def test_merge_without_core_raises_runtime_error():

    bridge = CoreBridge(None)

    with pytest.raises(RuntimeError):
        bridge.merge({}, {}, {})


def test_merge_delegates_to_implementation():

    bridge = CoreBridge(_MergingCore())

    result = bridge.merge("base", "a", "b")

    assert result == {"merged": ["base", "a", "b"]}


def test_merge_propagates_not_implemented_when_core_lacks_support():

    bridge = CoreBridge(_NonMergingCore())

    with pytest.raises(NotImplementedError):
        bridge.merge({}, {}, {})


def test_merge_propagates_conflict_error():

    class ConflictingCore(_MergingCore):
        def merge(self, base, branch_a, branch_b):
            raise RuntimeMergeConflictError(
                [
                    RuntimeMergeConflict(
                        object_id="obj-1",
                        base=None,
                        branch_a="a",
                        branch_b="b",
                    )
                ]
            )

    bridge = CoreBridge(ConflictingCore())

    with pytest.raises(RuntimeMergeConflictError) as excinfo:
        bridge.merge({}, {}, {})

    assert excinfo.value.conflicts[0].object_id == "obj-1"


def test_supports_merge_false_without_core():

    bridge = CoreBridge(None)

    assert bridge.supports_merge is False


def test_supports_merge_false_for_non_merging_core():

    bridge = CoreBridge(_NonMergingCore())

    assert bridge.supports_merge is False


def test_supports_merge_true_for_merging_core():

    bridge = CoreBridge(_MergingCore())

    assert bridge.supports_merge is True
