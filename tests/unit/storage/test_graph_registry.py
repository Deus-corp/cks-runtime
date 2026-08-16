"""
Tests for the graph_registry storage layer (Memory Agent v1 lookup +
Memory Agent v2 `public` gallery flag), across InMemoryStorage and
SQLiteStorage.
"""

from __future__ import annotations

import pytest

from cks_runtime.storage.memory_storage import InMemoryStorage
from cks_runtime.storage.sqlite_storage import SQLiteStorage

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(params=["memory", "sqlite"])
def storage(request):
    if request.param == "memory":
        yield InMemoryStorage()
    else:
        store = SQLiteStorage(":memory:")
        yield store
        store.clear()


# ---------------------------------------------------------------------------
# register_graph / get_graph: `public` defaults and round-trip
# ---------------------------------------------------------------------------


def test_public_defaults_to_false(storage):
    storage.register_graph("g1", "s1")
    record = storage.get_graph("g1")
    assert record["public"] is False


def test_public_true_round_trips(storage):
    storage.register_graph("g1", "s1", "desc", "tag1", public=True)
    record = storage.get_graph("g1")
    assert record["public"] is True
    assert record["name"] == "g1"
    assert record["session_id"] == "s1"
    assert record["description"] == "desc"
    assert record["tags"] == "tag1"


def test_get_graph_missing_returns_none(storage):
    assert storage.get_graph("nope") is None


# ---------------------------------------------------------------------------
# unregister_graph
# ---------------------------------------------------------------------------


def test_unregister_graph_removes_entry(storage):
    storage.register_graph("g1", "s1")
    assert storage.get_graph("g1") is not None

    removed = storage.unregister_graph("g1")

    assert removed is True
    assert storage.get_graph("g1") is None


def test_unregister_graph_missing_returns_false(storage):
    removed = storage.unregister_graph("nope")
    assert removed is False


def test_unregister_graph_does_not_affect_other_entries(storage):
    storage.register_graph("g1", "s1")
    storage.register_graph("g2", "s2")

    storage.unregister_graph("g1")

    assert storage.get_graph("g1") is None
    assert storage.get_graph("g2") is not None


def test_reregister_updates_public_flag(storage):
    storage.register_graph("g1", "s1", public=False)
    assert storage.get_graph("g1")["public"] is False

    storage.register_graph("g1", "s1", public=True)
    assert storage.get_graph("g1")["public"] is True


# ---------------------------------------------------------------------------
# list_graphs: public_only filtering
# ---------------------------------------------------------------------------


def test_list_graphs_public_only_filters_private(storage):
    storage.register_graph("public-graph", "s1", public=True)
    storage.register_graph("private-graph", "s2", public=False)

    all_graphs = storage.list_graphs()
    assert {g["name"] for g in all_graphs} == {"public-graph", "private-graph"}

    public_graphs = storage.list_graphs(public_only=True)
    assert {g["name"] for g in public_graphs} == {"public-graph"}


def test_list_graphs_public_only_combined_with_tag(storage):
    storage.register_graph("a", "s1", tags="demo", public=True)
    storage.register_graph("b", "s2", tags="demo", public=False)
    storage.register_graph("c", "s3", tags="other", public=True)

    result = storage.list_graphs(tag="demo", public_only=True)
    assert {g["name"] for g in result} == {"a"}


def test_list_graphs_public_only_empty_when_none_public(storage):
    storage.register_graph("g1", "s1", public=False)
    assert storage.list_graphs(public_only=True) == []


# ---------------------------------------------------------------------------
# Backward compatibility: pre-existing (v1) rows without `public` set
# ---------------------------------------------------------------------------


def test_sqlite_migration_defaults_existing_rows_to_private(tmp_path):
    db_path = str(tmp_path / "v1.db")

    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE graph_registry (
            name        TEXT PRIMARY KEY,
            session_id  TEXT NOT NULL,
            description TEXT,
            tags        TEXT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        "INSERT INTO graph_registry (name, session_id) VALUES ('legacy', 's0')"
    )
    conn.commit()
    conn.close()

    store = SQLiteStorage(db_path)
    try:
        record = store.get_graph("legacy")
        assert record is not None
        assert record["public"] is False
        assert store.list_graphs(public_only=True) == []
    finally:
        store.clear()


# ---------------------------------------------------------------------------
# lifecycle_state: defaults, round-trip, and re-register preservation
# ---------------------------------------------------------------------------


def test_lifecycle_state_defaults_to_draft(storage):
    storage.register_graph("g1", "s1")
    record = storage.get_graph("g1")
    assert record["lifecycle_state"] == "draft"


def test_lifecycle_state_defaults_to_published_when_public(storage):
    storage.register_graph("g1", "s1", public=True)
    record = storage.get_graph("g1")
    assert record["lifecycle_state"] == "published"


def test_lifecycle_state_explicit_value_round_trips(storage):
    storage.register_graph("g1", "s1", lifecycle_state="under_review")
    record = storage.get_graph("g1")
    assert record["lifecycle_state"] == "under_review"


def test_lifecycle_state_survives_plain_reregister(storage):
    storage.register_graph("g1", "s1", lifecycle_state="active")
    assert storage.get_graph("g1")["lifecycle_state"] == "active"

    # A plain re-register (e.g. update_registered_graph editing the
    # description) must not silently reset lifecycle_state back to
    # draft/published.
    storage.register_graph("g1", "s1", description="updated desc")
    assert storage.get_graph("g1")["lifecycle_state"] == "active"


def test_lifecycle_state_can_be_explicitly_transitioned(storage):
    storage.register_graph("g1", "s1", lifecycle_state="draft")
    storage.register_graph("g1", "s1", lifecycle_state="published")
    assert storage.get_graph("g1")["lifecycle_state"] == "published"


def test_list_graphs_includes_lifecycle_state(storage):
    storage.register_graph("g1", "s1", lifecycle_state="stale")
    entries = storage.list_graphs()
    assert entries[0]["lifecycle_state"] == "stale"


# ---------------------------------------------------------------------------
# Backward compatibility: pre-existing rows without `lifecycle_state` set
# ---------------------------------------------------------------------------


def test_sqlite_migration_defaults_existing_rows_lifecycle_state(tmp_path):
    db_path = str(tmp_path / "v2.db")

    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE graph_registry (
            name        TEXT PRIMARY KEY,
            session_id  TEXT NOT NULL,
            description TEXT,
            tags        TEXT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
            public      INTEGER NOT NULL DEFAULT 0,
            source_graph_name TEXT,
            visibility  TEXT NOT NULL DEFAULT 'private',
            team        TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO graph_registry (name, session_id, public, visibility) "
        "VALUES ('legacy-private', 's0', 0, 'private')"
    )
    conn.execute(
        "INSERT INTO graph_registry (name, session_id, public, visibility) "
        "VALUES ('legacy-public', 's1', 1, 'public')"
    )
    conn.commit()
    conn.close()

    store = SQLiteStorage(db_path)
    try:
        assert store.get_graph("legacy-private")["lifecycle_state"] == "draft"
        assert store.get_graph("legacy-public")["lifecycle_state"] == "published"
    finally:
        store.clear()