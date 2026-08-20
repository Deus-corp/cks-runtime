"""
Storage conformance suite.

Runs the *same* behavioral-contract tests against every
``AsyncRuntimeStorage`` backend -- ``InMemoryStorage`` and
``SQLiteStorage`` (via ``SyncStorageAdapter``) and ``PostgresStorage``
natively -- via a single parametrized fixture. Each test function
below therefore exercises all three backends.

Why this exists
----------------
Before this file, each backend had its own hand-written, independently
maintained test file (``test_memory_storage.py``,
``test_sqlite_storage.py``, ``test_postgres_storage.py``). That let
backend-specific bugs slip through undetected in two ways:

1. A scenario simply never got written for one backend's file, so
   that backend's behavior for it was never checked at all.
2. Even when a scenario *was* written for all three, subtle wording
   or setup differences between the copies meant they could quietly
   drift out of sync with each other over time.

Concretely: ``PostgresStorage.record_operations()`` called
``conn.executemany(...)`` directly, but psycopg3's ``AsyncConnection``
has no such method -- only ``AsyncCursor`` does -- so every call
raised ``AttributeError`` against a real driver. It shipped because
the entire ``test_postgres_storage.py`` file is skipped whenever
``CKS_TEST_POSTGRES_DSN`` isn't set, which is the common case for
anyone running ``pytest`` without a Postgres instance handy.

This suite doesn't fix that skip condition on its own -- Postgres
still needs a real DSN to be exercised, and the ``postgres`` param is
still skipped without one. What it changes is *scope*: the exact same
``test_record_operations_round_trips_when_supported`` below runs
against SQLite unconditionally (no DSN needed) and against Postgres
whenever a DSN is available, instead of the two backends having their
own separately-written (and therefore separately-driftable) copies of
"does record_operations round-trip". A bug specific to one backend's
implementation of a shared method is now equally visible from
whichever backend happens to be easiest to run in a given
environment.

This file is a *supplement* to the existing per-backend test files,
not a replacement -- it only covers the shared contract documented on
``RuntimeStorage``/``AsyncRuntimeStorage``. Backend-specific mechanics
(SQLite's retry-on-locked behavior, Postgres's outbox
``FOR UPDATE SKIP LOCKED`` claiming, pgvector similarity search
ranking, etc.) still belong in each backend's own file, since those
behaviors have no equivalent to compare against on other backends.
"""

from __future__ import annotations

import os

import cks
import pytest
import pytest_asyncio

from cks_runtime.core_api.field_operation import RuntimeFieldOperation
from cks_runtime.session.session import RuntimeSession
from cks_runtime.storage.adapter import SyncStorageAdapter
from cks_runtime.storage.memory_storage import InMemoryStorage
from cks_runtime.storage.sqlite_storage import SQLiteStorage
from cks_runtime.storage.storage import ConcurrentModificationError
from cks_runtime.versioning.version import RuntimeVersion

try:
    from cks_runtime.storage.postgres_storage import PostgresStorage
    _PSYCOPG_AVAILABLE = True
except ImportError:
    PostgresStorage = None  # type: ignore[assignment,misc]
    _PSYCOPG_AVAILABLE = False

_PG_DSN = os.environ.get("CKS_TEST_POSTGRES_DSN")

pytestmark = pytest.mark.asyncio

BACKENDS = [
    "memory",
    "sqlite",
    pytest.param(
        "postgres",
        marks=pytest.mark.skipif(
            not _PG_DSN or not _PSYCOPG_AVAILABLE,
            reason="CKS_TEST_POSTGRES_DSN not set or psycopg not installed",
        ),
    ),
]


