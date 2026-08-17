"""
Regression tests for the version-reconstruction hash-mismatch bug
(Priority 1.2): ``RuntimeSession.get_version_state`` raises
``ValueError`` when a reconstructed state's hash doesn't match its
recorded ``state_hash``. Previously ``OutboxEmbeddingWorker`` treated
that exactly like any other failure: log it, schedule another
exponential-backoff retry, forever -- with no upper bound and no
attempt to recover from a stale read.

These tests cover the two things ``_reconstruct_with_retry``/
``_process_next_task`` now do about that:

1. A mismatch is retried exactly once against a freshly-reloaded
   session (clears a stale-read race without masking real corruption).
2. A task that keeps failing (mismatch persists, or any other error)
   is dead-lettered once ``max_retries`` is reached instead of
   retrying forever.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from cks_runtime.projection.outbox_worker import OutboxEmbeddingWorker
from cks_runtime.storage.storage import OutboxTask

pytestmark = pytest.mark.asyncio


def _task(**overrides) -> OutboxTask:
    base = {
        "task_id": 1,
        "task_type": "projection",
        "session_id": "s1",
        "payload": json.dumps({"previous_version_id": None, "new_version_id": "v2"}),
        "retry_count": 0,
    }
    base.update(overrides)
    return OutboxTask(**base)


def _mock_storage(task: OutboxTask | None):
    storage = MagicMock()
    storage.supports_outbox = True
    storage.dequeue_next_outbox_task = AsyncMock(return_value=task)
    storage.complete_outbox_task = AsyncMock()
    storage.fail_outbox_task = AsyncMock()
    storage.dead_letter_outbox_task = AsyncMock()
    storage.delete_object_embeddings = AsyncMock()
    storage.save_object_embeddings = AsyncMock()
    return storage


def _fake_structure(objects=()):
    structure = MagicMock()
    structure.objects = list(objects)
    structure.get = MagicMock(return_value=None)
    return structure


async def test_hash_mismatch_recovers_after_reloading_session():
    """First get_version_state call raises the hash-mismatch
    ValueError; the retry against a freshly-loaded session succeeds.
    The task must complete, not fail/retry."""
    stale_session = MagicMock()
    stale_session.version_history = []
    stale_session.get_version_state = MagicMock(
        side_effect=ValueError(
            "Reconstructed state for version 'v2' does not match its "
            "recorded hash (expected 'aaa', got 'bbb')."
        )
    )

    fresh_structure = _fake_structure()
    fresh_session = MagicMock()
    fresh_session.get_version_state = MagicMock(return_value=fresh_structure)

    task = _task()
    storage = _mock_storage(task)
    # First load_session call (in _execute_task) returns the stale
    # session; the reload inside _reconstruct_with_retry returns the
    # fresh one.
    storage.load_session = AsyncMock(side_effect=[stale_session, fresh_session])

    worker = OutboxEmbeddingWorker(storage, core_bridge=MagicMock())
    await worker._process_next_task()

    storage.complete_outbox_task.assert_awaited_once_with(1)
    storage.fail_outbox_task.assert_not_called()
    storage.dead_letter_outbox_task.assert_not_called()
    assert stale_session.get_version_state.call_count == 1
    assert fresh_session.get_version_state.call_count == 1


async def test_hash_mismatch_persists_after_reload_still_fails_the_task():
    """If the mismatch survives the reload-and-retry (genuine
    corruption, not a stale read), the task must fail normally (not
    be silently treated as success)."""
    persistent_error = ValueError(
        "Reconstructed state for version 'v2' does not match its "
        "recorded hash (expected 'aaa', got 'ccc')."
    )
    session = MagicMock()
    session.version_history = []
    session.get_version_state = MagicMock(side_effect=persistent_error)

    task = _task(retry_count=0)
    storage = _mock_storage(task)
    storage.load_session = AsyncMock(return_value=session)

    worker = OutboxEmbeddingWorker(storage, core_bridge=MagicMock(), max_retries=5)
    await worker._process_next_task()

    storage.complete_outbox_task.assert_not_called()
    storage.fail_outbox_task.assert_awaited_once()
    storage.dead_letter_outbox_task.assert_not_called()
    args, _ = storage.fail_outbox_task.call_args
    assert args[0] == 1
    assert args[1] == 1  # retry_count incremented


async def test_task_is_dead_lettered_after_max_retries_instead_of_looping_forever():
    """Regression test for the 'never gives up' bug: once retry_count
    reaches max_retries, the task must dead-letter instead of yet
    another fail_outbox_task backoff cycle."""
    session = MagicMock()
    session.version_history = []
    session.get_version_state = MagicMock(
        side_effect=ValueError("Reconstructed state ... does not match its recorded hash (...)")
    )

    task = _task(retry_count=4)  # one more failure reaches max_retries=5
    storage = _mock_storage(task)
    storage.load_session = AsyncMock(return_value=session)

    worker = OutboxEmbeddingWorker(storage, core_bridge=MagicMock(), max_retries=5)
    await worker._process_next_task()

    storage.dead_letter_outbox_task.assert_awaited_once()
    args, _ = storage.dead_letter_outbox_task.call_args
    assert args[0] == 1
    storage.fail_outbox_task.assert_not_called()
    storage.complete_outbox_task.assert_not_called()


async def test_non_hash_mismatch_value_error_is_not_reload_retried():
    """A ValueError that isn't a hash mismatch (e.g. missing version)
    must propagate straight through -- reloading the same session
    can't fix a missing-version error, so load_session must only be
    called once."""
    session = MagicMock()
    session.version_history = []
    session.get_version_state = MagicMock(
        side_effect=ValueError("Version 'v2' not found in session history.")
    )

    task = _task()
    storage = _mock_storage(task)
    storage.load_session = AsyncMock(return_value=session)

    worker = OutboxEmbeddingWorker(storage, core_bridge=MagicMock())
    await worker._process_next_task()

    assert storage.load_session.await_count == 1
    storage.fail_outbox_task.assert_awaited_once()
