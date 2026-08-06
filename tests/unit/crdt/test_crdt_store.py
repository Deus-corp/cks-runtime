from __future__ import annotations

import sqlite3

import cks
import pytest

from cks_runtime.crdt.crdt_store import (
    InMemoryCRDTStore,
    SQLiteCRDTStore,
    object_id_for,
)
from cks_runtime.crdt.version_vector import VersionVector


def _make_object(object_id: str, obj_type: str = "note", **structure) -> cks.KnowledgeObject:
    return cks.KnowledgeObject(
        identity=cks.ObjectIdentity(id=object_id, type=obj_type, name=object_id),
        structure=structure,
    )


@pytest.fixture(params=["sqlite", "memory"])
def store(request):
    if request.param == "sqlite":
        return SQLiteCRDTStore(sqlite3.connect(":memory:"))
    return InMemoryCRDTStore()


def test_add_object_returns_true_when_new(store):
    obj = _make_object("obj-1", value=1)
    assert store.add_object(obj) is True


def test_add_object_returns_false_for_duplicate(store):
    obj = _make_object("obj-1", value=1)
    store.add_object(obj)
    assert store.add_object(obj) is False


def test_content_identical_objects_dedupe_by_hash(store):
    # Same identity + structure from two "different" constructions
    # (simulating two nodes independently producing the same object)
    # must collide into a single CRDT record.
    obj_a = _make_object("obj-1", value=1)
    obj_b = _make_object("obj-1", value=1)
    assert object_id_for(obj_a) == object_id_for(obj_b)
    assert store.add_object(obj_a) is True
    assert store.add_object(obj_b) is False
    assert len(store.list_objects()) == 1


def test_different_structure_is_a_different_record(store):
    obj_a = _make_object("obj-1", value=1)
    obj_b = _make_object("obj-1", value=2)
    assert object_id_for(obj_a) != object_id_for(obj_b)
    store.add_object(obj_a)
    store.add_object(obj_b)
    assert len(store.list_objects()) == 2


def test_get_object_roundtrip(store):
    obj = _make_object("obj-1", value=42)
    store.add_object(obj)
    record = store.get_object(object_id_for(obj))
    assert record is not None
    assert record["structure"]["value"] == 42
    assert record["identity"]["id"] == "obj-1"


def test_get_object_missing_returns_none(store):
    assert store.get_object("0" * 64) is None


def test_merge_objects_counts_only_new(store):
    obj1 = _make_object("obj-1", value=1)
    obj2 = _make_object("obj-2", value=2)
    added = store.merge_objects([obj1, obj2, obj1])
    assert added == 2
    assert len(store.list_objects()) == 2


def test_root_hash_changes_after_add(store):
    before = store.get_root_hash()
    store.add_object(_make_object("obj-1", value=1))
    after = store.get_root_hash()
    assert before != after


def test_version_vector_roundtrip(store):
    vv = VersionVector(clocks={"node-a": 3, "node-b": 7})
    store.update_version_vector("node-a", vv)
    restored = store.get_version_vector("node-a")
    assert restored.clocks == vv.clocks


def test_version_vector_defaults_to_empty(store):
    restored = store.get_version_vector("never-seen-node")
    assert restored.clocks == {}