@pytest_asyncio.fixture(params=BACKENDS)
async def storage(request):
    """
    Yields a fresh, empty ``AsyncRuntimeStorage`` for one backend.

    Parametrized over ``BACKENDS``, so every test that takes
    ``storage`` as an argument runs once per backend automatically --
    pytest reports each as a separate test case
    (``test_foo[memory]``, ``test_foo[sqlite]``, ``test_foo[postgres]``).
    """
    backend = request.param
    if backend == "memory":
        yield SyncStorageAdapter(InMemoryStorage())
    elif backend == "sqlite":
        yield SyncStorageAdapter(SQLiteStorage(":memory:"))
    elif backend == "postgres":
        store = await PostgresStorage.connect(_PG_DSN, min_size=1, max_size=4)
        await store.clear()
        yield store
        await store.clear()
        await store.close()
    else:  # pragma: no cover - guards against a BACKENDS/branch typo
        raise AssertionError(f"unhandled backend param: {backend!r}")


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def make_ks():
    """Minimal valid knowledge structure for testing."""
    return cks.parse(
        '{"objects":[{"identity":{"id":"obj-1","type":"Test","name":"t"},"structure":{}}]}'
    )


def make_session(session_id: str = "s1") -> RuntimeSession:
    return RuntimeSession(knowledge_structure=make_ks(), session_id=session_id)


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


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


async def test_save_and_load_session_round_trips(storage):
    session = make_session("s1")
    await storage.save_session(session)

    loaded = await storage.load_session("s1")

    assert loaded is not None
    assert loaded.session_id == "s1"


async def test_load_missing_session_returns_none(storage):
    assert await storage.load_session("does-not-exist") is None


async def test_has_session(storage):
    assert await storage.has_session("s1") is False

    await storage.save_session(make_session("s1"))

    assert await storage.has_session("s1") is True


async def test_list_sessions(storage):
    await storage.save_session(make_session("s1"))
    await storage.save_session(make_session("s2"))

    ids = {s.session_id for s in await storage.list_sessions()}

    assert ids == {"s1", "s2"}


async def test_load_session_returns_an_isolated_copy(storage):
    """
    Mutating the object handed back by load_session must never affect
    what's actually persisted -- every load must return an
    independent copy, not a reference into backend-internal state.
    """
    await storage.save_session(make_session("s1"))

    first = await storage.load_session("s1")
    first.metadata["mutated"] = True

    second = await storage.load_session("s1")

    assert "mutated" not in second.metadata


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


async def test_save_and_load_version_round_trips(storage):
    await storage.save_version(make_version("s1", "v1"))

    loaded = await storage.load_version("v1")

    assert loaded is not None
    assert loaded.version_id == "v1"
    assert loaded.session_id == "s1"


async def test_load_missing_version_returns_none(storage):
    assert await storage.load_version("does-not-exist") is None


async def test_has_version(storage):
    assert await storage.has_version("v1") is False

    await storage.save_version(make_version("s1", "v1"))

    assert await storage.has_version("v1") is True


async def test_list_versions(storage):
    await storage.save_version(make_version("s1", "v1"))
    await storage.save_version(make_version("s1", "v2"))

    ids = {v.version_id for v in await storage.list_versions()}

    assert ids == {"v1", "v2"}


# ---------------------------------------------------------------------------
# Compare-and-swap (save_session's expected_version_id)
# ---------------------------------------------------------------------------


async def test_cas_accepts_matching_expected_version(storage):
    session = make_session("s1")
    session.add_version(make_version("s1", "v1"))
    await storage.save_version(make_version("s1", "v1"))
    await storage.save_session(session)  # initial write, unconditional

    session.add_version(make_version("s1", "v2"))
    await storage.save_version(make_version("s1", "v2"))
    await storage.save_session(session, expected_version_id="v1")

    loaded = await storage.load_session("s1")
    assert [v.version_id for v in loaded.version_history] == ["v1", "v2"]


async def test_cas_rejects_stale_expected_version(storage):
    session = make_session("s1")
    session.add_version(make_version("s1", "v1"))
    await storage.save_version(make_version("s1", "v1"))
    await storage.save_session(session)

    # A second writer races in and commits v2 first.
    racer = make_session("s1")
    racer.add_version(make_version("s1", "v1"))
    racer.add_version(make_version("s1", "v2"))
    await storage.save_version(make_version("s1", "v2"))
    await storage.save_session(racer, expected_version_id="v1")

    # The original writer, still working off v1, tries to commit v3 --
    # must be rejected rather than silently clobbering v2.
    session.add_version(make_version("s1", "v3"))
    with pytest.raises(ConcurrentModificationError):
        await storage.save_session(session, expected_version_id="v1")

    # v2 must survive untouched.
    loaded = await storage.load_session("s1")
    assert [v.version_id for v in loaded.version_history] == ["v1", "v2"]


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------


