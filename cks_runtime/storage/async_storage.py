"""
Async Runtime Storage Interface.

``RuntimeStorage`` (see ``storage.py``) is synchronous, which fits
``InMemoryStorage`` and ``SQLiteStorage`` -- neither has a meaningful
"await" point. A backend built on a real network database does, and
wrapping an async driver in a sync facade (``asyncio.run(...)`` per
call) throws away the concurrency the driver exists to provide and
breaks the moment it's called from inside an already-running event
loop. So this is a second, parallel storage contract -- same shape and
same behavioural guarantees as ``RuntimeStorage``, every method
``async def`` -- for backends where that matters.

This is an intentional fork, not a refactor of ``RuntimeStorage``:
``Runtime``, ``SessionManager``, and the execution pipeline are all
synchronous today and are not touched by this module. A backend that
implements ``AsyncRuntimeStorage`` (``PostgresStorage`` today) is not
yet wireable into the synchronous ``Runtime`` -- that bridge (either
an async-native ``Runtime``, or a thin sync facade over an event loop
owned by the caller) is deliberately left for a follow-up once the
storage layer itself is proven out, rather than guessed at here.

``ConcurrentModificationError`` and ``OutboxTask`` are shared,
transport-agnostic types with no sync/async distinction of their own,
so they are imported from ``storage.py`` rather than duplicated.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from cks_runtime.core_api.field_operation import RuntimeFieldOperation
from cks_runtime.session.session import RuntimeSession
from cks_runtime.storage.storage import (
    AgentLivenessRecord,
    ConcurrentModificationError,  # noqa: F401
    OutboxTask,
)
from cks_runtime.versioning.version import RuntimeVersion
from cks_runtime.versioning.version_vector import VersionVector


class AsyncRuntimeStorage(ABC):
    """
    Abstract async Runtime storage.

    Same responsibilities and the same non-responsibilities as
    ``RuntimeStorage``: storage persists Runtime objects but never
    owns Runtime behaviour, session/transaction/version identity, or
    semantic validation. See ``storage.py`` for the full rationale;
    this class exists only to make every method awaitable.
    """

    #
    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------
    #

    @abstractmethod
    async def save_session(
        self,
        session: RuntimeSession,
        expected_version_id: str | None = None,
    ) -> None:
        """
        Persist a RuntimeSession.

        expected_version_id
            Optional compare-and-swap guard, identical in meaning to
            ``RuntimeStorage.save_session``: when given, the write is
            rejected with ``ConcurrentModificationError`` unless the
            backend's currently persisted ``latest_version_id`` for
            this session equals this value (``None`` matching "no
            version persisted yet"). Omit it to write unconditionally.
        """

    @abstractmethod
    async def load_session(
        self,
        session_id: str,
    ) -> RuntimeSession | None:
        """Restore a RuntimeSession. Returns None when it does not exist."""

    @abstractmethod
    async def has_session(
        self,
        session_id: str,
    ) -> bool:
        """Whether a RuntimeSession exists."""

    @abstractmethod
    async def list_sessions(
        self,
    ) -> tuple[RuntimeSession, ...]:
        """Return every persisted RuntimeSession."""

    #
    # ------------------------------------------------------------------
    # Versions
    # ------------------------------------------------------------------
    #

    @abstractmethod
    async def save_version(
        self,
        version: RuntimeVersion,
    ) -> None:
        """Persist a RuntimeVersion."""

    @abstractmethod
    async def load_version(
        self,
        version_id: str,
    ) -> RuntimeVersion | None:
        """Restore a RuntimeVersion. Returns None when it does not exist."""

    @abstractmethod
    async def has_version(
        self,
        version_id: str,
    ) -> bool:
        """Whether a RuntimeVersion exists."""

    @abstractmethod
    async def list_versions(
        self,
    ) -> tuple[RuntimeVersion, ...]:
        """Return every persisted RuntimeVersion."""

    #
    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------
    #

    @abstractmethod
    async def clear(self) -> None:
        """Remove every persisted object. Primarily for tests."""

    #
    # ------------------------------------------------------------------
    # Optional subsystems (outbox, embeddings, operation log) -- default
    # no-ops, same convention as RuntimeStorage. Backends that support
    # them override the methods below and the matching ``supports_*``
    # property. Left unimplemented in PostgresStorage's first cut
    # (schema + session/version CRUD + CAS) and filled in as those
    # backends are built out.
    # ------------------------------------------------------------------
    #

    async def enqueue_outbox_task(
        self,
        session_id: str,
        previous_version_id: str | None,
        new_version_id: str,
    ) -> None:
        """No-op by default -- backends that support projections override this."""

    async def enqueue_task(
        self,
        task_type: str,
        session_id: str,
        payload: str,
    ) -> None:
        """No-op by default -- backends that support the task bus override this."""

    async def dequeue_next_outbox_task(self, task_type: str | None = None) -> OutboxTask | None:
        """
        Atomically claim and return the next eligible outbox task, or
        None if the backend doesn't support outbox operations (or
        nothing is eligible). Implementations that support the outbox
        must claim the task as part of the same operation that reads
        it, so that concurrent callers never both receive the same
        task -- for ``PostgresStorage`` this is ``SELECT ... FOR
        UPDATE SKIP LOCKED``, not a port of SQLite's single-writer
        claim.

        ``task_type``, when given, restricts eligibility to tasks of
        that type, same rationale as ``RuntimeStorage``'s sync
        counterpart: it's what lets an ``OutboxEmbeddingWorker`` and a
        Critic-agent worker share one table without stealing each
        other's tasks. ``None`` preserves the original untyped
        behaviour.
        """
        return None

    async def complete_outbox_task(self, task_id: int) -> None:
        """Mark an outbox task as completed. No-op by default."""

    async def fail_outbox_task(
        self, task_id: int, retry_count: int, error: str, next_retry_at: str
    ) -> None:
        """Mark a task as failed with exponential backoff. No-op by default."""

    async def dead_letter_outbox_task(self, task_id: int, error: str) -> None:
        """
        Permanently mark a task as ``DEAD`` instead of scheduling
        another retry -- see ``RuntimeStorage.dead_letter_outbox_task``
        for the rationale. No-op by default.
        """

    async def touch_outbox_task(self, task_id: int) -> bool:
        """
        Renew an ``IN_PROGRESS`` task's lease -- see
        ``RuntimeStorage.touch_outbox_task`` for the rationale and the
        meaning of the return value. No-op (returns ``False``) by
        default.
        """
        return False

    async def list_tasks_by_type(
        self,
        task_type: str,
        session_id: str | None = None,
        drain: bool = True,
    ) -> list[OutboxTask]:
        """
        Return every PENDING task of ``task_type`` (optionally filtered
        to one ``session_id``), oldest first, draining them from the
        outbox unless ``drain=False``. See
        ``RuntimeStorage.list_tasks_by_type`` for the full rationale --
        this is the batch peek/drain read a Critic agent uses to list
        outstanding gossip/inference conflicts, as opposed to
        ``dequeue_next_outbox_task``'s single-task claim. Empty by
        default.
        """
        return []

    async def list_dead_letter_tasks(self, task_type: str | None = None) -> list[OutboxTask]:
        """
        Return every ``DEAD``-lettered task (optionally filtered to one
        ``task_type``), oldest first, for inspection -- never drains.
        Empty by default.
        """
        return []

    async def upsert_agent_liveness(self, record: AgentLivenessRecord) -> None:
        """
        Write/refresh one standalone-agent process instance's liveness
        heartbeat row (ADR-014). No-op by default -- backends that
        support agent liveness override this.
        """

    async def list_agent_liveness(self) -> list[AgentLivenessRecord]:
        """
        Return every known standalone-agent process instance, most
        recently started first. See ``RuntimeStorage.list_agent_liveness``
        for the rationale (TTL computed by the caller, not stored).
        Empty by default.
        """
        return []

    async def get_agent_liveness(self, instance_id: str) -> AgentLivenessRecord | None:
        """
        Return the single liveness row for ``instance_id``, or ``None``
        if no such row exists. See ``RuntimeStorage.get_agent_liveness``
        for the rationale (ADR-016 §2). ``None`` by default.
        """
        return None

    async def request_agent_stop(self, instance_id: str) -> bool:
        """
        Set ``desired_state='stop_requested'`` for this ``instance_id``.
        See ``RuntimeStorage.request_agent_stop`` for the rationale
        (ADR-016 §1). ``False`` by default.
        """
        return False

    @property
    def supports_agent_liveness(self) -> bool:
        """Whether this storage backend supports agent liveness tracking."""
        return False

    async def set_sweeper_desired_running(self, agent_id: str, desired_running: bool) -> None:
        """
        Persist a manual override of whether ``agent_id`` should be
        running. See ``RuntimeStorage.set_sweeper_desired_running``
        for the rationale (ADR-015 §1). No-op by default.
        """

    async def get_sweeper_desired_running(self, agent_id: str) -> bool | None:
        """
        Return the stored override for ``agent_id``, or ``None`` if no
        override row exists. See
        ``RuntimeStorage.get_sweeper_desired_running`` for the
        rationale. ``None`` by default.
        """
        return None

    async def save_object_embeddings(
        self, object_id: str, session_id: str, embedding: bytes
    ) -> None:
        """Save an embedding for an object. No-op by default."""

    async def delete_object_embeddings(self, object_id: str, session_id: str) -> None:
        """Delete embeddings for an object. No-op by default."""

    @property
    def supports_outbox(self) -> bool:
        """Whether this storage backend supports outbox operations."""
        return False

    async def record_operations(
        self,
        session_id: str,
        version_id: str,
        operations: list[RuntimeFieldOperation],
    ) -> None:
        """Append field-level operations for a committed version (ADR-007). No-op by default."""

    @property
    def supports_operation_log(self) -> bool:
        """Whether this storage backend supports the operation log."""
        return False

    async def list_operations(
        self,
        session_id: str,
        object_id: str | None = None,
        version_id: str | None = None,
    ) -> list[RuntimeFieldOperation]:
        """
        Return logged field-level operations for a session. Empty by
        default. The ``version_id`` filter (ADR-008) narrows to a
        single committed version's operations.
        """
        return []

    #
    # ------------------------------------------------------------------
    # Distributed replication (ADR-008)
    # ------------------------------------------------------------------
    #

    async def get_or_create_replica_id(self) -> str | None:
        """
        Return this storage instance's durable replica identity,
        creating and persisting one on first call if none exists yet.
        ``None`` by default -- see ``RuntimeStorage.get_or_create_replica_id``
        (``storage.py``) for the full rationale, identical here.
        """
        return None

    async def fetch_operations_since(
        self, vector: VersionVector
    ) -> list[RuntimeFieldOperation]:
        """
        Return every logged field-level operation belonging to a
        RuntimeVersion whose own recorded VersionVector is not
        dominated by ``vector``. A generic, backend-agnostic default
        built entirely on ``list_versions()`` + ``list_operations(version_id=...)``
        -- see ``RuntimeStorage.fetch_operations_since`` (``storage.py``)
        for the full rationale, identical here.
        """
        if not self.supports_operation_log:
            return []

        operations: list[RuntimeFieldOperation] = []
        for version in await self.list_versions():
            version_vector = VersionVector.from_metadata(version.metadata)
            if vector.dominates(version_vector):
                continue
            operations.extend(
                await self.list_operations(
                    version.session_id, version_id=version.version_id
                )
            )
        return operations


    #
    # ------------------------------------------------------------------
    # Embedding search (optional subsystem, same convention as outbox /
    # operation log above)
    # ------------------------------------------------------------------
    #

    async def search_embeddings(
        self,
        query_embedding: bytes,
        session_id: str,
        top_k: int = 5,
    ) -> list[tuple[str, float]]:
        """
        Return (object_id, similarity_score) pairs for the top_k closest
        embeddings to query_embedding within the given session, ordered
        from most to least similar. Empty by default -- backends that
        support vector similarity search override this.
        """
        return []

    @property
    def supports_embedding_search(self) -> bool:
        """Whether this storage backend supports vector similarity search."""
        return False


    # ------------------------------------------------------------------
    # Backup / Disaster Recovery (ADR-012)
    # ------------------------------------------------------------------

    async def export_storage(self) -> dict:
        """
        Return a complete, JSON-serialisable snapshot of every table
        this backend owns. Raises ``NotImplementedError`` by default --
        see ``RuntimeStorage.export_storage`` (``storage.py``) for the
        full rationale, identical here.
        """
        raise NotImplementedError

    async def import_storage(self, data: dict, mode: str = "merge") -> None:
        """
        Restore a snapshot produced by ``export_storage``.
        Raises ``NotImplementedError`` by default -- see
        ``RuntimeStorage.import_storage`` (``storage.py``).
        """
        raise NotImplementedError

    async def list_sessions_modified_before(
        self,
        cutoff: Any,
        limit: int = 1000,
    ) -> list[Any]:
        """Return sessions with modified_at < cutoff. Empty by default."""
        return []

    async def list_sessions_modified_since(
        self,
        watermark: Any,
        limit: int = 1000,
    ) -> list[Any]:
        """
        Return sessions with modified_at >= watermark, oldest first.
        Empty by default -- see ``RuntimeStorage.list_sessions_modified_since``
        (``storage.py``) for the full rationale, identical here.
        """
        return []

    async def archive_session(self, session: Any) -> None:
        """Archive a session and remove it from active storage. No-op by default."""

    #
    # ------------------------------------------------------------------
    # Graph registry (Memory Agent v1)
    # ------------------------------------------------------------------
    #

    async def register_graph(
        self,
        name: str,
        session_id: str,
        description: str = "",
        tags: str = "",
        public: bool = False,
        source_graph_name: str | None = None,
        visibility: str | None = None,
        team: str | None = None,
    ) -> None:
        """
        Register (or update) a ``name -> session_id`` mapping. No-op by
        default -- see ``RuntimeStorage.register_graph`` (``storage.py``)
        for the full rationale, identical here.
        """

    async def get_graph(self, name: str) -> dict | None:
        """
        Look up a registered graph by name. ``None`` by default -- see
        ``RuntimeStorage.get_graph`` (``storage.py``).
        """
        return None

    async def list_graphs(
        self,
        tag: str | None = None,
        public_only: bool = False,
        team: str | None = None,
    ) -> list[dict]:
        """
        List registered graphs, optionally filtered by tag and/or to
        only public graphs, and/or (Memory Agent v3) to graphs scoped to
        ``team``. Empty by default -- see ``RuntimeStorage.list_graphs``
        (``storage.py``).
        """
        return []