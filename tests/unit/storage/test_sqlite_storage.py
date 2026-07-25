"""
Tests for SQLiteStorage (JSON-based).
"""

from __future__ import annotations

import sqlite3

import pytest
from cks_runtime.session.session import RuntimeSession
from cks_runtime.storage.sqlite_storage import SQLiteStorage, _retry_on_locked
from cks_runtime.storage.storage import ConcurrentModificationError
from cks_runtime.versioning.version import RuntimeVersion
import cks


class _FlakyConnProxy:
    """
    Wraps a real ``sqlite3.Connection`` so the first ``fail_times``
    calls to ``execute()`` raise a transient "database is locked"
    error before delegating to the real connection.

    ``sqlite3.Connection`` instances (and the type itself) don't allow
    arbitrary attribute assignment, so ``execute`` can't be monkeypatched
    directly on the connection -- this proxy stands in for it instead.
    Swap it onto ``storage._conn`` (a plain attribute on a normal
    Python object) to verify a write path actually survives one round
    of lock contention end-to-end, not just that ``_retry_on_locked``
    works in isolation.
    """

    def __init__(self, real_conn, fail_times: int = 1) -> None:
        self._real = real_conn
        self._fail_times = fail_times
        self.calls = 0

    def execute(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise sqlite3.OperationalError("database is locked")
        return self._real.execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture
def storage():
    """Create a fresh in-memory SQLiteStorage for each test."""
    store = SQLiteStorage(":memory:")
    yield store
    store.clear()


def make_ks():
    """Minimal valid knowledge structure for testing."""
    return cks.parse(
        '{"objects":[{"identity":{"id":"obj-1","type":"Test","name":"t"},"structure":{}}]}'
    )


def make_session(session_id: str = "s1") -> RuntimeSession:
    return RuntimeSession(
        knowledge_structure=make_ks(),
        session_id=session_id,
    )


def make_version(
    session_id: str = "s1",
    version_id: str = "v1",
    ks=None,
) -> RuntimeVersion:
    if ks is None:
        ks = make_ks()
    return RuntimeVersion(
        session_id=session_id,
        transaction_id="t1",
        knowledge_structure=ks,
        metadata={"m": 1},
        version_id=version_id,
    )


def test_save_and_load_session(storage):
    session = make_session("s1")
    storage.save_session(session)
    loaded = storage.load_session("s1")
    assert loaded is not None
    assert loaded.session_id == "s1"
    # Compare serialized forms to avoid deep structure issues
    assert cks.serialize(loaded.knowledge_structure) == cks.serialize(make_ks())


def test_load_missing_session_returns_none(storage):
    assert storage.load_session("missing") is None


def test_save_session_cas_accepts_matching_expected_version(storage):
    session = make_session("s1")
    session.add_version(make_version("s1", "v1"))
    storage.save_version(make_version("s1", "v1"))
    storage.save_session(session)  # initial write, no CAS

    session.add_version(make_version("s1", "v2"))
    storage.save_version(make_version("s1", "v2"))
    # Matches the latest_version_id the first write just persisted (v1).
    storage.save_session(session, expected_version_id="v1")

    loaded = storage.load_session("s1")
    assert [v.version_id for v in loaded.version_history] == ["v1", "v2"]


def test_save_session_cas_rejects_stale_expected_version(storage):
    from cks_runtime.storage.storage import ConcurrentModificationError

    session = make_session("s1")
    session.add_version(make_version("s1", "v1"))
    storage.save_version(make_version("s1", "v1"))
    storage.save_session(session)

    # Simulate a second writer racing in and committing v2 first.
    racer = make_session("s1")
    racer.add_version(make_version("s1", "v1"))
    racer.add_version(make_version("s1", "v2"))
    storage.save_version(make_version("s1", "v2"))
    storage.save_session(racer, expected_version_id="v1")

    # Original writer, still working off v1, tries to commit v3 --
    # must be rejected rather than silently clobbering v2.
    session.add_version(make_version("s1", "v3"))
    with pytest.raises(ConcurrentModificationError):
        storage.save_session(session, expected_version_id="v1")

    # v2 must survive untouched.
    loaded = storage.load_session("s1")
    assert [v.version_id for v in loaded.version_history] == ["v1", "v2"]


def test_save_version_rejects_duplicate_version_id(storage):
    storage.save_version(make_version("s1", "v1"))
    with pytest.raises(Exception):  # sqlite3.IntegrityError
        storage.save_version(make_version("s1", "v1"))


def test_has_session(storage):
    assert not storage.has_session("s1")
    storage.save_session(make_session("s1"))
    assert storage.has_session("s1")


def test_list_sessions(storage):
    storage.save_session(make_session("s1"))
    storage.save_session(make_session("s2"))
    sessions = storage.list_sessions()
    assert len(sessions) == 2
    ids = {s.session_id for s in sessions}
    assert ids == {"s1", "s2"}


def test_save_and_load_version(storage):
    version = make_version("s1", "v1")
    storage.save_version(version)
    loaded = storage.load_version("v1")
    assert loaded is not None
    assert loaded.version_id == "v1"
    assert loaded.session_id == "s1"
    assert cks.serialize(loaded.knowledge_structure) == cks.serialize(make_ks())


def test_load_missing_version_returns_none(storage):
    assert storage.load_version("missing") is None


def test_has_version(storage):
    assert not storage.has_version("v1")
    storage.save_version(make_version("s1", "v1"))
    assert storage.has_version("v1")


def test_list_versions(storage):
    storage.save_version(make_version("s1", "v1"))
    storage.save_version(make_version("s2", "v2"))
    versions = storage.list_versions()
    assert len(versions) == 2
    vids = {v.version_id for v in versions}
    assert vids == {"v1", "v2"}


def test_clear(storage):
    storage.save_session(make_session("s1"))
    storage.save_version(make_version("s1", "v1"))
    storage.clear()
    assert not storage.has_session("s1")
    assert not storage.has_version("v1")


# ---------------------------------------------------------------------------
# _retry_on_locked
# ---------------------------------------------------------------------------

def test_retry_on_locked_succeeds_after_transient_failures():
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    assert _retry_on_locked(flaky) == "ok"
    assert attempts["n"] == 3


def test_retry_on_locked_raises_after_exhausting_retries():
    attempts = {"n": 0}

    def always_locked():
        attempts["n"] += 1
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError):
        _retry_on_locked(always_locked)
    assert attempts["n"] == 5  # _WRITE_RETRIES, all exhausted


