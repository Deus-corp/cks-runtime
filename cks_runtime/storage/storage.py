"""
Runtime Storage Interface.

Defines the persistence boundary for Runtime operational state.

Storage implementations persist Runtime objects but never
own Runtime behaviour or semantic interpretation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from cks_runtime.core_api.field_operation import RuntimeFieldOperation
from cks_runtime.session.session import RuntimeSession
from cks_runtime.versioning.version import RuntimeVersion
from cks_runtime.versioning.version_vector import VersionVector


class ConcurrentModificationError(RuntimeError):
    """
    Raised by ``save_session`` when ``expected_version_id`` was given
    and no longer matches the persisted ``latest_version_id`` -- i.e.
    another writer (another process, or another concurrent commit on
    this one) has already advanced this session since the caller last
    read it. Callers should reload the session and retry, not treat
    this as a generic storage failure.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(
            f"Session '{session_id}' was modified concurrently; "
            "reload and retry."
        )


class RuntimeStorage(ABC):
    """
    Abstract Runtime storage.

    Storage is responsible only for persistence.

    Storage never:

    - owns RuntimeSessions;
    - owns RuntimeTransactions;
    - owns RuntimeVersions;
    - performs semantic validation.
    """

    #
    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------
    #

    @abstractmethod
    def save_session(
        self,
        session: RuntimeSession,
        expected_version_id: str | None = None,
    ) -> None:
        """
        Persist a RuntimeSession.

        expected_version_id
            Optional compare-and-swap guard. When given, the write is
            rejected with ``ConcurrentModificationError`` unless the
            backend's currently persisted ``latest_version_id`` for
            this session equals this value (``None`` matching "no
            version persisted yet"). Callers that are not committing
            a new version against a specific prior version (initial
            creation, rollback, abort) may omit it to write
            unconditionally, as before.
        """

    @abstractmethod
    def load_session(
        self,
        session_id: str,
    ) -> RuntimeSession | None:
        """
        Restore a RuntimeSession.

        Returns None when the session does not exist.
        """

    @abstractmethod
    def has_session(
        self,
        session_id: str,
    ) -> bool:
        """
        Whether a RuntimeSession exists.
        """

    @abstractmethod
    def list_sessions(
        self,
    ) -> tuple[RuntimeSession, ...]:
        """
        Return every persisted RuntimeSession.
        """

    #
    # ------------------------------------------------------------------
    # Versions
    # ------------------------------------------------------------------
    #

    @abstractmethod
    def save_version(
        self,
        version: RuntimeVersion,
    ) -> None:
        """
        Persist a RuntimeVersion.
        """

    @abstractmethod
    def load_version(
        self,
        version_id: str,
    ) -> RuntimeVersion | None:
        """
        Restore a RuntimeVersion.

        Returns None when the version does not exist.
        """

    @abstractmethod
    def has_version(
        self,
        version_id: str,
    ) -> bool:
        """
        Whether a RuntimeVersion exists.
        """

    @abstractmethod
    def list_versions(
        self,
    ) -> tuple[RuntimeVersion, ...]:
        """
        Return every persisted RuntimeVersion.
        """

    #
    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------
    #

    @abstractmethod
    def clear(self) -> None:
        """
        Remove every persisted object.

        Primarily intended for testing and
        reference implementations.
        """


    def enqueue_outbox_task(
        self,
        session_id: str,
        previous_version_id: str | None,
        new_version_id: str,
    ) -> None:
        """
        Write a task to the projection outbox (if supported).
        Default implementation does nothing — storage backends that
        support projections override this.
        """


    def enqueue_task(
        self,
        task_type: str,
        session_id: str,
        payload: str,
    ) -> None:
        """
        Write a generic task to the outbox (if supported).
        Default implementation does nothing -- storage backends that
        support the task bus override this. Callers must not assume
        this method's mere presence means the task will actually be
        persisted; check ``supports_outbox`` first.
        """

    def dequeue_next_outbox_task(self, task_type: str | None = None) -> OutboxTask | None:
        """
        Atomically claim and return the next eligible outbox task, or
        None if the backend doesn't support outbox operations (or
        nothing is eligible). Implementations that support the outbox
        must claim the task as part of the same operation that reads
        it, so that two callers polling concurrently never both
        receive the same task.

        ``task_type``, when given, restricts eligibility to tasks of
        that type -- so a worker dedicated to one kind of task (e.g.
        ``OutboxEmbeddingWorker`` polling for ``"projection"``, or a
        Critic-agent worker polling for ``"gossip_conflict"`` /
        ``"inference_conflict"``) never claims a task meant for a
        different worker and fails on it. ``None`` (the default)
        preserves the original untyped behaviour of claiming the
        earliest eligible task regardless of type.
        """
        return None

    def complete_outbox_task(self, task_id: int) -> None:
        """Mark an outbox task as completed. No-op by default."""

    def fail_outbox_task(self, task_id: int, retry_count: int, error: str, next_retry_at: str) -> None:
        """Mark a task as failed with exponential backoff. No-op by default."""

    def dead_letter_outbox_task(self, task_id: int, error: str) -> None:
        """
        Permanently mark a task as ``DEAD`` -- the caller has given up
        retrying it (e.g. a Critic agent that could not resolve a
        conflict with any confidence after repeated attempts). Unlike
        ``fail_outbox_task``, this removes the task from the eligible
        pool for good rather than scheduling another retry; it stays
        in the table (see ``list_dead_letter_tasks``) for a human or
        an operator tool to inspect. No-op by default.
        """

    def touch_outbox_task(self, task_id: int) -> bool:
        """
        Renew an ``IN_PROGRESS`` task's lease (``claimed_at``) so a
        worker still actively processing a slow task (e.g. an
        unattended Critic agent waiting on an LLM call) isn't reclaimed
        by another worker once ``dequeue_next_outbox_task``'s stale-lease
        window elapses. Callers are expected to call this periodically,
        well inside that window, for the duration of a long-running
        resolution -- not just once.

        Returns ``True`` if the lease was renewed (the task was still
        ``IN_PROGRESS`` under this task_id), ``False`` if it wasn't
        found in that state -- e.g. it was already reclaimed by another
        worker, or already completed/failed/dead-lettered. A caller
        that sees ``False`` should treat its own claim on the task as
        lost and abandon any further action on it (in particular, must
        not call ``complete_outbox_task``/``fail_outbox_task`` for it
        afterwards -- that would race with whoever holds the lease now).
        No-op (returns ``False``) by default.
        """
        return False

    def list_tasks_by_type(
        self,
        task_type: str,
        session_id: str | None = None,
        drain: bool = True,
    ) -> list[OutboxTask]:
        """
        Return every PENDING task of ``task_type`` (optionally filtered
        to one ``session_id``), oldest first -- a batch read for
        callers that want the whole matching queue at once rather than
        one task at a time (unlike ``dequeue_next_outbox_task``, which
        claims a single task for sequential processing). This is the
        outbox-backed equivalent of ``ConflictInbox.list``/
        ``list_inference``'s peek/drain shape, so gossip- and
        inference-conflict records enqueued as outbox tasks can be
        listed the same way regardless of which storage backend holds
        them.

        ``drain`` (default ``True``) removes the returned tasks from
        the outbox as part of the same read -- a caller that just read
        a conflict is expected to act on it. Pass ``drain=False`` to
        peek without consuming. Empty by default -- backends that
        support the outbox override this.
        """
        return []

    def list_dead_letter_tasks(self, task_type: str | None = None) -> list[OutboxTask]:
        """
        Return every ``DEAD``-lettered task (optionally filtered to one
        ``task_type``), oldest first, for inspection -- never drains.
        Empty by default -- backends that support the outbox override
        this.
        """
        return []

    def save_object_embeddings(self, object_id: str, session_id: str, embedding: bytes) -> None:
        """Save an embedding for an object. No-op by default."""

    def delete_object_embeddings(self, object_id: str, session_id: str) -> None:
        """Delete embeddings for an object. No-op by default."""

    @property
    def supports_outbox(self) -> bool:
        """Whether this storage backend supports outbox operations."""
        return False

    def record_operations(
        self,
        session_id: str,
        version_id: str,
        operations: list[RuntimeFieldOperation],
    ) -> None:
        """
        Append field-level operations for a committed version to the
        operation log (if supported). No-op by default -- storage
        backends that support it override this. See ADR-007: this is
        an optional accelerant for future merge fast-paths, not part
        of the observable Version history, so its absence never
        affects commit correctness. Callers must not assume this
        method's mere presence means anything is actually persisted;
        check ``supports_operation_log`` first.
        """

    @property
    def supports_operation_log(self) -> bool:
        """Whether this storage backend supports the operation log."""
        return False

    def list_operations(
        self,
        session_id: str,
        object_id: str | None = None,
        version_id: str | None = None,
    ) -> list[RuntimeFieldOperation]:
        """
        Return logged field-level operations for a session (optionally
        filtered to one object_id), oldest first. Empty by default --
        backends that support the operation log override this. Only
        meaningful when ``supports_operation_log`` is true; callers
        (``MergeOperation``'s ADR-007 fast path) already check that
        before calling this, so the empty-list default never needs to
        be distinguished from "not supported" by callers that skip the
        guard.
        """
        return []

    #
    # ------------------------------------------------------------------
    # Distributed replication (ADR-008)
    # ------------------------------------------------------------------
    #

    def get_or_create_replica_id(self) -> str | None:
        """
        Return this storage instance's durable replica identity,
        creating and persisting one on first call if none exists yet.
        ``None`` by default -- backends that support gossip
        replication override this. A ``None`` replica id means this
        storage instance is not a distinguishable gossip peer; callers
        (``GossipAdapter``) must not attempt to gossip through it.
        """
        return None

    def fetch_operations_since(
        self, vector: VersionVector
    ) -> list[RuntimeFieldOperation]:
        """
        Return every logged field-level operation belonging to a
        RuntimeVersion whose own recorded VersionVector is not
        dominated by ``vector``.

        A generic, backend-agnostic default built entirely on
        ``list_versions()`` + ``list_operations(version_id=...)`` --
        mirrors ``AsyncRuntimeStorage.fetch_operations_since()``
        exactly, so any backend that already supports the operation
        log (``supports_operation_log``) gets this for free without
        writing its own version. Empty when the operation log isn't
        supported, same as ``list_operations``' own default.
        """
        if not self.supports_operation_log:
            return []

        operations: list[RuntimeFieldOperation] = []
        for version in self.list_versions():
            version_vector = VersionVector.from_metadata(version.metadata)
            if vector.dominates(version_vector):
                continue
            operations.extend(
                self.list_operations(version.session_id, version_id=version.version_id)
            )
        return operations

    def list_sessions_modified_before(
        self,
        cutoff: Any,  # datetime
        limit: int = 1000,
    ) -> list[Any]:
        """Return sessions with modified_at < cutoff. Empty by default."""
        return []

    def list_sessions_modified_since(
        self,
        watermark: Any,  # datetime
        limit: int = 1000,
    ) -> list[Any]:
        """
        Return sessions with modified_at >= watermark, oldest first.
        Empty by default -- backends that support GC's
        ``list_sessions_modified_before`` (they share the same
        indexed ``modified_at`` column, see ``sqlite_storage.py``/
        ``postgres_storage.py``) implement this too. Used by
        ``InferenceStalenessSweeper`` (ADR-009) to find candidates for
        a reasoning-staleness re-check; unlike GC's cutoff, a session
        is never excluded here for being open -- see that class'
        module docstring.
        """
        return []

    def archive_session(self, session: Any) -> None:
        """Archive a session and remove it from active storage. No-op by default."""


@dataclass(frozen=True, slots=True)
class OutboxTask:
    """A task read from the outbox table."""
    task_id: int
    task_type: str
    session_id: str
    payload: str
    retry_count: int = 0
