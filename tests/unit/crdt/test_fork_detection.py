from __future__ import annotations

import sqlite3

import pytest

from cks_runtime.crdt.crdt_store import InMemoryCRDTStore, SQLiteCRDTStore


@pytest.fixture(params=["sqlite", "memory"])
def store(request):
    if request.param == "sqlite":
        return SQLiteCRDTStore(sqlite3.connect(":memory:"))
    return InMemoryCRDTStore()


def test_escalate_fork_returns_event_id(store):
    event_id = store.escalate_fork("head", ["obj-a", "obj-b"], [{"n1": 1}, {"n2": 1}])
    assert isinstance(event_id, str)
    assert event_id


def test_escalated_fork_appears_in_pending_list(store):
    event_id = store.escalate_fork("head", ["obj-a", "obj-b"], [{"n1": 1}, {"n2": 1}])
    pending = store.list_pending_forks()
    assert len(pending) == 1
    record = pending[0]
    assert record["event_id"] == event_id
    assert record["pointer_key"] == "head"
    assert record["conflicting_object_ids"] == ["obj-a", "obj-b"]
    assert record["vector_clocks"] == [{"n1": 1}, {"n2": 1}]


def test_mark_fork_resolved_removes_from_pending(store):
    event_id = store.escalate_fork("head", ["obj-a", "obj-b"], [{"n1": 1}, {"n2": 1}])
    store.mark_fork_resolved(event_id)
    assert store.list_pending_forks() == []


def test_multiple_pending_forks_are_all_listed(store):
    store.escalate_fork("head-a", ["obj-1", "obj-2"], [{"n1": 1}, {"n2": 1}])
    store.escalate_fork("head-b", ["obj-3", "obj-4"], [{"n1": 1}, {"n3": 1}])
    pending = store.list_pending_forks()
    assert {r["pointer_key"] for r in pending} == {"head-a", "head-b"}


def test_resolving_one_fork_does_not_affect_another(store):
    id_a = store.escalate_fork("head-a", ["obj-1", "obj-2"], [{"n1": 1}, {"n2": 1}])
    store.escalate_fork("head-b", ["obj-3", "obj-4"], [{"n1": 1}, {"n3": 1}])
    store.mark_fork_resolved(id_a)
    pending = store.list_pending_forks()
    assert len(pending) == 1
    assert pending[0]["pointer_key"] == "head-b"