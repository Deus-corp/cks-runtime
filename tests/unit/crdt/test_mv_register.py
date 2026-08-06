from __future__ import annotations

import sqlite3

import pytest

from cks_runtime.crdt.crdt_store import InMemoryCRDTStore, SQLiteCRDTStore
from cks_runtime.crdt.version_vector import VersionVector


@pytest.fixture(params=["sqlite", "memory"])
def store(request):
    if request.param == "sqlite":
        return SQLiteCRDTStore(sqlite3.connect(":memory:"))
    return InMemoryCRDTStore()


def test_update_pointer_adds_first_record(store):
    vv = VersionVector(clocks={"n1": 1})
    assert store.update_pointer("head", "obj-a", vv, "n1") is True
    pointers = store.get_pointers("head")
    assert len(pointers) == 1
    assert pointers[0]["object_id"] == "obj-a"
    assert pointers[0]["vector_clock"] == {"n1": 1}
    assert pointers[0]["origin_node"] == "n1"


def test_dominating_update_replaces_dominated_pointer(store):
    store.update_pointer("head", "obj-a", VersionVector(clocks={"n1": 1}), "n1")
    added = store.update_pointer("head", "obj-b", VersionVector(clocks={"n1": 2}), "n1")
    assert added is True
    pointers = store.get_pointers("head")
    assert [p["object_id"] for p in pointers] == ["obj-b"]


def test_dominated_update_is_discarded(store):
    store.update_pointer("head", "obj-a", VersionVector(clocks={"n1": 2}), "n1")
    added = store.update_pointer("head", "obj-b", VersionVector(clocks={"n1": 1}), "n1")
    assert added is False
    pointers = store.get_pointers("head")
    assert [p["object_id"] for p in pointers] == ["obj-a"]


def test_concurrent_updates_are_both_kept():
    store = InMemoryCRDTStore()
    store.update_pointer("head", "obj-a", VersionVector(clocks={"n1": 1}), "n1")
    added = store.update_pointer("head", "obj-b", VersionVector(clocks={"n2": 1}), "n2")
    assert added is True
    pointers = store.get_pointers("head")
    ids = {p["object_id"] for p in pointers}
    assert ids == {"obj-a", "obj-b"}


def test_get_pointers_empty_for_unknown_key(store):
    assert store.get_pointers("nonexistent") == []


def test_resolve_pointer_collapses_to_winner(store):
    store.update_pointer("head", "obj-a", VersionVector(clocks={"n1": 1}), "n1")
    store.update_pointer("head", "obj-b", VersionVector(clocks={"n2": 1}), "n2")
    assert len(store.get_pointers("head")) == 2

    resolved = store.resolve_pointer("head", "obj-a")
    assert resolved is True
    pointers = store.get_pointers("head")
    assert [p["object_id"] for p in pointers] == ["obj-a"]


def test_resolve_pointer_returns_false_for_unknown_winner(store):
    store.update_pointer("head", "obj-a", VersionVector(clocks={"n1": 1}), "n1")
    assert store.resolve_pointer("head", "does-not-exist") is False
    # unchanged
    assert [p["object_id"] for p in store.get_pointers("head")] == ["obj-a"]


def test_multiple_pointer_keys_are_independent(store):
    store.update_pointer("head-a", "obj-1", VersionVector(clocks={"n1": 1}), "n1")
    store.update_pointer("head-b", "obj-2", VersionVector(clocks={"n1": 1}), "n1")
    assert [p["object_id"] for p in store.get_pointers("head-a")] == ["obj-1"]
    assert [p["object_id"] for p in store.get_pointers("head-b")] == ["obj-2"]