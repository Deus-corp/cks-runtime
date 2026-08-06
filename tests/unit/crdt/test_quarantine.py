from __future__ import annotations

import cks
import pytest

from cks_runtime.crdt.crdt_store import InMemoryCRDTStore, object_id_for
from cks_runtime.crdt.quarantine import CRDTQuarantine


def _make_object(object_id: str, **structure) -> cks.KnowledgeObject:
    return cks.KnowledgeObject(
        identity=cks.ObjectIdentity(id=object_id, type="note", name=object_id),
        structure=structure,
    )


class _AlwaysValidCks:
    def validate(self, knowledge_object):
        return True


class _AlwaysInvalidCks:
    def validate(self, knowledge_object):
        return False


class _NoValidatorCks:
    """Duck-typed cks-core stand-in exposing no `validate` at all."""


@pytest.fixture
def store():
    return InMemoryCRDTStore()


@pytest.mark.asyncio
async def test_valid_object_is_added(store):
    quarantine = CRDTQuarantine(store, _AlwaysValidCks())
    obj = _make_object("obj-1", value=1)
    assert await quarantine.validate_and_add(obj) is True
    assert store.get_object(object_id_for(obj)) is not None


@pytest.mark.asyncio
async def test_invalid_object_is_rejected_and_never_added(store):
    quarantine = CRDTQuarantine(store, _AlwaysInvalidCks())
    obj = _make_object("obj-1", value=1)
    assert await quarantine.validate_and_add(obj) is False
    assert store.list_objects() == []


@pytest.mark.asyncio
async def test_non_knowledge_object_dict_without_id_is_rejected(store):
    quarantine = CRDTQuarantine(store, _AlwaysValidCks())
    assert await quarantine.validate_and_add({"not": "an id"}) is False
    assert store.list_objects() == []


@pytest.mark.asyncio
async def test_missing_validator_fails_open_on_structural_check(store):
    quarantine = CRDTQuarantine(store, _NoValidatorCks())
    obj = _make_object("obj-1", value=1)
    assert await quarantine.validate_and_add(obj) is True


@pytest.mark.asyncio
async def test_process_batch_counts_only_admitted(store):
    quarantine = CRDTQuarantine(store, _AlwaysValidCks())
    objects = [_make_object("obj-1", value=1), _make_object("obj-2", value=2)]
    count = await quarantine.process_batch(objects)
    assert count == 2
    assert len(store.list_objects()) == 2


@pytest.mark.asyncio
async def test_process_batch_skips_invalid_entries(store):
    quarantine = CRDTQuarantine(store, _AlwaysInvalidCks())
    objects = [_make_object("obj-1", value=1), _make_object("obj-2", value=2)]
    count = await quarantine.process_batch(objects)
    assert count == 0
    assert store.list_objects() == []


# ---------------------------------------------------------------------------
# Regression: the real validator this is normally wired up against
# (``CoreBridge.validate``) validates a *KnowledgeStructure*, not a bare
# KnowledgeObject -- quarantine must call it with the right shape.
# ---------------------------------------------------------------------------


class _StructureExpectingCks:
    """
    Stand-in for ``CoreBridge``: only accepts a ``cks.KnowledgeStructure``
    (mirrors its real signature -- see cks_runtime/core_api/bridge.py),
    raises on anything else (a bare KnowledgeObject) so a regression back
    to the old "call validate(knowledge_object) directly" shape fails
    loudly instead of happening to still return something truthy.
    """

    def __init__(self) -> None:
        self.seen_structures: list[cks.KnowledgeStructure] = []

    def validate(self, knowledge_structure):
        if not isinstance(knowledge_structure, cks.KnowledgeStructure):
            raise TypeError(
                f"expected a cks.KnowledgeStructure, got {type(knowledge_structure)!r}"
            )
        self.seen_structures.append(knowledge_structure)
        return True


@pytest.mark.asyncio
async def test_validate_is_called_with_a_knowledge_structure_not_a_bare_object(store):
    validator = _StructureExpectingCks()
    quarantine = CRDTQuarantine(store, validator)
    obj = _make_object("obj-1", value=1)

    assert await quarantine.validate_and_add(obj) is True
    assert len(validator.seen_structures) == 1
    assert [o.identity.id for o in validator.seen_structures[0].objects] == ["obj-1"]


@pytest.mark.asyncio
async def test_dict_payload_skips_structural_validation_but_still_checked_for_identity(store):
    """
    A dict payload has no live KnowledgeObject to wrap into a
    KnowledgeStructure, so it can't go through the structural
    validator -- but it must still be rejected if its claimed id
    doesn't match its own content (the identity check).
    """
    validator = _StructureExpectingCks()
    quarantine = CRDTQuarantine(store, validator)
    real = _make_object("obj-1", value=1)
    tampered_record = {
        "id": real._hash.hex(),  # claims to be `real`'s hash...
        "identity": {"id": "obj-1", "type": "note", "name": "obj-1"},
        "structure": {"value": 999},  # ...but the content doesn't match it
    }

    assert await quarantine.validate_and_add(tampered_record) is False
    assert store.list_objects() == []
    assert validator.seen_structures == []