async def test_clear_removes_everything(storage):
    await storage.save_session(make_session("s1"))
    await storage.save_version(make_version("s1", "v1"))

    await storage.clear()

    assert await storage.has_session("s1") is False
    assert await storage.has_version("v1") is False


# ---------------------------------------------------------------------------
# Operation log (ADR-007) -- optional capability, gated by
# supports_operation_log. InMemoryStorage doesn't implement it and is
# expected to report False; the round-trip test is skipped for it
# rather than asserting behavior it never promised.
# ---------------------------------------------------------------------------


async def test_record_operations_empty_list_never_raises(storage):
    """Contract holds regardless of capability -- must be a safe no-op."""
    await storage.record_operations("s1", "v1", [])


async def test_record_operations_round_trips_when_supported(storage):
    if not storage.supports_operation_log:
        pytest.skip(f"{type(storage).__name__} does not support the operation log")

    ops = [
        RuntimeFieldOperation(
            object_id="obj-1",
            op_type="set_field",
            field_key="name",
            field_value="Alpha",
        ),
    ]

    await storage.record_operations("s1", "v1", ops)
    logged = await storage.list_operations("s1")

    assert len(logged) == 1
    assert logged[0].object_id == "obj-1"
    assert logged[0].field_value == "Alpha"


async def test_list_operations_filters_by_object_id_when_supported(storage):
    if not storage.supports_operation_log:
        pytest.skip(f"{type(storage).__name__} does not support the operation log")

    ops = [
        RuntimeFieldOperation(
            object_id="obj-1", op_type="set_field", field_key="a", field_value=1
        ),
        RuntimeFieldOperation(
            object_id="obj-2", op_type="set_field", field_key="b", field_value=2
        ),
    ]
    await storage.record_operations("s1", "v1", ops)

    logged = await storage.list_operations("s1", object_id="obj-1")

    assert len(logged) == 1
    assert logged[0].object_id == "obj-1"


# ---------------------------------------------------------------------------
# Outbox -- optional capability, gated by supports_outbox.
# InMemoryStorage doesn't implement it and is expected to report
# False; the round-trip test is skipped for it accordingly.
# ---------------------------------------------------------------------------


async def test_outbox_round_trips_when_supported(storage):
    if not storage.supports_outbox:
        pytest.skip(f"{type(storage).__name__} does not support the outbox")

    await storage.enqueue_outbox_task("s1", None, "v1")

    task = await storage.dequeue_next_outbox_task()
    assert task is not None
    assert task.session_id == "s1"

    await storage.complete_outbox_task(task.task_id)

    # A completed task must not be handed out again.
    assert await storage.dequeue_next_outbox_task() is None


# ---------------------------------------------------------------------------
# task_type filter, dead-lettering, and batch listing -- added alongside
# the Critic-agent outbox work. Same supports_outbox gating as above.
# ---------------------------------------------------------------------------


async def test_dequeue_respects_task_type_filter(storage):
    if not storage.supports_outbox:
        pytest.skip(f"{type(storage).__name__} does not support the outbox")

    await storage.enqueue_task("gossip_conflict", "s1", "{}")
    await storage.enqueue_task("projection", "s1", "{}")

    # A worker dedicated to "projection" must never claim the
    # "gossip_conflict" task meant for a different worker.
    task = await storage.dequeue_next_outbox_task(task_type="projection")
    assert task is not None
    assert task.task_type == "projection"

    # The gossip_conflict task is untouched and still claimable by its
    # own worker.
    remaining = await storage.dequeue_next_outbox_task(task_type="gossip_conflict")
    assert remaining is not None
    assert remaining.task_type == "gossip_conflict"


async def test_dequeue_with_task_type_ignores_other_types_even_when_older(storage):
    if not storage.supports_outbox:
        pytest.skip(f"{type(storage).__name__} does not support the outbox")

    await storage.enqueue_task("projection", "s1", "{}")  # enqueued first
    await storage.enqueue_task("gossip_conflict", "s1", "{}")  # enqueued second

    # Ordering only applies *within* a type -- a type-scoped dequeue must
    # not fall back to an older task of a different type.
    task = await storage.dequeue_next_outbox_task(task_type="gossip_conflict")
    assert task is not None
    assert task.task_type == "gossip_conflict"


