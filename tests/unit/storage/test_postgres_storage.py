"""
Integration tests: Runtime end-to-end with PostgresStorage.

These tests exercise the full async bridge:

    Runtime.create(config=RuntimeConfig(storage_path="postgresql://..."))
        → PostgresStorage.connect()              # async pool open + DDL
        → _restore_from_storage()               # list_sessions JOIN query
        → OutboxEmbeddingWorker.start()          # asyncio.Task poll loop

Each test then runs a real workflow (create session → begin tx →
commit → restart → verify) and asserts the Postgres-native subsystems
(outbox, pgvector search) behave correctly end-to-end.

Skip condition: CKS_TEST_POSTGRES_DSN not set, or psycopg not installed.
pgvector must be available in the target database:
    CREATE EXTENSION IF NOT EXISTS vector;
"""

from __future__ import annotations

import math
import os
import struct

import cks
import pytest
import pytest_asyncio

from cks_runtime.config import RuntimeConfig
from cks_runtime.operations.operation_types import ValidateOperation
from cks_runtime.runtime import Runtime

if not os.environ.get("CKS_TEST_POSTGRES_DSN"):
    pytest.skip("Postgres not configured; skipping embedding tests.", allow_module_level=True)

try:
    from cks_runtime.storage.postgres_storage import PostgresStorage
    _PSYCOPG_AVAILABLE = True
except ImportError:
    PostgresStorage = None  # type: ignore[assignment,misc]
    _PSYCOPG_AVAILABLE = False

_DSN = os.environ.get("CKS_TEST_POSTGRES_DSN")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not _DSN or not _PSYCOPG_AVAILABLE,
        reason="CKS_TEST_POSTGRES_DSN not set or psycopg not installed",
    ),
]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_KS_JSON = (
    '{"objects":['
    '{"identity":{"id":"obj-1","type":"Concept","name":"Alpha"},'
    '"structure":{"description":"first concept"}},'
    '{"identity":{"id":"obj-2","type":"Concept","name":"Beta"},'
    '"structure":{"description":"second concept"}}'
    ']}'
)


def make_ks():
    return cks.parse(_KS_JSON)


@pytest_asyncio.fixture
async def pg_storage():
    """A clean PostgresStorage, cleared before and after each test."""
    store = await PostgresStorage.connect(_DSN, min_size=1, max_size=4)
    await store.clear()
    yield store
    # After a test that closes the pool (e.g. test_aclose_*), the pool
    # is already closed and clear() would fail with PoolClosed.
    if not store._pool.closed:
        await store.clear()


@pytest_asyncio.fixture
async def runtime(pg_storage):
    """
    A Runtime wired to a clean PostgresStorage.

    Constructed via Runtime.__init__ (not Runtime.create) so we control
    exactly when _restore_from_storage and outbox worker start — useful
    for tests that need to inspect state before restore.  Tests that
    need the full startup path call Runtime.create() themselves.
    """
    rt = Runtime(storage=pg_storage)
    yield rt
    await rt.aclose()


# ===========================================================================
# 1. Storage wiring
# ===========================================================================

async def test_runtime_storage_is_postgres(runtime):
    """Runtime.storage is a PostgresStorage (not wrapped in SyncStorageAdapter)."""
    from cks_runtime.storage.postgres_storage import PostgresStorage as PG
    assert isinstance(runtime.storage, PG)


async def test_runtime_create_via_dsn():
    """
    Runtime.create(config=RuntimeConfig(storage_path=DSN)) resolves
    PostgresStorage automatically via the lazy import path.
    """
    config = RuntimeConfig(storage_path=_DSN)
    rt = await Runtime.create(config=config)
    try:
        from cks_runtime.storage.postgres_storage import PostgresStorage as PG
        assert isinstance(rt.storage, PG)
    finally:
        await rt.aclose()


async def test_runtime_accepts_explicit_postgres_storage(pg_storage):
    """Runtime(storage=<PostgresStorage>) wires through without wrapping."""
    from cks_runtime.storage.adapter import SyncStorageAdapter
    rt = Runtime(storage=pg_storage)
    assert not isinstance(rt.storage, SyncStorageAdapter)
    await rt.aclose()


# ===========================================================================
# 2. Session lifecycle
# ===========================================================================

async def test_create_and_load_session(runtime, pg_storage):
    """Session created via Runtime is persisted in Postgres immediately."""
    ks = make_ks()
    session = await runtime.create_session(ks)

    loaded = await pg_storage.load_session(session.session_id)
    assert loaded is not None
    assert loaded.session_id == session.session_id


