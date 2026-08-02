"""
Canonical Runtime.

SPEC-001 Runtime Overview.

Runtime coordinates operational behaviour.

Runtime never owns semantics.
"""

from __future__ import annotations

from typing import Any

from cks_runtime.config import RuntimeConfig
from cks_runtime.core_api.bridge import (
    CoreBridge,
)
from cks_runtime.core_api.interfaces import (
    CoreInterface,
)
from cks_runtime.diagnostics.aggregator import (
    DiagnosticAggregator,
)
from cks_runtime.dispatcher.dispatcher import Dispatcher
from cks_runtime.embedding.client import EmbeddingClient, StubEmbeddingClient
from cks_runtime.events.event_bus import EventBus
from cks_runtime.events.runtime_event import SessionClosed, SessionCreated
from cks_runtime.execution.operation_executor import OperationExecutor
from cks_runtime.gc.garbage_collector import GarbageCollector
from cks_runtime.metrics.collector import MetricsCollector
from cks_runtime.operations.operation_registry import OperationRegistry
from cks_runtime.pipeline.execution_pipeline import (
    ExecutionPipeline,
)
from cks_runtime.projection.embedding_projection import EmbeddingProjection
from cks_runtime.projection.outbox_worker import OutboxEmbeddingWorker
from cks_runtime.session.session import (
    RuntimeSession,
)
from cks_runtime.session.session_manager import (
    SessionManager,
)
from cks_runtime.storage.adapter import SyncStorageAdapter
from cks_runtime.storage.async_storage import AsyncRuntimeStorage
from cks_runtime.storage.memory_storage import (
    InMemoryStorage,
)
from cks_runtime.storage.sqlite_storage import SQLiteStorage
from cks_runtime.storage.storage import (
    RuntimeStorage,
)
from cks_runtime.transaction.transaction import (
    RuntimeTransaction,
)
from cks_runtime.transaction.transaction_manager import (
    TransactionManager,
)
from cks_runtime.versioning.version import (
    RuntimeVersion,
)
from cks_runtime.versioning.version_manager import (
    VersionManager,
)


def _resolve_storage(
    storage: RuntimeStorage | AsyncRuntimeStorage | None,
    config: RuntimeConfig,
) -> AsyncRuntimeStorage:
    """
    Resolve the storage backend Runtime will actually talk to.

    Runtime is async end-to-end and always awaits an
    ``AsyncRuntimeStorage``. Three cases:

    - An ``AsyncRuntimeStorage`` was passed explicitly (e.g.
      ``PostgresStorage``) -- use it as-is.
    - A synchronous ``RuntimeStorage`` was passed explicitly, or none
      was and ``config.storage_path`` selects the in-memory/SQLite
      default -- wrap it in ``SyncStorageAdapter`` so every call is
      still genuinely non-blocking (dispatched via
      ``asyncio.to_thread``), not just type-compatible.
    - ``config.storage_path`` is a ``postgres://``/``postgresql://``
      DSN and no explicit ``storage`` was given -- this function only
      resolves the *type* of default; the actual async connection
      happens in ``Runtime.create()``, which is why this returns
      ``None`` for that one case instead of connecting here (this
      function is not itself async).
    """
    if storage is not None:
        if isinstance(storage, AsyncRuntimeStorage):
            return storage
        return SyncStorageAdapter(storage)

    if config.storage_path == ":memory:":
        return SyncStorageAdapter(InMemoryStorage())

    if config.storage_path.startswith(("postgres://", "postgresql://")):
        raise ValueError(
            "A postgres:// storage_path requires the async "
            "Runtime.create(...) constructor, not Runtime(...) "
            "directly -- connecting requires an awaited call."
        )

    return SyncStorageAdapter(SQLiteStorage(config.storage_path))