async def test_dead_letter_task_is_never_dequeued_again(storage):
    if not storage.supports_outbox:
        pytest.skip(f"{type(storage).__name__} does not support the outbox")

    await storage.enqueue_task("inference_conflict", "s1", "{}")
    task = await storage.dequeue_next_outbox_task()
    assert task is not None

    await storage.dead_letter_outbox_task(task.task_id, "gave up after 5 retries")

    # Unlike fail_outbox_task, dead-lettering must not schedule a retry --
    # the task is gone from the eligible pool for good.
    assert await storage.dequeue_next_outbox_task() is None


async def test_dead_letter_task_appears_in_list_dead_letter_tasks(storage):
    if not storage.supports_outbox:
        pytest.skip(f"{type(storage).__name__} does not support the outbox")

    await storage.enqueue_task("inference_conflict", "s1", "{}")
    task = await storage.dequeue_next_outbox_task()
    assert task is not None
    await storage.dead_letter_outbox_task(task.task_id, "boom")

    dead = await storage.list_dead_letter_tasks()
    assert len(dead) == 1
    assert dead[0].task_id == task.task_id
    assert dead[0].task_type == "inference_conflict"


async def test_list_dead_letter_tasks_filters_by_task_type(storage):
    if not storage.supports_outbox:
        pytest.skip(f"{type(storage).__name__} does not support the outbox")

    await storage.enqueue_task("gossip_conflict", "s1", "{}")
    await storage.enqueue_task("inference_conflict", "s1", "{}")
    gossip_task = await storage.dequeue_next_outbox_task(task_type="gossip_conflict")
    inference_task = await storage.dequeue_next_outbox_task(task_type="inference_conflict")
    assert gossip_task is not None and inference_task is not None
    await storage.dead_letter_outbox_task(gossip_task.task_id, "boom")
    await storage.dead_letter_outbox_task(inference_task.task_id, "boom")

    only_gossip = await storage.list_dead_letter_tasks(task_type="gossip_conflict")
    assert len(only_gossip) == 1
    assert only_gossip[0].task_id == gossip_task.task_id


async def test_list_dead_letter_tasks_never_drains(storage):
    if not storage.supports_outbox:
        pytest.skip(f"{type(storage).__name__} does not support the outbox")

    await storage.enqueue_task("gossip_conflict", "s1", "{}")
    task = await storage.dequeue_next_outbox_task()
    assert task is not None
    await storage.dead_letter_outbox_task(task.task_id, "boom")

    first_read = await storage.list_dead_letter_tasks()
    second_read = await storage.list_dead_letter_tasks()
    assert len(first_read) == len(second_read) == 1


async def test_retry_dead_letter_task_requeues_dead_task(storage):
    if not storage.supports_outbox:
        pytest.skip(f"{type(storage).__name__} does not support the outbox")

    await storage.enqueue_task("gossip_conflict", "s1", "{}")
    task = await storage.dequeue_next_outbox_task()
    assert task is not None
    await storage.dead_letter_outbox_task(task.task_id, "boom")

    assert await storage.retry_dead_letter_task(task.task_id) is True

    # No longer DEAD, so it must drop out of list_dead_letter_tasks...
    assert await storage.list_dead_letter_tasks() == []


async def test_retry_dead_letter_task_missing_task_returns_false(storage):
    if not storage.supports_outbox:
        pytest.skip(f"{type(storage).__name__} does not support the outbox")

    assert await storage.retry_dead_letter_task(999999) is False


async def test_retry_dead_letter_task_refuses_non_dead_task(storage):
    if not storage.supports_outbox:
        pytest.skip(f"{type(storage).__name__} does not support the outbox")

    await storage.enqueue_task("gossip_conflict", "s1", "{}")
    pending_task = await storage.dequeue_next_outbox_task()
    assert pending_task is not None

    # The task is now IN_PROGRESS (claimed above), not DEAD -- must be
    # refused rather than silently requeued out from under whoever
    # holds the lease.
    assert await storage.retry_dead_letter_task(pending_task.task_id) is False


