"""
EmbeddingProjection — listens for VersionCreated events and writes tasks
to the outbox table for asynchronous embedding generation.
"""

from __future__ import annotations

import logging
from typing import Any

from cks_runtime.events.event_bus import EventBus
from cks_runtime.events.runtime_event import VersionCreated

logger = logging.getLogger(__name__)


class EmbeddingProjection:
    """
    Subscribes to VersionCreated events and records a task in the outbox
    so that a background worker can later compute embeddings for any new
    or changed Knowledge Objects.
    """

    def __init__(self, event_bus: EventBus, storage: Any) -> None:
        self._event_bus = event_bus
        self._storage = storage

    def start(self) -> None:
        self._event_bus.subscribe(VersionCreated, self._on_version_created)
        logger.info("EmbeddingProjection started, listening for VersionCreated events.")

    def _on_version_created(self, event: VersionCreated) -> None:
        """Callback: write a task to the outbox."""
        session_id = event.session_id
        # We don't have previous_version_id directly on the event,
        # but we can pass None — the worker can compute the diff later.
        previous_version_id = None
        new_version_id = event.version_id

        try:
            self._storage.enqueue_outbox_task(
                event.session_id,
                previous_version_id,
                event.version_id,
            )
            logger.debug(
                "Outbox task created: session=%s, new_version=%s",
                session_id,
                new_version_id,
            )
        except Exception as exc:
            logger.error("Failed to write outbox task: %s", exc)