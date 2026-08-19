import cks
import pytest

from cks_runtime.adapters.cks_core import CksCoreAdapter
from cks_runtime.operations.operation_types import ValidateOperation
from cks_runtime.projection.outbox_worker import OutboxEmbeddingWorker
from cks_runtime.runtime import Runtime
from cks_runtime.storage.sqlite_storage import SQLiteStorage


@pytest.mark.asyncio
async def test_version_created_adds_outbox_task():
    """When a version is created, the outbox should contain a new task."""
    storage = SQLiteStorage(":memory:")
    runtime = Runtime(core=CksCoreAdapter(), storage=storage)

    ks = cks.parse(
        '{"objects":[{"identity":{"id":"obj-1","type":"Test","name":"t"},"structure":{}}]}'
    )
    session = await runtime.create_session(ks)
    tx = runtime.begin_transaction(session)
    tx.add_operation(ValidateOperation("v1", knowledge_structure=ks))
    await runtime.commit_transaction(tx)

    # Check outbox table
    rows = storage._conn.execute(
        "SELECT * FROM cks_outbox_tasks"
    ).fetchall()
    assert len(rows) == 1
    # Indices: 1=task_type, 2=session_id, 4=status
    assert rows[0][1] == "projection"
    assert rows[0][2] == session.session_id
    assert rows[0][4] == "PENDING"


@pytest.mark.asyncio
async def test_worker_ignores_tasks_of_other_types():
    """
    OutboxEmbeddingWorker must only ever claim "projection" tasks --
    now that the outbox table is shared with other consumers (e.g. a
    Critic agent's "gossip_conflict"/"inference_conflict" tasks, see
    cks-runtime 1.34.0's task_type-filtered dequeue_next_outbox_task),
    it must never dequeue-and-fail on a task meant for someone else.
    """
    storage = SQLiteStorage(":memory:")
    storage.enqueue_task("gossip_conflict", "s1", "{}")
    runtime = Runtime(core=CksCoreAdapter(), storage=storage)
    worker = OutboxEmbeddingWorker(runtime.storage, core_bridge=CksCoreAdapter())

    await worker._process_next_task()

    # The foreign task must still be sitting there, PENDING and
    # untouched -- not claimed, not failed, not retried.
    row = storage._conn.execute(
        "SELECT task_type, status, retry_count FROM cks_outbox_tasks"
    ).fetchone()
    assert row == ("gossip_conflict", "PENDING", 0)


@pytest.mark.asyncio
async def test_worker_processes_its_own_projection_task_even_with_other_types_queued():
    """A "gossip_conflict" task enqueued earlier must not block the
    worker from reaching its own "projection" task behind it."""
    storage = SQLiteStorage(":memory:")
    runtime = Runtime(core=CksCoreAdapter(), storage=storage)

    storage.enqueue_task("gossip_conflict", "s0", "{}")

    ks = cks.parse(
        '{"objects":[{"identity":{"id":"obj-1","type":"Test","name":"t"},"structure":{}}]}'
    )
    session = await runtime.create_session(ks)
    tx = runtime.begin_transaction(session)
    tx.add_operation(ValidateOperation("v1", knowledge_structure=ks))
    await runtime.commit_transaction(tx)

    worker = OutboxEmbeddingWorker(runtime.storage, core_bridge=CksCoreAdapter())
    await worker._process_next_task()

    remaining = storage._conn.execute(
        "SELECT task_type, status FROM cks_outbox_tasks ORDER BY task_id"
    ).fetchall()
    # The gossip_conflict task is untouched; the projection task is gone
    # (completed -- completion deletes the row).
    assert remaining == [("gossip_conflict", "PENDING")]