async def test_retry_dead_letter_task_can_be_claimed_again(storage):
    if not storage.supports_outbox:
        pytest.skip(f"{type(storage).__name__} does not support the outbox")

    await storage.enqueue_task("gossip_conflict", "s1", "{}")
    task = await storage.dequeue_next_outbox_task()
    assert task is not None
    await storage.dead_letter_outbox_task(task.task_id, "boom")

    assert await storage.retry_dead_letter_task(task.task_id) is True

    reclaimed = await storage.dequeue_next_outbox_task()
    assert reclaimed is not None
    assert reclaimed.task_id == task.task_id
    assert reclaimed.task_type == "gossip_conflict"
    assert reclaimed.session_id == "s1"


async def test_dead_letter_and_list_methods_no_op_when_outbox_unsupported_retry(storage):
    if storage.supports_outbox:
        pytest.skip(f"{type(storage).__name__} supports the outbox")

    assert await storage.retry_dead_letter_task(999) is False


async def test_list_tasks_by_type_returns_only_matching_pending_tasks(storage):
    if not storage.supports_outbox:
        pytest.skip(f"{type(storage).__name__} does not support the outbox")

    await storage.enqueue_task("gossip_conflict", "s1", "{}")
    await storage.enqueue_task("gossip_conflict", "s1", "{}")
    await storage.enqueue_task("projection", "s1", "{}")

    tasks = await storage.list_tasks_by_type("gossip_conflict", drain=False)
    assert len(tasks) == 2
    assert all(t.task_type == "gossip_conflict" for t in tasks)


async def test_list_tasks_by_type_drains_by_default(storage):
    if not storage.supports_outbox:
        pytest.skip(f"{type(storage).__name__} does not support the outbox")

    await storage.enqueue_task("gossip_conflict", "s1", "{}")

    first = await storage.list_tasks_by_type("gossip_conflict")
    assert len(first) == 1

    second = await storage.list_tasks_by_type("gossip_conflict")
    assert second == []


async def test_list_tasks_by_type_peek_does_not_drain(storage):
    if not storage.supports_outbox:
        pytest.skip(f"{type(storage).__name__} does not support the outbox")

    await storage.enqueue_task("gossip_conflict", "s1", "{}")

    first_peek = await storage.list_tasks_by_type("gossip_conflict", drain=False)
    second_peek = await storage.list_tasks_by_type("gossip_conflict", drain=False)
    assert len(first_peek) == len(second_peek) == 1
    assert first_peek[0].task_id == second_peek[0].task_id


async def test_list_tasks_by_type_filters_by_session_id(storage):
    if not storage.supports_outbox:
        pytest.skip(f"{type(storage).__name__} does not support the outbox")

    await storage.enqueue_task("gossip_conflict", "s1", "{}")
    await storage.enqueue_task("gossip_conflict", "s2", "{}")

    tasks = await storage.list_tasks_by_type("gossip_conflict", session_id="s1")
    assert len(tasks) == 1
    assert tasks[0].session_id == "s1"


async def test_list_tasks_by_type_excludes_claimed_in_progress_tasks(storage):
    if not storage.supports_outbox:
        pytest.skip(f"{type(storage).__name__} does not support the outbox")

    await storage.enqueue_task("gossip_conflict", "s1", "{}")
    # Claim it via the single-task path -- it's now IN_PROGRESS, not
    # PENDING, so a batch reader must not also hand it to another worker.
    claimed = await storage.dequeue_next_outbox_task(task_type="gossip_conflict")
    assert claimed is not None

    tasks = await storage.list_tasks_by_type("gossip_conflict")
    assert tasks == []


async def test_dead_letter_and_list_methods_no_op_when_outbox_unsupported(storage):
    if storage.supports_outbox:
        pytest.skip(f"{type(storage).__name__} supports the outbox -- covered above")

    # Must behave as documented no-ops rather than raising, even though
    # there is nothing to act on.
    await storage.dead_letter_outbox_task(999, "n/a")
    assert await storage.list_tasks_by_type("gossip_conflict") == []
    assert await storage.list_dead_letter_tasks() == []