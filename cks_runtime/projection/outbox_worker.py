"""
OutboxEmbeddingWorker — polls the outbox table and generates embeddings
for new or changed Knowledge Objects.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from cks_runtime.embedding.client import EmbeddingClient, StubEmbeddingClient
from cks_runtime.storage.async_storage import AsyncRuntimeStorage

logger = logging.getLogger(__name__)


class OutboxEmbeddingWorker:
    """
    Background worker that reads tasks from the outbox, computes text
    representations for added/modified objects, generates embeddings,
    and stores them in cks_object_embeddings.

    Runs as an ``asyncio.Task`` (not a thread): the poll loop and every
    storage call are ``await``-ed directly against the runtime's own
    ``AsyncRuntimeStorage``, so there is exactly one execution model in
    play, not a background thread quietly calling into storage
    alongside the event loop. ``EmbeddingClient.embed_batch`` is still
    a blocking, synchronous call (a plain HTTP request under the
    hood) -- that one call is dispatched via ``asyncio.to_thread`` so
    it doesn't stall the loop for the duration of the request, without
    requiring an async-native embedding client.
    """

    def __init__(
        self,
        storage: AsyncRuntimeStorage,
        core_bridge: Any,
        embedding_client: EmbeddingClient | None = None,
        poll_interval: float = 2.0,
        max_retries: int = 5,
    ) -> None:
        self._storage = storage
        self._core_bridge = core_bridge
        self._embedding_client = embedding_client or StubEmbeddingClient()
        self._poll_interval = poll_interval
        #: after this many failed attempts at one task, dead-letter it
        #: instead of scheduling yet another backoff retry -- without
        #: this, a task whose failure cause is *not* transient (e.g. a
        #: genuinely corrupted patch chain -- see
        #: ``_execute_task``/``_reconstruct_with_retry``'s docstrings)
        #: retries forever with exponentially-growing backoff and
        #: never actually goes away.
        self._max_retries = max_retries
        self._running = False
        self._task: asyncio.Task[None] | None = None

    def set_embedding_client(self, client: EmbeddingClient) -> None:
        """
        Swap the embedding client used for subsequent poll iterations.
        Safe to call while the worker is running: the client is only
        read at the start of each poll iteration (see ``_poll_once``),
        never cached across iterations, so a client installed mid-run
        takes effect on the very next batch.
        """
        self._embedding_client = client

    async def start(self) -> None:
        if self._running:
            return
        if not getattr(self._storage, "supports_outbox", False):
            # No point polling forever: a backend that doesn't
            # implement the outbox (e.g. InMemoryStorage) will never
            # have anything queued for dequeue_next_outbox_task to
            # find. Staying stopped avoids a background task that
            # spins on a no-op every poll_interval indefinitely.
            logger.info(
                "%s does not support the projection outbox; "
                "OutboxEmbeddingWorker will not start.",
                type(self._storage).__name__,
            )
            return
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("OutboxEmbeddingWorker started.")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("OutboxEmbeddingWorker stopped.")

    async def _run(self) -> None:
        while self._running:
            try:
                await self._process_next_task()
            except Exception as exc:  # noqa: BLE001 -- one bad iteration must not kill the worker task; logged below
                logger.error("Worker iteration error: %s", exc)
            await asyncio.sleep(self._poll_interval)

    async def _process_next_task(self) -> None:
        # Filtered to "projection" so this worker only ever claims its
        # own tasks -- other task_types (e.g. "gossip_conflict" /
        # "inference_conflict", enqueued for a Critic agent) now share
        # this same table (see storage.py's dequeue_next_outbox_task
        # task_type filter, cks-runtime 1.34.0) and must never be
        # claimed, and fail, here.
        task = await self._storage.dequeue_next_outbox_task(task_type="projection")
        if task is None:
            return

        try:
            payload = json.loads(task.payload)
            prev_version_id = payload.get("previous_version_id")
            new_version_id = payload.get("new_version_id")
            await self._execute_task(task.session_id, prev_version_id, new_version_id)

            await self._storage.complete_outbox_task(task.task_id)
            logger.info("Outbox task %s completed.", task.task_id)
        except Exception as exc:  # noqa: BLE001 -- any failure must route to the retry/backoff path below; logged
            logger.error("Outbox task %s failed: %s", task.task_id, exc)
            retry_count = task.retry_count + 1
            if retry_count >= self._max_retries:
                # Give up for good instead of retrying forever with
                # ever-growing backoff -- a hash-mismatch (or any
                # other) failure that survives ``_execute_task``'s own
                # reload-and-retry is not transient, and an unbounded
                # fail/backoff loop would otherwise never surface that
                # to an operator.
                await self._storage.dead_letter_outbox_task(task.task_id, str(exc))
                logger.error(
                    "Outbox task %s dead-lettered after %s attempt(s): %s",
                    task.task_id, retry_count, exc,
                )
                return
            delay_seconds = min(2 ** retry_count, 3600)
            next_retry = (datetime.now(UTC) + timedelta(seconds=delay_seconds)).isoformat()
            await self._storage.fail_outbox_task(
                task.task_id, retry_count, str(exc), next_retry
            )

    async def _execute_task(
        self,
        session_id: str,
        prev_version_id: str | None,
        new_version_id: str,
    ) -> None:
        # Load versions
        # Load session to reconstruct version state (handles delta versions)
        session = await self._storage.load_session(session_id)
        if session is None:
            raise ValueError(f"Session {session_id} not found")

        # Reconstruct the full Knowledge Structure for the new version.
        # A hash mismatch here (RuntimeSession.get_version_state
        # raising ValueError) can be a genuine data-integrity problem,
        # but it can also be a snapshot-consistency race: this
        # worker's ``load_session`` read may have landed between two
        # writes from a concurrent agent updating the same session
        # (e.g. a snapshot compaction not yet visible when the version
        # rows were, or vice versa). Reloading once and reconstructing
        # again against a fully-fresh read clears that race without
        # masking a real corruption -- a genuinely bad patch chain
        # still fails the same way on the retry and propagates.
        new_structure = await self._reconstruct_with_retry(session_id, session, new_version_id)
        if new_structure is None:
            raise ValueError(f"Failed to reconstruct state for version {new_version_id}")

        # Reconstruct old structure for diff (if available). The caller
        # (EmbeddingProjection) doesn't have direct access to the
        # previous version id at event time, so it always passes
        # `None` here -- we resolve it ourselves from the session's
        # own version history, which is exactly the version
        # immediately preceding `new_version_id` in commit order.
        if prev_version_id is None:
            version_ids = [v.version_id for v in session.version_history]
            try:
                index = version_ids.index(new_version_id)
            except ValueError:
                index = -1
            if index > 0:
                prev_version_id = version_ids[index - 1]

        if prev_version_id:
            old_structure = await self._reconstruct_with_retry(
                session_id, session, prev_version_id
            )
        else:
            old_structure = None

        # Compute diff (or treat all objects as new)
        if old_structure is not None and self._core_bridge is not None:
            patch = self._core_bridge.diff(old_structure, new_structure)
        else:
            # First version — all objects are new
            patch = None

        # Collect added/modified objects, and ids to drop from the
        # embedding index because the object was removed.
        objects_to_embed: list[Any] = []
        ids_to_remove: list[str] = []
        if patch is not None:
            from cks.evolution import AddObject, RemoveObject
            for op in patch:
                if isinstance(op, AddObject):
                    # AddObject exposes the KnowledgeObject itself via the
                    # public `obj` property (cks-core >= v1.14.0); the
                    # object's id lives at `obj.identity.id`. There is no
                    # `object_id` attribute on AddObject — that only
                    # exists on RemoveObject.
                    obj = new_structure.get(op.obj.identity.id)
                    if obj is not None:
                        objects_to_embed.append(obj)
                elif isinstance(op, RemoveObject):
                    ids_to_remove.append(op.object_id)
        else:
            # No diff — embed all objects
            objects_to_embed = list(new_structure.objects)

        # Filter out relation objects — only Concepts/Documents/etc. should be searchable
        from cks.core import CanonicalRelation
        objects_to_embed = [
            obj for obj in objects_to_embed
            if not isinstance(obj, CanonicalRelation)
        ]

        if ids_to_remove:
            for object_id in ids_to_remove:
                await self._storage.delete_object_embeddings(object_id, session_id)

        if not objects_to_embed:
            return

        # Generate embeddings using the configured client. embed_batch
        # is a blocking call (synchronous HTTP request under the hood)
        # -- offloaded via to_thread so it doesn't stall the event loop
        # for the duration of the request.
        texts = [self._format_for_embedding(obj) for obj in objects_to_embed]
        embeddings = await asyncio.to_thread(
            self._embedding_client.embed_batch, texts, normalize=True
        )

        # strict=True: if embed_batch ever returns a different number
        # of vectors than texts sent (e.g. a partial provider failure),
        # fail loudly here instead of silently dropping the trailing
        # objects from the embedding index with no error signal.
        for obj, embedding in zip(objects_to_embed, embeddings, strict=True):
            await self._storage.save_object_embeddings(obj.identity.id, session_id, embedding)

    async def _reconstruct_with_retry(
        self, session_id: str, session: Any, version_id: str
    ) -> Any:
        """
        Reconstruct ``version_id``'s Knowledge Structure via
        ``session.get_version_state``, retrying exactly once against
        a freshly-reloaded ``RuntimeSession`` if the first attempt
        fails on a state-hash mismatch (see ``_execute_task``'s
        docstring for why a fresh reload can clear a transient
        snapshot-consistency race). Any other ``ValueError`` (missing
        version, no core_bridge for a delta, etc.) is not
        reload-and-retried -- reloading the same session can't fix
        those -- and propagates immediately, same as before this
        method existed. A mismatch that persists after the reload is
        a genuine corruption, not a race: it also propagates, so the
        caller's fail/dead-letter accounting in ``_process_next_task``
        applies to it.
        """
        try:
            return session.get_version_state(version_id, self._core_bridge)
        except ValueError as exc:
            if "does not match its recorded hash" not in str(exc):
                raise
            logger.warning(
                "Hash mismatch reconstructing version %s for session %s; "
                "reloading session from storage and retrying once: %s",
                version_id, session_id, exc,
            )
            fresh_session = await self._storage.load_session(session_id)
            if fresh_session is None:
                raise
            return fresh_session.get_version_state(version_id, self._core_bridge)

    @staticmethod
    def _format_for_embedding(obj: Any) -> str:
        return (
            f"{obj.identity.name} ({obj.identity.type}): "
            f"{obj.structure.get('description', '')}"
        )
