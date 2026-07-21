"""Unit tests for the CKS Core adapter."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

import cks
from cks_runtime.core_api.merge_conflict import RuntimeMergeConflictError
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
        lambda ks, extra_constraints=None: FakeValidationResult(),
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

    validate.assert_called_once_with(knowledge_structure, extra_constraints=None)


def test_validate_forwards_extra_constraints(monkeypatch, adapter):
    """extra_constraints passed to the adapter must reach cks.validate() unchanged."""
    validate = Mock()

    class FakeValidationResult:
        is_valid = True
        diagnostics = ()
        metadata = {}

    validate.return_value = FakeValidationResult()
    monkeypatch.setattr("cks.validate", validate)

    knowledge_structure = object()
    sentinel_constraints = [object()]
    adapter.validate(knowledge_structure, extra_constraints=sentinel_constraints)

    validate.assert_called_once_with(
        knowledge_structure, extra_constraints=sentinel_constraints
    )


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


def test_merge_calls_core(monkeypatch, adapter):
    merge = Mock(return_value="merged-structure")
    monkeypatch.setattr("cks.merge", merge)

    base = object()
    branch_a = object()
    branch_b = object()

    result = adapter.merge(base, branch_a, branch_b)

    assert result == "merged-structure"
    merge.assert_called_once_with(base, branch_a, branch_b)


def test_merge_translates_conflict_error(monkeypatch, adapter):
    """
    cks-core's MergeConflictError must never escape the adapter as-is
    -- it is translated into the Runtime-native
    RuntimeMergeConflictError, mirroring how _translate_diagnostic
    translates validation diagnostics at the same boundary.
    """

    core_conflict = cks.MergeConflict(
        object_id="obj-1",
        base=None,
        branch_a="a-value",
        branch_b="b-value",
    )

    def raise_conflict(base, branch_a, branch_b):
        raise cks.MergeConflictError([core_conflict])

    monkeypatch.setattr("cks.merge", raise_conflict)

    with pytest.raises(RuntimeMergeConflictError) as excinfo:
        adapter.merge(object(), object(), object())

    translated = excinfo.value.conflicts
    assert len(translated) == 1
    assert translated[0].object_id == "obj-1"
    assert translated[0].base is None
    assert translated[0].branch_a == "a-value"
    assert translated[0].branch_b == "b-value"

    # The original cks-core exception is preserved as the cause, for
    # anyone debugging with a full traceback, without being the type
    # Runtime callers need to catch.
    assert isinstance(excinfo.value.__cause__, cks.MergeConflictError)


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