class Runtime:
    """
    Canonical Runtime façade.

    Runtime owns:

        - orchestration;
        - lifecycle;
        - transactions;
        - persistence;
        - execution flow.

    Runtime does not own:

        - semantic rules;
        - validation logic;
        - knowledge interpretation.

    Semantic behaviour belongs to Core plugins.

    Runtime is async end-to-end: every method that touches storage
    (directly, or by way of the execution pipeline/operations) is a
    coroutine. Construction is split in two because of this --
    ``__init__`` does synchronous wiring only (no I/O), and the
    ``async def create(...)`` classmethod does the awaited part
    (restoring persisted sessions, starting the background outbox
    worker, and -- for a ``postgres://`` storage_path -- opening the
    connection pool). Use ``Runtime(...)`` directly only when you
    already have a storage instance and don't need startup restore
    (e.g. many unit tests construct a fresh in-memory Runtime and
    don't care about ``_restore_from_storage``); everything else,
    including any real deployment, should go through
    ``await Runtime.create(...)``.
    """

    __slots__ = (
        "_core_bridge",
        "_diagnostics",
        "_dispatcher",
        "_embedding_client",
        "_embedding_projection",
        "_events",
        "_executor",
        "_gc",
        "_metrics",
        "_outbox_worker",
        "_pipeline",
        "_registry",
        "_replica_id",
        "_sessions",
        "_storage",
        "_transactions",
        "_versions",
        "config",
    )

    def __init__(
        self,
        *,
        core: CoreInterface | None = None,
        storage: RuntimeStorage | AsyncRuntimeStorage | None = None,
        config: RuntimeConfig | None = None,
        embedding_client: EmbeddingClient | None = None,
    ) -> None:

        self.config = (
            config
            if config is not None
            else RuntimeConfig()
        )

        #
        # Semantic boundary
        #

        self._core_bridge = CoreBridge(
            core,
        )

        #
        # Infrastructure
        #

        self._storage = _resolve_storage(storage, self.config)

        # Durable per-process identity (ADR-008 §1), distinct from the
        # per-session node_id SessionManager mints below. None until
        # sourced from storage in create() -- a bare Runtime(...) (many
        # existing unit tests construct one this way, with no running
        # event loop / async startup) sees no behaviour change, exactly
        # like node_id-less VersionManager.create() calls today.
        self._replica_id: str | None = None

        #
        # Runtime subsystems
        #

        self._sessions = SessionManager()

        self._transactions = TransactionManager()

        self._versions = VersionManager()

        self._diagnostics = DiagnosticAggregator()

        self._pipeline = ExecutionPipeline(
            self,
        )

        self._events = EventBus()
        self._metrics = MetricsCollector()

        # Resolved once, here, and shared verbatim between the outbox
        # worker (which embeds and indexes objects) and whatever reads
        # Runtime.embedding_client to embed a query (e.g. cks-mcp's
        # search_semantic tool). Previously this fallback lived only
        # inside OutboxEmbeddingWorker's own __init__, and the client
        # instance itself was never stored on Runtime -- so a caller
        # embedding a query had no way to reach the same client used
        # to index, and would silently encode the query with a
        # different (or non-semantic Stub) embedding space, making
        # every similarity search meaningless without ever raising an
        # error.
        self._embedding_client = embedding_client or StubEmbeddingClient()

        # Projections
        self._embedding_projection = EmbeddingProjection(
            event_bus=self._events,
            storage=self._storage,
        )
        self._embedding_projection.start()
        self._outbox_worker = OutboxEmbeddingWorker(
            storage=self._storage,
            core_bridge=self._core_bridge,
            embedding_client=self._embedding_client,
        )
        # NOTE: OutboxEmbeddingWorker.start() is now async (it creates
        # an asyncio.Task, which requires a running event loop) and is
        # therefore NOT called here -- see create()/astart() below.
        # A Runtime constructed via bare Runtime(...) has no running
        # outbox worker until one of those is awaited.

        # Garbage collector: evicts stale closed sessions from storage.
        # Disabled when config.gc_retention is None.
        # Also started lazily in create() — requires a running event loop.
        self._gc: GarbageCollector | None = None
        if self.config.gc_retention is not None:
            self._gc = GarbageCollector(
                self._storage,
                retention=self.config.gc_retention,
                sweep_interval=self.config.gc_sweep_interval,
                batch_size=self.config.gc_batch_size,
            )

        # Сначала создаём executor, потому что dispatcher зависит от него
        self._executor = OperationExecutor(
            core_adapter=self._core_bridge, metrics=self._metrics, storage=self._storage
        )

        self._registry = OperationRegistry()
        self._dispatcher = Dispatcher(
            registry=self._registry,
            executor=self._executor,
        )

    @classmethod
    async def create(
        cls,
        *,
        core: CoreInterface | None = None,
        storage: RuntimeStorage | AsyncRuntimeStorage | None = None,
        config: RuntimeConfig | None = None,
        embedding_client: EmbeddingClient | None = None,
    ) -> Runtime:
        """
        Construct a Runtime and complete its async startup:

        - if ``storage`` is omitted and ``config.storage_path`` is a
          ``postgres://``/``postgresql://`` DSN, opens a
          ``PostgresStorage`` connection pool for it (``psycopg`` is
          imported lazily here, so it stays an optional dependency for
          callers on the in-memory/SQLite defaults);
        - restores persisted sessions from storage
          (``_restore_from_storage``);
        - starts the background outbox embedding worker.

        This is the constructor real deployments should use. Plain
        ``Runtime(...)`` is still available for callers (many existing
        unit tests) that don't need startup restore or the outbox
        worker running.
        """
        resolved_config = config if config is not None else RuntimeConfig()

        resolved_storage = storage
        if resolved_storage is None and resolved_config.storage_path.startswith(
            ("postgres://", "postgresql://")
        ):
            # Lazy import: psycopg is an optional dependency
            # (``cks-runtime[postgres]``) and must not become a hard
            # import for callers on the in-memory/SQLite defaults.
            from cks_runtime.storage.postgres_storage import PostgresStorage

            resolved_storage = await PostgresStorage.connect(resolved_config.storage_path)

        runtime = cls(
            core=core,
            storage=resolved_storage,
            config=resolved_config,
            embedding_client=embedding_client,
        )

        # Sourced once per process lifetime, here -- the one place
        # ADR-008 §4 (SPEC-009) designates as responsible for durability
        # across restarts. A backend without gossip support (its
        # get_or_create_replica_id() default) returns None; that's the
        # correct "not a distinguishable gossip peer" outcome, not an
        # error, so it's stored as-is rather than substituted.
        runtime._replica_id = await runtime._storage.get_or_create_replica_id()

        await runtime._restore_from_storage()
        await runtime._outbox_worker.start()

        if runtime._gc is not None:
            await runtime._gc.start()

        return runtime

    #
    # ------------------------------------------------------------------
    # Public subsystem access
    # ------------------------------------------------------------------
    #

    @property
    def core_bridge(
        self,
    ) -> CoreBridge:
        """
        Runtime ↔ Core boundary.
        """

        return self._core_bridge


    @property
    def storage(
        self,
    ) -> AsyncRuntimeStorage:
        """
        Runtime storage backend.
        """

        return self._storage

    @property
    def replica_id(
        self,
    ) -> str | None:
        """
        Durable per-process identity (ADR-008 §1), or ``None`` for a
        bare ``Runtime(...)`` that never ran ``create()``'s async
        startup, or whose storage backend doesn't support gossip
        (``get_or_create_replica_id()`` returning ``None`` is the
        documented "not a distinguishable gossip peer" outcome).
        Distinct from the per-session ``node_id`` in
        ``session.metadata`` -- see ``VersionVector`` / ``GossipAdapter``.
        """

        return self._replica_id


    @property
    def diagnostics(
        self,
    ) -> DiagnosticAggregator:
        """
        Runtime diagnostic collector.
        """

        return self._diagnostics


    @property
    def pipeline(
        self,
    ) -> ExecutionPipeline:
        """
        Runtime execution pipeline.
        """

        return self._pipeline
    

    @property
    def dispatcher(self) -> Dispatcher:
        """
        Runtime operation dispatcher.
        """
        return self._dispatcher

    @property
    def operation_registry(self) -> OperationRegistry:
        """
        Runtime operation registry.
        """
        return self._registry
    

    @property
    def executor(self) -> OperationExecutor:
        """
        Runtime operation executor.
        """
        return self._executor

    @property
    def transactions(self) -> TransactionManager:
        """Runtime transaction manager."""
        return self._transactions

    @property
    def versions(self) -> VersionManager:
        """Runtime version manager."""
        return self._versions

    @property
    def sessions(self) -> SessionManager:
        """Runtime session manager."""
        return self._sessions


    @property
    def has_core(
        self,
    ) -> bool:
        """
        Whether a semantic Core is attached.
        """

        return self._core_bridge.available
    

    @property
    def events(self) -> EventBus:
        """Runtime event bus."""
        return self._events


    @property
    def metrics(self) -> MetricsCollector:
        """Runtime metrics collector."""
        return self._metrics


    @property
    def embedding_client(self) -> EmbeddingClient:
        """
        The embedding client used to index objects (via the outbox
        worker). Callers that need to embed a query for similarity
        search -- e.g. cks-mcp's search_semantic tool -- must use this
        same instance, not a freshly-constructed client, or the query
        vector will not be comparable to what was actually indexed.
        """
        return self._embedding_client


    #
    # ------------------------------------------------------------------
    # Session façade
    # ------------------------------------------------------------------
    #

    async def create_session(
        self,
        knowledge_structure: Any,
    ) -> RuntimeSession:
        """
        Create and persist a Runtime Session.
        """

        session = self._sessions.create_session(
            knowledge_structure,
        )

        await self._storage.save_session(
            session,
        )

        await self._events.publish(
            SessionCreated(session_id=session.session_id),
        )

        return session


    async def create_branch(
        self,
        session: RuntimeSession,
        *,
        version_id: str | None = None,
    ) -> RuntimeSession:
        """
        Create and persist a branch of ``session``.

        When ``version_id`` is given, the branch starts from that
        historical version, reconstructed via
        ``RuntimeSession.get_version_state`` (which may need
        ``core_bridge`` to replay delta versions past the nearest
        snapshot) -- and that same ``version_id`` is recorded on the
        branch as its ``parent_version_id``, so a later merge can find
        this exact fork point again. When omitted, the branch starts
        from ``session``'s current, possibly uncommitted, state. In
        that case ``parent_version_id`` still defaults to ``session``'s
        latest committed version whenever that version is provably
        equal to the current state -- i.e. ``session`` has at least one
        version and no transaction is active, which after any
        successful commit means ``session.knowledge_structure`` is
        exactly what that version recorded (see VersionManager.create,
        which snapshots/hashes the session's live structure at commit
        time). This is the common case (branching right after
        validate/evolve/merge). Only when neither holds -- an empty
        session, or one with a transaction still in flight -- is
        ``parent_version_id`` left unset, since no committed version
        can then be guaranteed to match the current state.
        """

        if version_id is not None:
            structure = session.get_version_state(
                version_id,
                self._core_bridge,
            )
            fork_version_id = version_id
        else:
            structure = session.knowledge_structure
            if session.has_versions and not session.has_active_transaction:
                fork_version_id = session.version_history[-1].version_id
            else:
                fork_version_id = None

        branch = self._sessions.create_branch(
            session,
            structure,
            parent_version_id=fork_version_id,
        )

        await self._storage.save_session(
            branch,
        )

        await self._events.publish(
            SessionCreated(session_id=branch.session_id),
        )

        return branch


    def get_session(
        self,
        session_id: str,
    ) -> RuntimeSession | None:
        """
        Retrieve Runtime Session.
        """

        return self._sessions.get_session(
            session_id,
        )


    def list_sessions(
        self,
    ) -> tuple[RuntimeSession, ...]:
        """
        Return active Runtime Sessions.
        """

        return self._sessions.list_sessions()


    async def close_session(
        self,
        session_id: str,
    ) -> None:
        """
        Close Runtime Session.
        """

        session = self._sessions.get_session(
            session_id,
        )

        self._sessions.close_session(
            session_id,
        )

        # SessionManager.close_session() only updates the in-memory
        # registry (it calls session.close(), which sets
        # session.closed = True, then discards the session from its
        # dict) -- it never touches storage. Without persisting here,
        # the closed session's on-disk record still shows closed=False,
        # so after any process restart, load_session() would
        # reconstruct it as an active session again, even though
        # SQLiteStorage.save_session/load_session already fully support
        # the 'closed' field. This mirrors create_session/create_branch,
        # which persist immediately for the same reason.
        if session is not None:
            await self._storage.save_session(
                session,
            )

            await self._events.publish(
                SessionClosed(session_id=session_id),
            )


    #
    # ------------------------------------------------------------------
    # Transaction façade
    # ------------------------------------------------------------------
    #

    def begin_transaction(
        self,
        session: RuntimeSession,
    ) -> RuntimeTransaction:
        """
        Begin Runtime Transaction.
        """

        return self._transactions.begin(
            session,
        )


    async def commit_transaction(
        self,
        transaction: RuntimeTransaction,
    ) -> RuntimeVersion:
        """
        Commit Runtime Transaction.
        """

        return await self._pipeline.commit(
            transaction,
        )


    async def rollback_transaction(
        self,
        transaction: RuntimeTransaction,
    ) -> None:
        """
        Rollback Runtime Transaction.
        """

        await self._pipeline.rollback(
            transaction,
        )


    async def abort_transaction(
        self,
        transaction: RuntimeTransaction,
    ) -> None:
        """
        Abort Runtime Transaction.
        """

        await self._pipeline.abort(
            transaction,
        )


    #
    # ------------------------------------------------------------------
    # Version façade
    # ------------------------------------------------------------------
    #

    def latest_version(
        self,
        session: RuntimeSession,
    ) -> RuntimeVersion | None:
        """
        Return latest Runtime Version.
        """

        return self._versions.latest(
            session,
        )

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        """
        Graceful shutdown: stop the outbox worker's background task
        and close the storage backend if it owns a resource that needs
        closing (e.g. ``PostgresStorage``'s connection pool). Safe to
        call even when the worker was never started (bare
        ``Runtime(...)`` construction).
        """
        await self._outbox_worker.stop()

        if self._gc is not None:
            await self._gc.stop()

        close = getattr(self._storage, "close", None)
        if close is not None:
            await close()

    # ------------------------------------------------------------------
    # Restore persisted sessions and versions at startup
    # ------------------------------------------------------------------

    async def _restore_from_storage(self) -> None:
        """
        Load all sessions from the attached storage and register them
        with the in-memory managers.

        Version history is already restored by SQLiteStorage.load_session,
        so we only need to register the sessions here.
        """
        stored_sessions = await self._storage.list_sessions()
        for session in stored_sessions:
            self._sessions.restore(session)
