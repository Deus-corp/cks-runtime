"""
OutboxEmbeddingWorker — polls the outbox table and generates embeddings
for new or changed Knowledge Objects.
"""

from __future__ import annotations

import logging
import time
import threading
from typing import Any

logger = logging.getLogger(__name__)


class OutboxEmbeddingWorker:
    """
    Background worker that reads tasks from the outbox, computes text
    representations for added/modified objects, generates embeddings,
    and stores them in cks_object_embeddings.
    """

    def __init__(self, storage: Any, core_bridge: Any, poll_interval: float = 2.0) -> None:
        self._storage = storage
        self._core_bridge = core_bridge
        self._poll_interval = poll_interval
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._running:
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
        # Grab one pending task
        row = self._storage._conn.execute(
            """
            SELECT task_id, session_id, previous_version_id, new_version_id
            FROM cks_projection_outbox
            WHERE status = 'PENDING' AND next_retry_at <= datetime('now')
            ORDER BY created_at ASC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return

        task_id, session_id, prev_version_id, new_version_id = row

        try:
            self._execute_task(session_id, prev_version_id, new_version_id)
            # Success — delete task
            self._storage._conn.execute(
                "DELETE FROM cks_projection_outbox WHERE task_id = ?",
                (task_id,),
            )
            self._storage._conn.commit()
            logger.info("Outbox task %s completed.", task_id)
        except Exception as exc:
            logger.error("Outbox task %s failed: %s", task_id, exc)
            retry_count = row[5] + 1 if len(row) > 5 else 1
            self._storage._conn.execute(
                """
                UPDATE cks_projection_outbox
                SET status = 'FAILED',
                    retry_count = ?,
                    next_retry_at = datetime('now', '+' || ? || ' seconds'),
                    last_error = ?
                WHERE task_id = ?
                """,
                (retry_count, 2 ** retry_count, str(exc), task_id),
            )
            self._storage._conn.commit()

    def _execute_task(
        self,
        session_id: str,
        prev_version_id: str | None,
        new_version_id: str,
    ) -> None:
        # Load versions
        new_version = self._storage.load_version(new_version_id)
        if new_version is None:
            raise ValueError(f"Version {new_version_id} not found")

        new_structure = new_version.knowledge_structure
        if prev_version_id:
            prev_version = self._storage.load_version(prev_version_id)
            old_structure = prev_version.knowledge_structure if prev_version else None
        else:
            old_structure = None

        # Compute diff (or treat all objects as new)
        if old_structure is not None and self._core_bridge is not None:
            patch = self._core_bridge.diff(old_structure, new_structure)
        else:
            # First version — all objects are new
            patch = None

        # Collect added/modified objects
        objects_to_embed: list[Any] = []
        if patch is not None:
            from cks.evolution import AddObject, RemoveObject
            for op in patch:
                if isinstance(op, AddObject):
                    obj = new_structure.get(op._obj.identity.id)
                    if obj is not None:
                        objects_to_embed.append(obj)
        else:
            # No diff — embed all objects
            objects_to_embed = list(new_structure.objects)

        if not objects_to_embed:
            return

        # Generate embeddings (stub for now — random vectors)
        import hashlib
        for obj in objects_to_embed:
            text_repr = self._format_for_embedding(obj)
            # Stub embedding: hash the text and pad to 384 floats
            digest = hashlib.sha256(text_repr.encode()).digest()
            import struct
            embedding = bytes()
            for i in range(0, len(digest), 4):
                val = struct.unpack("f", digest[i:i+4])[0]
                embedding += struct.pack("f", val)
            # Pad to 384 floats
            while len(embedding) < 384 * 4:
                embedding += struct.pack("f", 0.0)

            self._storage._conn.execute(
                """
                INSERT OR REPLACE INTO cks_object_embeddings
                    (object_id, session_id, embedding)
                VALUES (?, ?, ?)
                """,
                (obj.identity.id, session_id, embedding),
            )
        self._storage._conn.commit()

    @staticmethod
    def _format_for_embedding(obj: Any) -> str:
        return (
            f"{obj.identity.name} ({obj.identity.type}): "
            f"{obj.structure.get('description', '')}"
        )