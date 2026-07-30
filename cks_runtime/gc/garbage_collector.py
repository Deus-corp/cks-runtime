"""
Session Garbage Collector.

Periodically scans storage for sessions that have not been modified
for longer than the configured retention window and evicts them by
calling ``storage.archive_session()``.

Design notes
------------
* Runs as an ``asyncio.Task`` (same model as ``OutboxEmbeddingWorker``).
  Start it with ``await gc.start()`` inside a running event loop and
  stop it with ``await gc.stop()`` during shutdown.

* Only active when the storage backend exposes
  ``list_sessions_modified_before`` and ``archive_session``.  Plain
  ``InMemoryStorage`` never does (sessions are ephemeral), so the
  worker stays idle rather than failing.

* Open sessions (``session.closed == False``) are **never** evicted
  regardless of age.  The GC only touches sessions that have been
  explicitly closed via ``Runtime.close_session()``.

* Eviction is done in batches (default 100) to avoid a single GC
  sweep monopolising the event loop for large deployments.

* All eviction events are logged at INFO level with session_id,
  modified_at, and the configured retention window so operators can
  trace the decision.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cks_runtime.storage.async_storage import AsyncRuntimeStorage
    from cks_runtime.storage.storage import RuntimeStorage

logger = logging.getLogger(__name__)

# Sentinel: attribute name used to duck-type GC-capable storage.
_GC_METHODS = ("list_sessions_modified_before", "archive_session")


def _storage_supports_gc(storage: object) -> bool:
    return all(callable(getattr(storage, m, None)) for m in _GC_METHODS)


class GarbageCollector:
    """
    Background worker that evicts stale closed sessions from storage.

    Parameters
    ----------
    storage:
        The runtime storage backend.  GC is a no-op when the backend
        does not implement ``list_sessions_modified_before`` /
        ``archive_session`` (e.g. ``InMemoryStorage``).
    retention:
        How long a **closed** session is kept before being archived.
        Defaults to 24 hours.
    sweep_interval:
        How often the GC loop wakes up.  Defaults to 10 minutes.
    batch_size:
        Maximum number of sessions evicted per sweep.  Prevents
        single sweeps from running too long on large deployments.
    """

    def __init__(
        self,
        storage: RuntimeStorage | AsyncRuntimeStorage,
        *,
        retention: timedelta = timedelta(hours=24),
        sweep_interval: float = 600.0,   # 10 minutes
        batch_size: int = 100,
    ) -> None:
        self._storage = storage
        self._retention = retention
        self._sweep_interval = sweep_interval
        self._batch_size = batch_size
        self._running = False
        self._task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        if not _storage_supports_gc(self._storage):
            logger.info(
                "%s does not support GC methods; GarbageCollector will not start.",
                type(self._storage).__name__,
            )
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="cks-gc")
        logger.info(
            "GarbageCollector started (retention=%s, sweep_interval=%.0fs, batch=%d).",
            self._retention,
            self._sweep_interval,
            self._batch_size,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("GarbageCollector stopped.")

    # ------------------------------------------------------------------
    # Sweep loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        while self._running:
            try:
                await self._sweep()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("GarbageCollector sweep failed; will retry next interval.")
            await asyncio.sleep(self._sweep_interval)

    async def _sweep(self) -> None:
        cutoff = datetime.now(UTC) - self._retention
        list_fn = self._storage.list_sessions_modified_before

        # list_fn may be sync (SQLiteStorage) or async (PostgresStorage).
        result = list_fn(cutoff, self._batch_size)
        if asyncio.iscoroutine(result):
            candidates = await result
        else:
            candidates = result

        if not candidates:
            return

        archived = 0
        skipped = 0
        for session in candidates:
            # Never evict open sessions — the caller may still be using them.
            if not session.closed:
                skipped += 1
                continue

            archive_fn = self._storage.archive_session
            coro = archive_fn(session)
            if asyncio.iscoroutine(coro):
                await coro

            logger.info(
                "GC archived session %s (modified_at=%s, retention=%s).",
                session.session_id,
                getattr(session, "modified_at", "unknown"),
                self._retention,
            )
            archived += 1

        logger.info(
            "GC sweep complete: %d archived, %d skipped (open sessions).",
            archived,
            skipped,
        )

    # ------------------------------------------------------------------
    # Convenience: run a single sweep synchronously (useful in tests)
    # ------------------------------------------------------------------

    async def run_once(self) -> None:
        """Trigger one sweep immediately, without starting the background loop."""
        await self._sweep()