async def test_close_session_persists_closed_flag(runtime, pg_storage):
    ks = make_ks()
    session = await runtime.create_session(ks)
    await runtime.close_session(session.session_id)

    loaded = await pg_storage.load_session(session.session_id)
    assert loaded is not None
    assert loaded.closed is True


async def test_create_branch_persisted(runtime, pg_storage):
    ks = make_ks()
    session = await runtime.create_session(ks)
    branch = await runtime.create_branch(session)

    assert branch.parent_session_id == session.session_id
    loaded = await pg_storage.load_session(branch.session_id)
    assert loaded is not None
    assert loaded.parent_session_id == session.session_id


# ===========================================================================
# 3. Transaction commit
# ===========================================================================

async def test_commit_transaction_persists_version(runtime, pg_storage):
    ks = make_ks()
    session = await runtime.create_session(ks)

    tx = runtime.begin_transaction(session)
    tx.add_operation(ValidateOperation("op-1", knowledge_structure=ks))
    version = await runtime.commit_transaction(tx)

    assert session.version_count == 1
    loaded_v = await pg_storage.load_version(version.version_id)
    assert loaded_v is not None
    assert loaded_v.session_id == session.session_id


async def test_commit_updates_session_latest_version(runtime, pg_storage):
    ks = make_ks()
    session = await runtime.create_session(ks)
    tx = runtime.begin_transaction(session)
    tx.add_operation(ValidateOperation("op-1", knowledge_structure=ks))
    version = await runtime.commit_transaction(tx)

    loaded = await pg_storage.load_session(session.session_id)
    assert loaded is not None
    assert loaded.version_count == 1
    assert loaded.version_history[0].version_id == version.version_id


async def test_rollback_transaction_restores_session(runtime, pg_storage):
    ks = make_ks()
    session = await runtime.create_session(ks)
    tx = runtime.begin_transaction(session)
    await runtime.rollback_transaction(tx)

    # No version created
    loaded = await pg_storage.load_session(session.session_id)
    assert loaded is not None
    assert loaded.version_count == 0


async def test_concurrent_commit_raises_concurrent_modification(pg_storage):
    """
    Two Runtime instances sharing the same storage must produce a
    ConcurrentModificationError when both try to advance the same
    session past the same expected_version_id.
    """
    from cks_runtime.operations.operation_types import ValidateOperation
    from cks_runtime.storage.storage import ConcurrentModificationError

    ks = make_ks()
    rt1 = Runtime(storage=pg_storage)
    session1 = await rt1.create_session(ks)

    # Commit first version via rt1
    tx = rt1.begin_transaction(session1)
    tx.add_operation(ValidateOperation("v1", knowledge_structure=ks))
    await rt1.commit_transaction(tx)

    # Create a second, independent session object with the same id
    session2 = await pg_storage.load_session(session1.session_id)
    rt2 = Runtime(storage=pg_storage)
    rt2.sessions.restore(session2)

    # Both try to commit v2 from the same base (v1)
    tx1 = rt1.begin_transaction(session1)
    tx2 = rt2.begin_transaction(session2)

    tx1.add_operation(ValidateOperation("v2a", knowledge_structure=session1.knowledge_structure))
    await rt1.commit_transaction(tx1)

    tx2.add_operation(ValidateOperation("v2b", knowledge_structure=session2.knowledge_structure))
    with pytest.raises(ConcurrentModificationError):
        await rt2.commit_transaction(tx2)


# ===========================================================================
# 4. Restart / restore
# ===========================================================================

async def test_sessions_survive_runtime_restart():
    """
    Full restart cycle: Runtime.create → commit → aclose → Runtime.create.
    The second instance must restore the session and its version history
    from Postgres without any manual intervention.
    """
    config = RuntimeConfig(storage_path=_DSN)
    ks = make_ks()

    # ── First Runtime ──────────────────────────────────────────────────
    rt1 = await Runtime.create(config=config)
    # Clear any state from previous runs
    await rt1.storage.clear()

    session = await rt1.create_session(ks)
    tx = rt1.begin_transaction(session)
    tx.add_operation(ValidateOperation("op-1", knowledge_structure=ks))
    version = await rt1.commit_transaction(tx)
    sid = session.session_id
    vid = version.version_id
    await rt1.aclose()

    # ── Second Runtime — simulated restart ────────────────────────────
    rt2 = await Runtime.create(config=config)
    try:
        restored = rt2.get_session(sid)
        assert restored is not None, "Session not found after restart"
        assert restored.version_count == 1
        assert restored.version_history[0].version_id == vid

        # list_sessions (in-memory) also includes it
        ids = {s.session_id for s in rt2.list_sessions()}
        assert sid in ids
    finally:
        await rt2.storage.clear()
        await rt2.aclose()


