"""Unit tests for the CKS Core adapter."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from cks_runtime.core_api.validation_result import (
    RuntimeValidationResult,
)
from cks_runtime_plugins.cks_core.adapter import (
    CksCoreAdapter,
)


@pytest.fixture
def adapter() -> CksCoreAdapter:
    return CksCoreAdapter()


def test_validate(monkeypatch, adapter):
    class FakeDiagnostic:
        identity = "TEST-001"
        severity = "error"
        message = "something is wrong"
        location = "obj-1"
        metadata = {"source": "core"}

    class FakeValidationResult:
        is_valid = True
        diagnostics = (FakeDiagnostic(),)
        metadata = {"source": "core"}

    monkeypatch.setattr(
        "cks.validate",
        lambda ks: FakeValidationResult(),
    )

    result = adapter.validate(object())

    assert isinstance(result, RuntimeValidationResult)
    assert result.valid is True

    translated = result.diagnostics[0]
    assert translated.code == "TEST-001"
    assert translated.severity.value == "error"
    assert translated.message == "something is wrong"
    assert translated.source.value == "core"
    assert dict(translated.metadata)["source"] == "core"
    assert dict(translated.metadata).get("location") == "obj-1"


def test_serialize(monkeypatch, adapter):

    monkeypatch.setattr(
        "cks.serialize",
        lambda ks: "serialized",
    )

    result = adapter.serialize(
        object(),
    )

    assert result == "serialized"


def test_evolve(monkeypatch, adapter):

    expected = object()

    monkeypatch.setattr(
        "cks_runtime_plugins.cks_core.adapter.compose",
        lambda ks, ops: expected,
    )

    result = adapter.evolve(
        object(),
        [],
    )

    assert result is expected


def test_evolve_requires_sequence(adapter):

    with pytest.raises(
        TypeError,
    ):
        adapter.evolve(
            object(),
            object(),
        )


def test_explain(monkeypatch, adapter):

    class FakeKnowledgeStructure:

        def __init__(self):

            self.objects = [
                1,
                2,
                3,
            ]

        def relations(
            self,
        ):
            return [
                "r1",
                "r2",
            ]

    monkeypatch.setattr(
        "cks_runtime_plugins.cks_core.adapter.cks_inspect",
        lambda ks: "summary",
    )

    result = adapter.explain(
        FakeKnowledgeStructure(),
    )

    assert result == {
        "object_count": 3,
        "relation_count": 2,
        "summary": "summary",
    }


def test_validate_calls_core(monkeypatch, adapter):
    validate = Mock()

    class FakeValidationResult:
        is_valid = True
        diagnostics = ()
        metadata = {}

    validate.return_value = FakeValidationResult()
    monkeypatch.setattr("cks.validate", validate)

    knowledge_structure = object()
    adapter.validate(knowledge_structure)

    validate.assert_called_once_with(knowledge_structure)


def test_serialize_calls_core(monkeypatch, adapter):

    serialize = Mock(
        return_value="json",
    )

    monkeypatch.setattr(
        "cks.serialize",
        serialize,
    )

    knowledge_structure = object()

    adapter.serialize(
        knowledge_structure,
    )

    serialize.assert_called_once_with(
        knowledge_structure,
    )


def test_evolve_calls_compose(monkeypatch, adapter):

    compose = Mock(
        return_value={},
    )

    monkeypatch.setattr(
        "cks_runtime_plugins.cks_core.adapter.compose",
        compose,
    )

    knowledge_structure = object()

    operations = []

    adapter.evolve(
        knowledge_structure,
        operations,
    )

    compose.assert_called_once_with(
        knowledge_structure,
        operations,
    )


def test_explain_calls_inspect(monkeypatch, adapter):

    inspect = Mock(
        return_value="summary",
    )

    monkeypatch.setattr(
        "cks_runtime_plugins.cks_core.adapter.cks_inspect",
        inspect,
    )

    class FakeKnowledgeStructure:

        objects = []

        def relations(self):
            return []

    knowledge_structure = FakeKnowledgeStructure()

    adapter.explain(
        knowledge_structure,
    )

    inspect.assert_called_once_with(
        knowledge_structure,
    )