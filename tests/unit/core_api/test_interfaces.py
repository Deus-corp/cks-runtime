"""Unit tests for CoreInterface."""

from __future__ import annotations

from typing import Any

from cks_runtime.core_api.interfaces import (
    CoreInterface,
)
from cks_runtime.core_api.validation_result import (
    RuntimeValidationResult,
)


class DummyCore(CoreInterface):
    """Minimal CoreInterface implementation."""

    def validate(
        self,
        knowledge_structure: Any,
    ) -> RuntimeValidationResult:
        return RuntimeValidationResult.success()

    def evolve(
        self,
        knowledge_structure: Any,
        operation: Any,
    ) -> Any:
        return knowledge_structure

    def serialize(
        self,
        knowledge_structure: Any,
    ) -> str:
        return "{}"

    def explain(
        self,
        knowledge_structure: Any,
    ) -> Any:
        return {
            "summary": "dummy",
        }
    
    def diff(self, source, target):
        return []


def test_interface_can_be_implemented():

    core = DummyCore()

    result = core.validate({})

    assert result.valid is True

    assert core.serialize({}) == "{}"

    assert core.evolve(
        {"x": 1},
        {},
    ) == {"x": 1}

    assert core.explain({}) == {
        "summary": "dummy",
    }


def test_validate_returns_runtime_validation_result():

    core = DummyCore()

    result = core.validate({})

    assert isinstance(
        result,
        RuntimeValidationResult,
    )