"""Integration tests for CksCoreAdapter."""
import pytest
from cks_runtime_core.adapter import CksCoreAdapter
from cks.core import KnowledgeObject, KnowledgeStructure, ObjectIdentity, CanonicalRelation


def _make_structure():
    """Create a minimal valid KnowledgeStructure."""
    return KnowledgeStructure([
        KnowledgeObject(identity=ObjectIdentity(id="1", type="Test", name="One")),
        KnowledgeObject(identity=ObjectIdentity(id="2", type="Test", name="Two")),
        CanonicalRelation(
            identity=ObjectIdentity(id="rel-1", type="Relation", name="knows"),
            participants=["1", "2"],
            relation_type="knows",
        ),
    ])


@pytest.fixture
def adapter():
    return CksCoreAdapter()


def test_validate_valid(adapter):
    structure = _make_structure()
    result = adapter.validate(structure)
    assert result.valid is True


def test_validate_invalid(adapter):
    # Create a structure with a dangling reference (relation points to non-existent object)
    structure = KnowledgeStructure([
        KnowledgeObject(identity=ObjectIdentity(id="1", type="Test", name="One")),
        CanonicalRelation(
            identity=ObjectIdentity(id="rel-1", type="Relation", name="broken"),
            participants=["1", "nonexistent"],
            relation_type="depends_on",
        ),
    ])
    result = adapter.validate(structure)
    assert result.valid is False
    assert result.has_diagnostics


def test_serialize(adapter):
    structure = _make_structure()
    json_str = adapter.serialize(structure)
    assert isinstance(json_str, str)
    assert "objects" in json_str


def test_evolve(adapter):
    from cks.evolution import AddObject, RemoveObject
    structure = _make_structure()
    new_obj = KnowledgeObject(identity=ObjectIdentity(id="3", type="Test", name="Three"))
    evolved = adapter.evolve(structure, [AddObject(new_obj)])
    assert len(evolved.objects) == len(structure.objects) + 1

    # Remove the newly added object
    restored = adapter.evolve(evolved, [RemoveObject("3")])
    assert len(restored.objects) == len(structure.objects)


def test_explain(adapter):
    structure = _make_structure()
    explanation = adapter.explain(structure)
    assert explanation["object_count"] == len(structure.objects)
    assert "summary" in explanation