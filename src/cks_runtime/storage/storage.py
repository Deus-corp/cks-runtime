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

    def list_dead_letter_tasks(
        self, task_type: str | None = None, session_id: str | None = None
    ) -> list[OutboxTask]:
        """
        Return every ``DEAD``-lettered task (optionally filtered to one
        ``task_type`` and/or one ``session_id``), oldest first, for
        inspection -- never drains. Empty by default -- backends that
        support the outbox override this.
        """
        return []

    def prune_agent_liveness(self, older_than_seconds: float) -> int:
        """
        Delete liveness rows whose last heartbeat is older than
        ``older_than_seconds`` -- cks_agent_liveness is an append/upsert
        table with no natural expiry (see ADR-014), so processes that
        died without a clean shutdown accumulate forever otherwise.
        Returns the number of rows removed. No-op (returns 0) by
        default -- backends that support agent liveness override this.
        """
        return 0

    def upsert_agent_liveness(self, record: AgentLivenessRecord) -> None:
        """
        Write/refresh one standalone-agent process instance's liveness
        heartbeat row (see cks-runtime ADR-014). Called once at process
        startup and then periodically (every ``liveness_interval``
        seconds) by each of the four standalone agent processes
        (Critic, Enrichment, Fork Resolution, Pipeline) -- not related
        to the outbox task-lease heartbeat (``touch_outbox_task``),
        which is a different mechanism for a different failure mode
        (see ADR-014's Context section). No-op by default -- backends
        that support agent liveness override this.
        """

    def list_agent_liveness(self) -> list[AgentLivenessRecord]:
        """
        Return every known standalone-agent process instance, most
        recently started first. Liveness (``alive``/``stopped``) is
        computed by the caller from ``last_heartbeat_at`` and
        ``liveness_interval_s`` (TTL = 3x interval, see ADR-014 §3),
        not stored as a column, so a slow reader doesn't see a stale
        cached verdict. Empty by default -- backends that support
        agent liveness override this.
        """
        return []

    def get_agent_liveness(self, instance_id: str) -> AgentLivenessRecord | None:
        """
        Return the single liveness row for ``instance_id``, or ``None``
        if no such row exists (ADR-016 §2). A targeted single-row read,
        used by ``LivenessReporter``'s own tick to check its own
        ``desired_state`` without scanning the whole table on every
        ``liveness_interval``. ``None`` by default -- backends that
        support agent liveness override this.
        """
        return None

    def request_agent_stop(self, instance_id: str) -> bool:
        """
        Set ``desired_state='stop_requested'`` for the liveness row
        with this ``instance_id`` (ADR-016 §1). A single-column
        ``UPDATE``, not an upsert -- it must never create a row (only
        ``upsert_agent_liveness``, owned by the process itself, does
        that), and it does not touch ``last_heartbeat_at``. Returns
        ``False`` if no row with this ``instance_id`` exists (already
        gone, or never existed) -- same not-an-error convention as
        ``touch_outbox_task``'s lease-renewal return value. ``False``
        by default -- backends that support agent liveness override
        this.
        """
        return False

    @property
    def supports_agent_liveness(self) -> bool:
        """Whether this storage backend supports agent liveness tracking."""
        return False

    def set_sweeper_desired_running(self, agent_id: str, desired_running: bool) -> None:
        """
        Persist a manual override of whether ``agent_id`` (an
        in-process reasoning sweeper) should be running (ADR-015 §1).
        One row per sweeper that has ever had its default overridden --
        absence of a row means "config default applies". No-op by
        default -- backends that support sweeper control override this.
        """

    def get_sweeper_desired_running(self, agent_id: str) -> bool | None:
        """
        Return the stored override for ``agent_id``, or ``None`` if no
        override row exists (config default applies -- see
        ``set_sweeper_desired_running``). ``None`` by default --
        backends that support sweeper control override this.
        """
        return None

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

    # ------------------------------------------------------------------
    # Backup / Disaster Recovery (ADR-012)
    # ------------------------------------------------------------------

    def export_storage(self) -> dict:
        """
        Return a complete, JSON-serialisable snapshot of every table
        this backend owns (sessions, versions, graph registry,
        embeddings, outbox tasks).

        The returned dict is backend-agnostic: any backend that
        implements ``import_storage`` can restore from it regardless of
        which backend produced it. Raises ``NotImplementedError`` by
        default -- backends that support backup override this.
        """
        raise NotImplementedError

    def import_storage(self, data: dict, mode: str = "merge") -> None:
        """
        Restore a snapshot produced by ``export_storage`` into this backend.

        ``mode`` controls collision handling:

        - ``"clear"``  — truncate every table first, then insert the
          snapshot. Use for disaster recovery.
        - ``"merge"`` — insert only rows whose primary key doesn't yet
          exist in the target; skip duplicates silently. Use for
          migrating or merging stores.

        Raises ``NotImplementedError`` by default -- backends that
        support backup override this.
        """
        raise NotImplementedError

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

    #
    # ------------------------------------------------------------------
    # Graph registry (Memory Agent v1)
    # ------------------------------------------------------------------
    #

    def register_graph(
        self,
        name: str,
        session_id: str,
        description: str = "",
        tags: str = "",
        public: bool = False,
        source_graph_name: str | None = None,
        visibility: str | None = None,
        team: str | None = None,
        lifecycle_state: str | None = None,
    ) -> None:
        """
        Register (or update) a ``name -> session_id`` mapping so a
        previously-built Knowledge Graph can be looked up by a
        memorable name in a later session, instead of being rebuilt
        from scratch. Registering an already-used ``name`` replaces
        its existing entry. No-op by default -- backends that support
        the graph registry override this.

        ``public`` (Memory Agent v2) marks the graph as eligible for
        the gallery -- discoverable via ``list_graphs(public_only=True)``
        / ``search_graphs`` by callers other than the one that
        registered it. Defaults to ``False`` for backward
        compatibility with existing graphs and callers.

        ``visibility`` (Memory Agent v3) is a three-way scope --
        ``'private'`` (default), ``'team'``, or ``'public'`` -- that
        supersedes ``public`` when given. ``team`` is the namespace a
        ``'team'``-visibility graph is scoped to; there is no
        authentication behind it, it's a caller-supplied namespace like
        the registry ``name`` itself.

        ``source_graph_name`` (clone lineage) records the registry name
        this graph was cloned from, typically set by
        ``clone_graph(copy_name=...)``. ``None`` leaves any existing
        lineage on this name untouched rather than clearing it, so a
        plain re-register (e.g. via ``update_registered_graph``) can't
        accidentally erase where a graph was forked from.

        ``lifecycle_state`` (Graph Lifecycle) is one of ``'draft'``,
        ``'published'``, ``'active'``, ``'stale'``, ``'under_review'``,
        or ``'archived'``. ``None`` leaves any existing lifecycle state
        on this name untouched (same "don't clobber on plain
        re-register" rationale as ``source_graph_name``); a first-time
        registration with no explicit value defaults to ``'published'``
        when ``public``/``visibility='public'`` is set, otherwise
        ``'draft'``.
        """

    def get_graph(self, name: str) -> dict | None:
        """
        Look up a registered graph by name. Returns a dict with keys
        ``name``, ``session_id``, ``description``, ``tags``,
        ``public``, ``visibility``, ``team``, ``source_graph_name``,
        ``lifecycle_state``, ``created_at``, ``updated_at``, or
        ``None`` if no graph is registered under that name. ``None``
        by default -- backends that support the graph registry
        override this.
        """
        return None

    def unregister_graph(self, name: str) -> bool:
        """
        Remove a registered ``name -> session_id`` mapping from the
        graph registry. Returns ``True`` if an entry existed under
        ``name`` and was removed, ``False`` otherwise. ``False`` by
        default -- backends that support the graph registry override
        this.

        This only removes the registry entry; it does not delete the
        underlying session or its Knowledge Structure, which remain
        addressable by session id.
        """
        return False

    def list_graphs(
        self,
        tag: str | None = None,
        public_only: bool = False,
        team: str | None = None,
    ) -> list[dict]:
        """
        List every registered graph, optionally filtered to those
        whose ``tags`` field contains ``tag``, and/or (Memory Agent v2)
        to only ``public`` graphs, and/or (Memory Agent v3) to graphs
        visible to ``team`` (public graphs plus this team's
        ``visibility='team'`` graphs). Empty by default -- backends
        that support the graph registry override this.
        """
        return []


@dataclass(frozen=True, slots=True)
class AgentLivenessRecord:
    """
    One standalone-agent process instance's liveness row (see
    cks-runtime ADR-014). ``instance_id`` is a fresh uuid4 generated
    once per process start -- a restarted process gets a new row, the
    old one is kept as history, not overwritten.
    """
    instance_id: str
    process_kind: str  # 'critic' | 'enrichment' | 'fork_resolution' | 'pipeline'
    hostname: str
    pid: int
    liveness_interval_s: float
    started_at: str
    last_heartbeat_at: str
    current_task_id: int | None = None
    current_task_type: str | None = None
    # ADR-016: NULL/'running' = no stop requested (default);
    # 'stop_requested' = pending. Written only by request_agent_stop,
    # never by upsert_agent_liveness (different actor/process -- see
    # ADR-016 §1).
    desired_state: str | None = None


@dataclass(frozen=True, slots=True)
class OutboxTask:
    """A task read from the outbox table."""
    task_id: int
    task_type: str
    session_id: str
    payload: str
    retry_count: int = 0
    last_error: str | None = None