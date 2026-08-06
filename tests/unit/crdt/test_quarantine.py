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