"""
Unit tests for GarbageCollector and the SQLiteStorage GC storage methods.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import cks
import pytest

from cks_runtime.gc.garbage_collector import GarbageCollector
from cks_runtime.session.session import RuntimeSession
from cks_runtime.storage.sqlite_storage import SQLiteStorage

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def storage():
    store = SQLiteStorage(":memory:")
    yield store
    store.clear()


def _make_ks():
    return cks.parse(
        '{"objects":[{"identity":{"id":"o1","type":"T","name":"N"},"structure":{}}]}'
    )


def _make_session(session_id: str = "s1", *, closed: bool = False) -> RuntimeSession:
    s = RuntimeSession(knowledge_structure=_make_ks(), session_id=session_id)
    s.closed = closed
    return s


def _backdate(storage: SQLiteStorage, session_id: str, days: int) -> None:
    """Manually push modified_at back in time so GC sees it as stale."""
    storage._conn.execute(
        "UPDATE sessions SET modified_at = datetime('now', ?) WHERE session_id = ?",
        (f"-{days} days", session_id),
    )
    storage._conn.commit()


# ---------------------------------------------------------------------------
# SQLiteStorage GC storage method tests
# ---------------------------------------------------------------------------


def test_list_sessions_modified_before_returns_stale(storage):
    s1 = _make_session("s1")
    s2 = _make_session("s2")
    storage.save_session(s1)
    storage.save_session(s2)

    _backdate(storage, "s1", 2)  # stale: 2 days old
    # s2 is fresh (just saved, modified_at = now)

    # Use a cutoff 1 day ago — s1 is 2 days old (stale), s2 is seconds old (fresh).
    cutoff = datetime.now(UTC) - timedelta(days=1)
    stale = storage.list_sessions_modified_before(cutoff)
    ids = {s.session_id for s in stale}

    assert "s1" in ids
    assert "s2" not in ids


def test_list_sessions_modified_before_empty_when_all_fresh(storage):
    s = _make_session("s1")
    storage.save_session(s)

    cutoff = datetime.now(UTC) - timedelta(hours=24)
    assert storage.list_sessions_modified_before(cutoff) == []


def test_list_sessions_modified_before_respects_limit(storage):
    for i in range(10):
        s = _make_session(f"s{i}")
        storage.save_session(s)
        _backdate(storage, f"s{i}", 10)

    cutoff = datetime.now(UTC) - timedelta(hours=1)
    result = storage.list_sessions_modified_before(cutoff, limit=3)
    assert len(result) == 3


def test_archive_session_moves_to_archive_table(storage):
    s = _make_session("s1")
    storage.save_session(s)

    assert storage.has_session("s1")
    storage.archive_session(s)
    assert not storage.has_session("s1")

    row = storage._conn.execute(
        "SELECT session_id FROM archive_sessions WHERE session_id = ?", ("s1",)
    ).fetchone()
    assert row is not None


def test_archive_session_is_idempotent(storage):
    s = _make_session("s1")
    storage.save_session(s)
    storage.archive_session(s)
    # Archiving again should not raise
    storage.archive_session(s)

    rows = storage._conn.execute(
        "SELECT COUNT(*) FROM archive_sessions WHERE session_id = ?", ("s1",)
    ).fetchone()
    assert rows[0] == 1  # still only one archived row


def test_save_session_updates_modified_at(storage):
    s = _make_session("s1")
    storage.save_session(s)
    _backdate(storage, "s1", 5)

    row_before = storage._conn.execute(
        "SELECT modified_at FROM sessions WHERE session_id = ?", ("s1",)
    ).fetchone()

    storage.save_session(s)  # second save should refresh modified_at

    row_after = storage._conn.execute(
        "SELECT modified_at FROM sessions WHERE session_id = ?", ("s1",)
    ).fetchone()

    assert row_after[0] > row_before[0]


# ---------------------------------------------------------------------------
# GarbageCollector behaviour tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gc_evicts_stale_closed_sessions(storage):
    s_closed = _make_session("closed", closed=True)
    s_open = _make_session("open", closed=False)

    storage.save_session(s_closed)
    storage.save_session(s_open)

    _backdate(storage, "closed", 10)
    _backdate(storage, "open", 10)

    gc = GarbageCollector(storage, retention=timedelta(hours=1))
    await gc.run_once()

    # Closed stale session should be evicted
    assert not storage.has_session("closed")
    # Open session must NEVER be evicted regardless of age
    assert storage.has_session("open")


@pytest.mark.asyncio
async def test_gc_does_not_evict_fresh_closed_sessions(storage):
    s = _make_session("fresh_closed", closed=True)
    storage.save_session(s)  # modified_at = now

    gc = GarbageCollector(storage, retention=timedelta(hours=24))
    await gc.run_once()

    assert storage.has_session("fresh_closed")


@pytest.mark.asyncio
async def test_gc_noop_on_unsupported_storage():
    """GarbageCollector.start() should not raise on InMemoryStorage."""
    from cks_runtime.storage.memory_storage import InMemoryStorage

    mem = InMemoryStorage()
    gc = GarbageCollector(mem)
    # Should log a warning and return without creating a task
    await gc.start()
    # The GC may still create a task, but it must be stoppable without error
    await gc.stop()
