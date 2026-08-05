"""
Unit tests for export_storage / import_storage (ADR-012).

Covers:
- round-trip for InMemoryStorage
- round-trip for SQLiteStorage (in-memory DB)
- merge vs clear modes
- graph_registry preserved across export/import
- embeddings preserved across export/import
- outbox tasks preserved across export/import
- cross-backend restore (InMemory → SQLite, SQLite → InMemory)
"""

from __future__ import annotations

from datetime import UTC

import cks as _cks
import pytest

from cks_runtime.session.session import RuntimeSession
from cks_runtime.storage.memory_storage import InMemoryStorage
from cks_runtime.storage.sqlite_storage import SQLiteStorage
from cks_runtime.versioning.version import RuntimeVersion

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_KS_JSON = '{"objects":[{"identity":{"id":"obj-1","type":"Test","name":"t"},"structure":{}}]}'


def _make_ks():
    return _cks.parse(_KS_JSON)


def _make_session(storage, session_id: str = "s1"):
    """Save a minimal session and return it."""
    session = RuntimeSession(
        knowledge_structure=_make_ks(),
        session_id=session_id,
        metadata={"author": "test"},
    )
    storage.save_session(session)
    return session


def _make_version(storage, session_id: str = "s1", version_id: str = "v1"):
    """Save a minimal version and return it."""
    from datetime import datetime
    version = RuntimeVersion(
        session_id=session_id,
        transaction_id="tx1",
        knowledge_structure=_make_ks(),
        metadata={},
        version_id=version_id,
        created_at=datetime.now(UTC),
    )
    storage.save_version(version)
    return version


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


@pytest.fixture
def sqlite_storage():
    store = SQLiteStorage(":memory:")
    yield store
    store.clear()


@pytest.fixture
def memory_storage():
    return InMemoryStorage()


# ---------------------------------------------------------------------------
# Basic export structure
# ---------------------------------------------------------------------------


def test_export_returns_version_key(storage):
    dump = storage.export_storage()
    assert dump["version"] == 1
    assert "exported_at" in dump


def test_export_empty_store(storage):
    dump = storage.export_storage()
    assert dump["sessions"] == []
    assert dump["versions"] == []
    assert dump["graphs"] == []


# ---------------------------------------------------------------------------
# Session round-trip
# ---------------------------------------------------------------------------


def test_session_round_trip(storage):
    _make_session(storage, "s1")
    _make_session(storage, "s2")

    dump = storage.export_storage()
    assert len(dump["sessions"]) == 2

    # Import into a fresh store of the same type
    if isinstance(storage, InMemoryStorage):
        target = InMemoryStorage()
    else:
        target = SQLiteStorage(":memory:")

    target.import_storage(dump, mode="clear")

    assert target.has_session("s1")
    assert target.has_session("s2")

    loaded = target.load_session("s1")
    assert loaded.session_id == "s1"
    assert loaded.metadata == {"author": "test"}


# ---------------------------------------------------------------------------
# Version round-trip
# ---------------------------------------------------------------------------


def test_version_round_trip(storage):
    _make_session(storage, "s1")
    _make_version(storage, "s1", "v1")

    dump = storage.export_storage()
    assert len(dump["versions"]) == 1

    if isinstance(storage, InMemoryStorage):
        target = InMemoryStorage()
    else:
        target = SQLiteStorage(":memory:")

    target.import_storage(dump)

    assert target.has_version("v1")
    v = target.load_version("v1")
    assert v.session_id == "s1"
    assert v.version_id == "v1"


# ---------------------------------------------------------------------------
# Graph registry
# ---------------------------------------------------------------------------


def test_graph_registry_round_trip(storage):
    storage.register_graph("g1", "s1", description="first", tags="demo", public=True)
    storage.register_graph("g2", "s2", description="second")

    dump = storage.export_storage()
    assert len(dump["graphs"]) == 2

    if isinstance(storage, InMemoryStorage):
        target = InMemoryStorage()
    else:
        target = SQLiteStorage(":memory:")

    target.import_storage(dump, mode="clear")

    g1 = target.get_graph("g1")
    assert g1 is not None
    assert g1["session_id"] == "s1"
    assert g1["public"] is True
    assert g1["description"] == "first"
    assert g1["tags"] == "demo"

    g2 = target.get_graph("g2")
    assert g2 is not None
    assert g2["session_id"] == "s2"


# ---------------------------------------------------------------------------
# Outbox tasks (SQLite only — InMemoryStorage doesn't persist them)
# ---------------------------------------------------------------------------


def test_outbox_tasks_round_trip(sqlite_storage):
    sqlite_storage.enqueue_task("projection", "s1", '{"k": "v"}')
    sqlite_storage.enqueue_task("inference_conflict", "s1", '{"x": 1}')

    dump = sqlite_storage.export_storage()
    assert len(dump["outbox_tasks"]) == 2

    target = SQLiteStorage(":memory:")
    target.import_storage(dump, mode="clear")

    # Tasks should be importable and visible as PENDING
    task = target.dequeue_next_outbox_task()
    assert task is not None
    assert task.task_type in {"projection", "inference_conflict"}


