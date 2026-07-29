"""
Sync -> async storage adapter.

``Runtime`` is async end-to-end (see ``runtime.py``) and always talks
to an ``AsyncRuntimeStorage``. ``InMemoryStorage`` and ``SQLiteStorage``
implement the synchronous ``RuntimeStorage`` instead -- rewriting
either as async-native would duplicate already-well-tested logic for
no real benefit (``InMemoryStorage`` has no I/O to speak of, and
``SQLiteStorage``'s blocking calls are exactly the kind of thing
``asyncio.to_thread`` exists to wrap).

``SyncStorageAdapter`` bridges the two: every call is dispatched to a
worker thread via ``asyncio.to_thread`` rather than called inline, so
a synchronous backend genuinely never blocks the event loop -- this is
not just a type-signature shim to satisfy ``AsyncRuntimeStorage``.

``Runtime`` applies this adapter automatically to any ``RuntimeStorage``
it's given (including the default ``InMemoryStorage``/``SQLiteStorage``
it constructs itself); a caller that already has an
``AsyncRuntimeStorage`` (``PostgresStorage``) passes it straight
through unwrapped.
"""

from __future__ import annotations

import asyncio

from cks_runtime.core_api.field_operation import RuntimeFieldOperation
from cks_runtime.session.session import RuntimeSession
from cks_runtime.storage.async_storage import AsyncRuntimeStorage
from cks_runtime.storage.storage import OutboxTask, RuntimeStorage
from cks_runtime.versioning.version import RuntimeVersion


class SyncStorageAdapter(AsyncRuntimeStorage):
    """Exposes a synchronous ``RuntimeStorage`` through the async interface."""

    def __init__(self, sync_storage: RuntimeStorage) -> None:
        self._sync = sync_storage

    @property
    def wrapped(self) -> RuntimeStorage:
        """The underlying synchronous storage, for callers that need it directly."""
        return self._sync

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    async def save_session(
        self,
        session: RuntimeSession,
        expected_version_id: str | None = None,
    ) -> None:
        await asyncio.to_thread(self._sync.save_session, session, expected_version_id)

    async def load_session(self, session_id: str) -> RuntimeSession | None:
        return await asyncio.to_thread(self._sync.load_session, session_id)

    async def has_session(self, session_id: str) -> bool:
        return await asyncio.to_thread(self._sync.has_session, session_id)

    async def list_sessions(self) -> tuple[RuntimeSession, ...]:
        return await asyncio.to_thread(self._sync.list_sessions)

    # ------------------------------------------------------------------
    # Versions
    # ------------------------------------------------------------------

    async def save_version(self, version: RuntimeVersion) -> None:
        await asyncio.to_thread(self._sync.save_version, version)

    async def load_version(self, version_id: str) -> RuntimeVersion | None:
        return await asyncio.to_thread(self._sync.load_version, version_id)

    async def has_version(self, version_id: str) -> bool:
        return await asyncio.to_thread(self._sync.has_version, version_id)

    async def list_versions(self) -> tuple[RuntimeVersion, ...]:
        return await asyncio.to_thread(self._sync.list_versions)

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    async def clear(self) -> None:
        await asyncio.to_thread(self._sync.clear)

    # ------------------------------------------------------------------
    # Outbox
    # ------------------------------------------------------------------

    async def enqueue_outbox_task(
        self,
        session_id: str,
        previous_version_id: str | None,
        new_version_id: str,
    ) -> None:
        await asyncio.to_thread(
            self._sync.enqueue_outbox_task, session_id, previous_version_id, new_version_id
        )

    async def enqueue_task(
        self,
        task_type: str,
        session_id: str,
        payload: str,
    ) -> None:
        await asyncio.to_thread(self._sync.enqueue_task, task_type, session_id, payload)

    async def dequeue_next_outbox_task(self) -> OutboxTask | None:
        return await asyncio.to_thread(self._sync.dequeue_next_outbox_task)

    async def complete_outbox_task(self, task_id: int) -> None:
        await asyncio.to_thread(self._sync.complete_outbox_task, task_id)

    async def fail_outbox_task(
        self, task_id: int, retry_count: int, error: str, next_retry_at: str
    ) -> None:
        await asyncio.to_thread(
            self._sync.fail_outbox_task, task_id, retry_count, error, next_retry_at
        )

    @property
    def supports_outbox(self) -> bool:
        return self._sync.supports_outbox

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------

    async def save_object_embeddings(
        self, object_id: str, session_id: str, embedding: bytes
    ) -> None:
        await asyncio.to_thread(
            self._sync.save_object_embeddings, object_id, session_id, embedding
        )

    async def delete_object_embeddings(self, object_id: str, session_id: str) -> None:
        await asyncio.to_thread(self._sync.delete_object_embeddings, object_id, session_id)

    # ------------------------------------------------------------------
    # Operation log (ADR-007)
    # ------------------------------------------------------------------

    async def record_operations(
        self,
        session_id: str,
        version_id: str,
        operations: list[RuntimeFieldOperation],
    ) -> None:
        await asyncio.to_thread(
            self._sync.record_operations, session_id, version_id, operations
        )

    @property
    def supports_operation_log(self) -> bool:
        return self._sync.supports_operation_log

    async def list_operations(
        self,
        session_id: str,
        object_id: str | None = None,
    ) -> list[RuntimeFieldOperation]:
        return await asyncio.to_thread(self._sync.list_operations, session_id, object_id)