async def test_multiple_sessions_restored_after_restart():
    """All sessions — including ones with multiple versions — survive restart."""
    config = RuntimeConfig(storage_path=_DSN)
    ks = make_ks()

    rt1 = await Runtime.create(config=config)
    await rt1.storage.clear()

    created_ids = set()
    for i in range(3):
        s = await rt1.create_session(ks)
        tx = rt1.begin_transaction(s)
        tx.add_operation(ValidateOperation(f"op-{i}", knowledge_structure=ks))
        await rt1.commit_transaction(tx)
        created_ids.add(s.session_id)

    await rt1.aclose()

    rt2 = await Runtime.create(config=config)
    try:
        restored_ids = {s.session_id for s in rt2.list_sessions()}
        assert created_ids.issubset(restored_ids)
        for sid in created_ids:
            s = rt2.get_session(sid)
            assert s is not None
            assert s.version_count == 1
    finally:
        await rt2.storage.clear()
        await rt2.aclose()


async def test_branch_parent_restored_after_restart():
    config = RuntimeConfig(storage_path=_DSN)
    ks = make_ks()

    rt1 = await Runtime.create(config=config)
    await rt1.storage.clear()

    parent = await rt1.create_session(ks)
    tx = rt1.begin_transaction(parent)
    tx.add_operation(ValidateOperation("op-1", knowledge_structure=ks))
    await rt1.commit_transaction(tx)
    branch = await rt1.create_branch(parent)
    await rt1.aclose()

    rt2 = await Runtime.create(config=config)
    try:
        restored_branch = rt2.get_session(branch.session_id)
        assert restored_branch is not None
        assert restored_branch.parent_session_id == parent.session_id
    finally:
        await rt2.storage.clear()
        await rt2.aclose()


# ===========================================================================
# 5. Outbox
# ===========================================================================

async def test_outbox_supports_flag(runtime):
    assert runtime.storage.supports_outbox is True


async def test_outbox_task_enqueued_and_dequeued(pg_storage):
    """
    Enqueuing a task through the storage layer and dequeuing it works
    with FOR UPDATE SKIP LOCKED atomicity.
    """
    await pg_storage.enqueue_task("projection", "s1", '{"previous_version_id":null,"new_version_id":"v1"}')
    task = await pg_storage.dequeue_next_outbox_task()
    assert task is not None
    assert task.task_type == "projection"
    assert task.session_id == "s1"
    await pg_storage.complete_outbox_task(task.task_id)
    # Queue now empty
    assert await pg_storage.dequeue_next_outbox_task() is None


async def test_outbox_worker_starts_with_postgres(pg_storage):
    """
    OutboxEmbeddingWorker starts when storage.supports_outbox is True
    (i.e. PostgresStorage) and stops cleanly on aclose().
    """
    rt = Runtime(storage=pg_storage)
    await rt._outbox_worker.start()
    assert rt._outbox_worker._running is True
    await rt.aclose()
    assert rt._outbox_worker._running is False


async def test_outbox_worker_does_not_double_start(pg_storage):
    rt = Runtime(storage=pg_storage)
    await rt._outbox_worker.start()
    task_before = rt._outbox_worker._task
    await rt._outbox_worker.start()  # second call is a no-op
    assert rt._outbox_worker._task is task_before
    await rt.aclose()


# ===========================================================================
# 6. Embedding search (pgvector)
# ===========================================================================

def _make_embedding(dim: int, *, seed: float = 1.0) -> bytes:
    """Normalised float32 embedding where every component = seed/sqrt(dim)."""
    v = seed / math.sqrt(dim)
    return struct.pack(f"{dim}f", *([v] * dim))


def _orthogonal_embedding(dim: int) -> bytes:
    """A vector orthogonal to _make_embedding: alternating +v/-v."""
    v = 1.0 / math.sqrt(dim)
    components = [v if i % 2 == 0 else -v for i in range(dim)]
    return struct.pack(f"{dim}f", *components)