def test_retry_on_locked_does_not_retry_unrelated_operational_error():
    attempts = {"n": 0}

    def broken_schema():
        attempts["n"] += 1
        raise sqlite3.OperationalError("no such table: ghost")

    with pytest.raises(sqlite3.OperationalError):
        _retry_on_locked(broken_schema)
    assert attempts["n"] == 1  # not a lock error -- must not retry


def test_retry_on_locked_does_not_retry_cas_rejection():
    """
    ConcurrentModificationError is a legitimate compare-and-swap
    rejection, not transient lock contention -- _retry_on_locked must
    let it through on the first attempt rather than masking a real
    conflict behind retries.
    """
    attempts = {"n": 0}

    def cas_rejected():
        attempts["n"] += 1
        raise ConcurrentModificationError("s1")

    with pytest.raises(ConcurrentModificationError):
        _retry_on_locked(cas_rejected)
    assert attempts["n"] == 1


# ---------------------------------------------------------------------------
# End-to-end: every write path survives one transient "locked" error
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "run",
    [
        pytest.param(lambda s: s.save_session(make_session("s1")), id="save_session"),
        pytest.param(lambda s: s.save_version(make_version("s1", "v1")), id="save_version"),
        pytest.param(lambda s: s.clear(), id="clear"),
        pytest.param(lambda s: s.enqueue_task("projection", "s1", "{}"), id="enqueue_task"),
        pytest.param(
            lambda s: s.save_object_embeddings("obj-1", "s1", b"\x00\x00\x80?"),
            id="save_object_embeddings",
        ),
        pytest.param(
            lambda s: s.delete_object_embeddings("obj-1", "s1"),
            id="delete_object_embeddings",
        ),
    ],
)
def test_write_path_survives_one_transient_lock(storage, run):
    storage._conn = _FlakyConnProxy(storage._conn, fail_times=1)
    run(storage)  # must not raise despite the injected transient lock
    assert storage._conn.calls >= 2  # confirms a retry actually happened


def test_complete_outbox_task_survives_transient_lock(storage):
    storage.enqueue_task("projection", "s1", "{}")
    task = storage.dequeue_next_outbox_task()
    storage._conn = _FlakyConnProxy(storage._conn, fail_times=1)
    storage.complete_outbox_task(task.task_id)
    assert storage._conn.calls >= 2


def test_fail_outbox_task_survives_transient_lock(storage):
    storage.enqueue_task("projection", "s1", "{}")
    task = storage.dequeue_next_outbox_task()
    storage._conn = _FlakyConnProxy(storage._conn, fail_times=1)
    storage.fail_outbox_task(task.task_id, 1, "boom", "2026-01-01 00:00:00")
    assert storage._conn.calls >= 2