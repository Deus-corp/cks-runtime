"""
OutboxEmbeddingWorker — polls the outbox table and generates embeddings
for new or changed Knowledge Objects.
"""

from __future__ import annotations

import logging
import time
import threading
import json
from typing import Any

from cks_runtime.embedding.client import EmbeddingClient, StubEmbeddingClient
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


class OutboxEmbeddingWorker:
    """
    Background worker that reads tasks from the outbox, computes text
    representations for added/modified objects, generates embeddings,
    and stores them in cks_object_embeddings.
    """

    def __init__(
        self,
        storage: Any,
        core_bridge: Any,
        embedding_client: EmbeddingClient | None = None,
        poll_interval: float = 2.0,
    ) -> None:
        self._storage = storage
        self._core_bridge = core_bridge
        self._embedding_client = embedding_client or StubEmbeddingClient()
        self._poll_interval = poll_interval
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._running:
            return
        if not getattr(self._storage, "supports_outbox", False):
            # No point polling forever: a backend that doesn't
            # implement the outbox (e.g. InMemoryStorage) will never
            # have anything queued for dequeue_next_outbox_task to
            # find. Staying stopped avoids a background thread that
            # spins on a no-op every poll_interval indefinitely.
            logger.info(
                "%s does not support the projection outbox; "
                "OutboxEmbeddingWorker will not start.",
                type(self._storage).__name__,
            )
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("OutboxEmbeddingWorker started.")

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5)
        logger.info("OutboxEmbeddingWorker stopped.")

    def _run(self) -> None:
        while self._running:
            try:
                self._process_next_task()
            except Exception as exc:
                logger.error("Worker iteration error: %s", exc)
            time.sleep(self._poll_interval)

    def _process_next_task(self) -> None:
        task = self._storage.dequeue_next_outbox_task()
        if task is None:
            return

        try:
            if task.task_type == "projection":
                import json
                payload = json.loads(task.payload)
                prev_version_id = payload.get("previous_version_id")
                new_version_id = payload.get("new_version_id")
                self._execute_task(task.session_id, prev_version_id, new_version_id)
            else:
                raise ValueError(f"Unknown task type: {task.task_type}")

            self._storage.complete_outbox_task(task.task_id)
            logger.info("Outbox task %s completed.", task.task_id)
        except Exception as exc:
            logger.error("Outbox task %s failed: %s", task.task_id, exc)
            retry_count = task.retry_count + 1
            delay_seconds = min(2 ** retry_count, 3600)
            next_retry = (datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)).isoformat()
            self._storage.fail_outbox_task(
                task.task_id, retry_count, str(exc), next_retry
            )

    def _execute_task(
        self,
        session_id: str,
        prev_version_id: str | None,
        new_version_id: str,
    ) -> None:
        # Load versions
        # Load session to reconstruct version state (handles delta versions)
        session = self._storage.load_session(session_id)
        if session is None:
            raise ValueError(f"Session {session_id} not found")

        # Reconstruct the full Knowledge Structure for the new version
        new_structure = session.get_version_state(new_version_id, self._core_bridge)
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
            old_structure = session.get_version_state(prev_version_id, self._core_bridge)
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
                    obj = new_structure.get(op._obj.identity.id)
                    if obj is not None:
                        objects_to_embed.append(obj)
                elif isinstance(op, RemoveObject):
                    ids_to_remove.append(op._object_id)
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
                self._storage.delete_object_embeddings(object_id, session_id)

        if not objects_to_embed:
            return

        # Generate embeddings using the configured client
        texts = [self._format_for_embedding(obj) for obj in objects_to_embed]
        embeddings = self._embedding_client.embed_batch(texts, normalize=True)

        for obj, embedding in zip(objects_to_embed, embeddings):
            self._storage.save_object_embeddings(obj.identity.id, session_id, embedding)

    @staticmethod
    def _format_for_embedding(obj: Any) -> str:
        return (
            f"{obj.identity.name} ({obj.identity.type}): "
            f"{obj.structure.get('description', '')}"
        )