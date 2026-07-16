from cks_runtime.core_api.adapter import CoreAdapter
from cks_runtime.core_api.interfaces import CoreInterface
from cks_runtime.core_api.validation_result import (
    RuntimeValidationResult,
)


class CoreStub(CoreInterface):

    def validate(
        self,
        knowledge_structure,
    ):
        return RuntimeValidationResult(
            valid=True,
        )

    def serialize(
        self,
        knowledge_structure,
    ):
        return {
            "serialized": knowledge_structure,
        }

    def evolve(
        self,
        knowledge_structure,
        operation,
    ):
        return {
            "structure": knowledge_structure,
            "operation": operation,
        }

    def explain(
        self,
        knowledge_structure,
    ):
        return {
            "explanation": knowledge_structure,
        }


def test_adapter_without_core():

    adapter = CoreAdapter(None)

    assert adapter.attached is False


def test_adapter_with_core():

    adapter = CoreAdapter(
        CoreStub(),
    )

    assert adapter.attached is True


def test_validate_without_core():

    adapter = CoreAdapter(None)

    result = adapter.validate({})

    assert isinstance(
        result,
        RuntimeValidationResult,
    )

    assert result.valid is True
    assert result.has_diagnostics is False


def test_validate_with_core():

    adapter = CoreAdapter(
        CoreStub(),
    )

    result = adapter.validate(
        {"example": 1},
    )

    assert isinstance(
        result,
        RuntimeValidationResult,
    )

    assert result.valid is True


def test_serialize():

    adapter = CoreAdapter(
        CoreStub(),
    )

    result = adapter.serialize(
        {"a": 1},
    )

    assert result == {
        "serialized": {
            "a": 1,
        }
    }


def test_evolve():

    adapter = CoreAdapter(
        CoreStub(),
    )

    result = adapter.evolve(
        {"a": 1},
        "operation",
    )

    assert result == {
        "structure": {
            "a": 1,
        },
        "operation": "operation",
    }


def test_explain():

    adapter = CoreAdapter(
        CoreStub(),
    )

    result = adapter.explain(
        {"a": 1},
    )

    assert result == {
        "explanation": {
            "a": 1,
        }
    }


def test_validate_without_core_returns_success():

    adapter = CoreAdapter(None)

    result = adapter.validate(
        {"anything": True},
    )

    assert result.valid
    assert result.diagnostics == ()