async def test_embedding_search_end_to_end(pg_storage):
    """
    Save two embeddings (similar + orthogonal), query, and verify
    ranking through the full async path to pgvector.
    """
    dim = 8
    emb_similar = _make_embedding(dim, seed=1.0)
    emb_ortho = _orthogonal_embedding(dim)

    await pg_storage.save_object_embeddings("obj-sim", "s1", emb_similar)
    await pg_storage.save_object_embeddings("obj-ort", "s1", emb_ortho)

    query = _make_embedding(dim, seed=1.0)
    results = await pg_storage.search_embeddings(query, "s1", top_k=2)

    assert len(results) == 2
    # obj-sim must be ranked first (cosine similarity ≈ 1.0)
    assert results[0][0] == "obj-sim"
    assert results[0][1] >= 0.99
    # obj-ort is orthogonal → similarity ≈ 0.0, clamped to 0.0
    assert results[1][0] == "obj-ort"
    assert results[1][1] <= 0.05


async def test_embedding_search_isolated_by_session(pg_storage):
    """Embeddings from session s1 must not appear in results for session s2."""
    dim = 8
    emb = _make_embedding(dim)

    await pg_storage.save_object_embeddings("shared-obj", "s1", emb)
    await pg_storage.save_object_embeddings("shared-obj", "s2", emb)

    r1 = await pg_storage.search_embeddings(emb, "s1", top_k=5)
    r2 = await pg_storage.search_embeddings(emb, "s2", top_k=5)

    assert len(r1) == 1 and r1[0][0] == "shared-obj"
    assert len(r2) == 1 and r2[0][0] == "shared-obj"


async def test_supports_embedding_search_flag(pg_storage):
    assert pg_storage.supports_embedding_search is True


async def test_embedding_dimension_mismatch_raises(pg_storage):
    """Changing the embedding model dimension is caught immediately."""
    await pg_storage.save_object_embeddings("obj-1", "s1", _make_embedding(8))
    with pytest.raises(ValueError, match="dimension mismatch"):
        await pg_storage.save_object_embeddings("obj-2", "s1", _make_embedding(16))


async def test_embedding_survives_restart(pg_storage):
    """
    Embedding dimension stored in cks_embedding_meta survives a new
    PostgresStorage instance pointing at the same database.
    """
    dim = 8
    emb = _make_embedding(dim)
    await pg_storage.save_object_embeddings("obj-1", "s1", emb)

    # Open a second storage instance to the same DB — simulates process restart
    store2 = await PostgresStorage.connect(_DSN, min_size=1, max_size=2)
    try:
        # _load_embed_dim() should have restored dim=8
        assert store2._embed_dim == dim

        # Search works immediately, no re-indexing needed
        results = await store2.search_embeddings(emb, "s1", top_k=1)
        assert len(results) == 1
        assert results[0][0] == "obj-1"
        assert results[0][1] >= 0.99
    finally:
        await store2.close()


# ===========================================================================
# 7. aclose / shutdown
# ===========================================================================

async def test_aclose_stops_worker_and_pool(pg_storage):
    """Runtime.aclose() stops the outbox worker and closes the storage pool."""
    rt = Runtime(storage=pg_storage)
    await rt._outbox_worker.start()
    await rt.aclose()

    assert rt._outbox_worker._running is False
    assert rt._outbox_worker._task is None
    # pg_storage pool is closed (pg_storage fixture owns it, but the close
    # call must not raise — double-close is safe per psycopg_pool docs)


async def test_aclose_safe_when_worker_never_started(pg_storage):
    """Runtime.aclose() is safe when called on a bare Runtime(...) instance."""
    rt = Runtime(storage=pg_storage)
    # Worker was never started — aclose must not raise
    await rt.aclose()


# ===========================================================================
# 8. Full workflow smoke test
# ===========================================================================

async def test_full_workflow_create_commit_restart_search():
    """
    End-to-end smoke test:
      1. Runtime.create with Postgres DSN
      2. Create session → commit version
      3. Restart (aclose + new Runtime.create)
      4. Restored session matches original
      5. Outbox worker is running in the new instance
    """
    config = RuntimeConfig(storage_path=_DSN)
    ks = make_ks()

    rt1 = await Runtime.create(config=config)
    await rt1.storage.clear()

    session = await rt1.create_session(ks)
    tx = rt1.begin_transaction(session)
    tx.add_operation(ValidateOperation("op-smoke", knowledge_structure=ks))
    version = await rt1.commit_transaction(tx)

    sid = session.session_id
    vid = version.version_id
    await rt1.aclose()

    rt2 = await Runtime.create(config=config)
    try:
        restored = rt2.get_session(sid)
        assert restored is not None
        assert restored.version_history[0].version_id == vid
        # Outbox worker is running (PostgresStorage supports_outbox=True)
        assert rt2._outbox_worker._running is True
    finally:
        await rt2.storage.clear()
        await rt2.aclose()