def test_outbox_tasks_exclude_dead_and_completed(sqlite_storage):
    """Only PENDING / FAILED tasks are exported."""
    sqlite_storage.enqueue_task("projection", "s1", "{}")
    task = sqlite_storage.dequeue_next_outbox_task()
    sqlite_storage.complete_outbox_task(task.task_id)  # marks COMPLETED

    sqlite_storage.enqueue_task("inference_conflict", "s1", "{}")

    dump = sqlite_storage.export_storage()
    statuses = {t["status"] for t in dump["outbox_tasks"]}
    # Only PENDING remains (COMPLETED was filtered out)
    assert "COMPLETED" not in statuses
    assert all(s in {"PENDING", "FAILED"} for s in statuses)


# ---------------------------------------------------------------------------
# Embeddings (SQLite only)
# ---------------------------------------------------------------------------


def test_embeddings_round_trip(sqlite_storage):
    import numpy as np

    vec = np.ones(4, dtype=np.float32)
    blob = vec.tobytes()
    sqlite_storage.save_object_embeddings("obj1", "s1", blob)

    dump = sqlite_storage.export_storage()
    assert len(dump["embeddings"]) == 1
    assert dump["embeddings"][0]["object_id"] == "obj1"

    target = SQLiteStorage(":memory:")
    target.import_storage(dump, mode="clear")

    # Verify the embedding is searchable in the target
    query = np.ones(4, dtype=np.float32).tobytes()
    results = target.search_embeddings(query, "s1", top_k=5)
    assert any(r[0] == "obj1" for r in results)


# ---------------------------------------------------------------------------
# mode="clear"
# ---------------------------------------------------------------------------


def test_clear_mode_wipes_existing_data(storage):
    _make_session(storage, "existing")
    storage.register_graph("old-graph", "existing")

    _make_session(storage, "s1")

    fresh_source = InMemoryStorage() if isinstance(storage, InMemoryStorage) else SQLiteStorage(":memory:")
    _make_session(fresh_source, "new-session")

    dump = fresh_source.export_storage()
    storage.import_storage(dump, mode="clear")

    # Old session/graph should be gone
    assert not storage.has_session("existing")
    assert storage.get_graph("old-graph") is None

    # New session from dump should be present
    assert storage.has_session("new-session")


# ---------------------------------------------------------------------------
# mode="merge"
# ---------------------------------------------------------------------------


def test_merge_mode_skips_existing(storage):
    _make_session(storage, "s1")

    # Export from a fresh source with the same session id
    source = InMemoryStorage() if isinstance(storage, InMemoryStorage) else SQLiteStorage(":memory:")
    _make_session(source, "s1")
    _make_session(source, "s2")

    dump = source.export_storage()
    storage.import_storage(dump, mode="merge")

    # Both should be present; s1 not duplicated
    assert storage.has_session("s1")
    assert storage.has_session("s2")
    sessions = storage.list_sessions()
    ids = {s.session_id for s in sessions}
    assert ids == {"s1", "s2"}  # no duplicate s1


def test_merge_graph_skips_existing(storage):
    storage.register_graph("g1", "original-session", description="original")

    source = InMemoryStorage() if isinstance(storage, InMemoryStorage) else SQLiteStorage(":memory:")
    source.register_graph("g1", "new-session", description="from dump")
    source.register_graph("g2", "s2")

    dump = source.export_storage()
    storage.import_storage(dump, mode="merge")

    g1 = storage.get_graph("g1")
    assert g1["session_id"] == "original-session"  # not overwritten
    assert storage.get_graph("g2") is not None


# ---------------------------------------------------------------------------
# Cross-backend restore (InMemory → SQLite)
# ---------------------------------------------------------------------------


def test_cross_backend_memory_to_sqlite(memory_storage, sqlite_storage):
    _make_session(memory_storage, "s1")
    _make_version(memory_storage, "s1", "v1")
    memory_storage.register_graph("g1", "s1", description="cross-backend")

    dump = memory_storage.export_storage()
    sqlite_storage.import_storage(dump, mode="clear")

    assert sqlite_storage.has_session("s1")
    assert sqlite_storage.has_version("v1")
    assert sqlite_storage.get_graph("g1")["description"] == "cross-backend"


def test_cross_backend_sqlite_to_memory(sqlite_storage, memory_storage):
    _make_session(sqlite_storage, "s1")
    _make_version(sqlite_storage, "s1", "v1")
    sqlite_storage.register_graph("g1", "s1", public=True)

    dump = sqlite_storage.export_storage()
    memory_storage.import_storage(dump, mode="clear")

    assert memory_storage.has_session("s1")
    assert memory_storage.has_version("v1")
    g = memory_storage.get_graph("g1")
    assert g is not None
    assert g["public"] is True


# ---------------------------------------------------------------------------
# Idempotency: import twice in merge mode
# ---------------------------------------------------------------------------


def test_idempotent_merge(storage):
    _make_session(storage, "s1")
    storage.register_graph("g1", "s1")

    dump = storage.export_storage()

    if isinstance(storage, InMemoryStorage):
        target = InMemoryStorage()
    else:
        target = SQLiteStorage(":memory:")

    target.import_storage(dump, mode="merge")
    target.import_storage(dump, mode="merge")  # second import

    sessions = target.list_sessions()
    assert len(sessions) == 1

    graphs = target.list_graphs()
    assert len(graphs) == 1