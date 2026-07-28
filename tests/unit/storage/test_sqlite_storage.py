"""
Tests for SQLiteStorage (JSON-based).
"""

from __future__ import annotations

import sqlite3
import threading

import cks
import numpy as np
import pytest

from cks_runtime.core_api.field_operation import RuntimeFieldOperation
from cks_runtime.session.session import RuntimeSession
from cks_runtime.storage.sqlite_storage import SQLiteStorage, _retry_on_locked
from cks_runtime.storage.storage import ConcurrentModificationError
from cks_runtime.versioning.version import RuntimeVersion


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

    def executemany(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise sqlite3.OperationalError("database is locked")
        return self._real.executemany(*args, **kwargs)

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
    with pytest.raises(sqlite3.IntegrityError):  # sqlite3.IntegrityError
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


def test_dequeue_claims_task_so_it_is_not_returned_again(storage):
    """
    dequeue_next_outbox_task must atomically claim the task (moving it
    out of PENDING), not just read it -- otherwise two workers polling
    the same table would both pick up the same task and double-process
    it.
    """
    storage.enqueue_task("projection", "s1", "{}")
    first = storage.dequeue_next_outbox_task()
    assert first is not None
    second = storage.dequeue_next_outbox_task()
    assert second is None, "the same task must not be claimable twice"


def test_dequeue_sets_status_and_claimed_at(storage):
    storage.enqueue_task("projection", "s1", "{}")
    task = storage.dequeue_next_outbox_task()
    row = storage._conn.execute(
        "SELECT status, claimed_at FROM cks_outbox_tasks WHERE task_id = ?",
        (task.task_id,),
    ).fetchone()
    assert row[0] == "IN_PROGRESS"
    assert row[1] is not None


def test_fail_outbox_task_clears_claim_and_is_reclaimable(storage):
    storage.enqueue_task("projection", "s1", "{}")
    task = storage.dequeue_next_outbox_task()
    storage.fail_outbox_task(task.task_id, 1, "boom", "2020-01-01 00:00:00")
    row = storage._conn.execute(
        "SELECT status, claimed_at FROM cks_outbox_tasks WHERE task_id = ?",
        (task.task_id,),
    ).fetchone()
    assert row == ("PENDING", None)
    retried = storage.dequeue_next_outbox_task()
    assert retried is not None and retried.task_id == task.task_id


def test_stale_in_progress_claim_is_reclaimed(storage):
    """A worker that claimed a task and then crashed/hung without
    calling complete/fail must not strand the task forever -- once the
    lease goes stale, another dequeue call should pick it back up."""
    storage.enqueue_task("projection", "s1", "{}")
    task = storage.dequeue_next_outbox_task()
    storage._conn.execute(
        "UPDATE cks_outbox_tasks SET claimed_at = datetime('now', '-10 minutes') WHERE task_id = ?",
        (task.task_id,),
    )
    storage._conn.commit()
    reclaimed = storage.dequeue_next_outbox_task()
    assert reclaimed is not None and reclaimed.task_id == task.task_id


def test_fresh_in_progress_claim_is_not_reclaimed(storage):
    storage.enqueue_task("projection", "s1", "{}")
    storage.dequeue_next_outbox_task()
    assert storage.dequeue_next_outbox_task() is None


def test_dequeue_never_double_claims_under_real_concurrency(tmp_path):
    """
    End-to-end regression test using real threads, each with its own
    connection to the same file-backed database -- the actual scenario
    the claim mechanism protects against. A weaker single-threaded
    test could pass even with a plain SELECT (no atomic UPDATE) if the
    two dequeue calls merely happen to be sequential; this test
    exercises genuine concurrent access.
    """
    db_path = str(tmp_path / "outbox_concurrency.db")
    setup_storage = SQLiteStorage(db_path)
    task_count = 20
    for i in range(task_count):
        setup_storage.enqueue_task("projection", f"s{i}", "{}")

    claimed_task_ids: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        worker_storage = SQLiteStorage(db_path)
        while True:
            task = worker_storage.dequeue_next_outbox_task()
            if task is None:
                break
            with lock:
                claimed_task_ids.append(task.task_id)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(claimed_task_ids) == task_count
    assert len(set(claimed_task_ids)) == task_count, "a task_id was claimed by more than one worker"


# ---------------------------------------------------------------------------
# Operation log (ADR-007)
# ---------------------------------------------------------------------------

def test_supports_operation_log(storage):
    assert storage.supports_operation_log is True


def test_record_operations_then_list_operations_round_trips(storage):
    ops = [
        RuntimeFieldOperation(object_id="obj-1", op_type="remove_object"),
        RuntimeFieldOperation(object_id="obj-2", op_type="add_object"),
        RuntimeFieldOperation(
            object_id="obj-3", op_type="set_field", field_key="color", field_value="blue"
        ),
    ]
    storage.record_operations("s1", "v1", ops)

    expected = [
        RuntimeFieldOperation(object_id="obj-1", op_type="remove_object", version_id="v1"),
        RuntimeFieldOperation(object_id="obj-2", op_type="add_object", version_id="v1"),
        RuntimeFieldOperation(
            object_id="obj-3", op_type="set_field", field_key="color", field_value="blue", version_id="v1"
        ),
    ]
    assert storage.list_operations("s1") == expected


def test_record_operations_preserves_none_field_value_as_a_deletion():
    """
    field_value=None on a set_field op means "this key was removed",
    distinct from the op simply carrying no field_value at all (e.g.
    add_object) -- both must round-trip as None, not collapse into
    each other or into a JSON string "null".
    """
    storage = SQLiteStorage(":memory:")
    op = RuntimeFieldOperation(
        object_id="obj-1", op_type="set_field", field_key="color", field_value=None
    )
    storage.record_operations("s1", "v1", [op])

    expected = RuntimeFieldOperation(
        object_id="obj-1", op_type="set_field", field_key="color", field_value=None, version_id="v1"
    )
    assert storage.list_operations("s1") == [expected]


def test_list_operations_filters_by_object_id(storage):
    storage.record_operations(
        "s1",
        "v1",
        [
            RuntimeFieldOperation(object_id="obj-1", op_type="add_object"),
            RuntimeFieldOperation(object_id="obj-2", op_type="add_object"),
        ],
    )

    assert storage.list_operations("s1", object_id="obj-1") == [
        RuntimeFieldOperation(object_id="obj-1", op_type="add_object", version_id="v1")
    ]


def test_list_operations_returns_empty_for_unknown_session(storage):
    assert storage.list_operations("nonexistent-session") == []


def test_record_operations_with_empty_list_is_a_no_op(storage):
    storage.record_operations("s1", "v1", [])
    assert storage.list_operations("s1") == []


def test_record_operations_survives_one_transient_lock(storage):
    storage._conn = _FlakyConnProxy(storage._conn, fail_times=1)
    ops = [RuntimeFieldOperation(object_id="obj-1", op_type="add_object", version_id="v1")]
    storage.record_operations("s1", "v1", ops)
    assert storage._conn.calls >= 2
    assert storage.list_operations("s1") == ops


# ---------------------------------------------------------------------------
# search_embeddings
# ---------------------------------------------------------------------------

def _vec(*components: float) -> bytes:
    """Pack floats as the little-endian float32 blob search_embeddings expects."""
    return np.array(components, dtype=np.float32).tobytes()


def test_search_embeddings_ranks_by_similarity(storage):
    query = _vec(1.0, 0.0)
    storage.save_object_embeddings("close", "s1", _vec(0.9, 0.436))   # near-identical
    storage.save_object_embeddings("far", "s1", _vec(0.436, 0.9))     # near-orthogonal

    results = storage.search_embeddings(query, "s1", top_k=5)

    assert [oid for oid, _ in results] == ["close", "far"]
    close_score, far_score = results[0][1], results[1][1]
    assert close_score > far_score
    assert 0.0 <= far_score <= close_score <= 1.0


def test_search_embeddings_respects_top_k(storage):
    query = _vec(1.0, 0.0)
    for i in range(5):
        storage.save_object_embeddings(f"obj-{i}", "s1", _vec(1.0, 0.0))

    assert len(storage.search_embeddings(query, "s1", top_k=2)) == 2
    assert len(storage.search_embeddings(query, "s1", top_k=100)) == 5


def test_search_embeddings_is_scoped_to_session(storage):
    query = _vec(1.0, 0.0)
    storage.save_object_embeddings("in-session", "s1", _vec(1.0, 0.0))
    storage.save_object_embeddings("other-session", "s2", _vec(1.0, 0.0))

    results = storage.search_embeddings(query, "s1", top_k=5)

    assert [oid for oid, _ in results] == ["in-session"]


def test_search_embeddings_empty_session_returns_empty_list(storage):
    assert storage.search_embeddings(_vec(1.0, 0.0), "no-such-session", top_k=5) == []


def test_search_embeddings_skips_dimension_mismatches(storage):
    query = _vec(1.0, 0.0, 0.0)
    storage.save_object_embeddings("same-dim", "s1", _vec(1.0, 0.0, 0.0))
    # Indexed by a different embedding model/provider -- wrong dimensionality.
    storage.save_object_embeddings("wrong-dim", "s1", _vec(1.0, 0.0))

    results = storage.search_embeddings(query, "s1", top_k=5)

    assert [oid for oid, _ in results] == ["same-dim"]


def test_search_embeddings_clamps_negative_similarity_to_zero(storage):
    query = _vec(1.0, 0.0)
    storage.save_object_embeddings("opposite", "s1", _vec(-1.0, 0.0))

    results = storage.search_embeddings(query, "s1", top_k=5)

    assert results == [("opposite", 0.0)]


def test_object_embeddings_session_index_exists(storage):
    """
    Regression guard for the session_id index: search_embeddings'
    WHERE session_id = ? query (and delete_object_embeddings') should
    never fall back to a full table scan.
    """
    rows = storage._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='cks_object_embeddings'"
    ).fetchall()
    assert ("idx_object_embeddings_session",) in rows