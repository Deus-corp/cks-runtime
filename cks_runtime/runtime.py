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

from cks_runtime.pipeline.execution_pipeline import (
    ExecutionPipeline,
)

from cks_runtime.session.session import (
    RuntimeSession,
)
from cks_runtime.session.session_manager import (
    SessionManager,
)

from cks_runtime.storage.memory_storage import (
    InMemoryStorage,
)
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
from cks_runtime.execution.operation_executor import OperationExecutor
from cks_runtime.dispatcher.dispatcher import Dispatcher
from cks_runtime.operations.operation_registry import OperationRegistry
from cks_runtime.events.event_bus import EventBus


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
    """

    __slots__ = (
        "config",
        "_core_bridge",
        "_storage",
        "_sessions",
        "_transactions",
        "_versions",
        "_diagnostics",
        "_pipeline",
        "_executor",
        "_registry",
        "_dispatcher",
        "_events",
    )

    def __init__(
        self,
        *,
        core: CoreInterface | None = None,
        storage: RuntimeStorage | None = None,
        config: RuntimeConfig | None = None,
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

        self._storage = (
            storage
            if storage is not None
            else InMemoryStorage()
        )

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

        # Сначала создаём executor, потому что dispatcher зависит от него
        self._executor = OperationExecutor(core_adapter=self._core_bridge)

        self._registry = OperationRegistry()
        self._dispatcher = Dispatcher(
            registry=self._registry,
            executor=self._executor,
        )
        self._events = EventBus()

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
    ) -> RuntimeStorage:
        """
        Runtime storage backend.
        """

        return self._storage


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


    #
    # ------------------------------------------------------------------
    # Session façade
    # ------------------------------------------------------------------
    #

    def create_session(
        self,
        knowledge_structure: Any,
    ) -> RuntimeSession:
        """
        Create and persist a Runtime Session.
        """

        session = self._sessions.create_session(
            knowledge_structure,
        )

        self._storage.save_session(
            session,
        )

        return session


    def create_branch(
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
        from ``session``'s current, possibly uncommitted, state, and
        ``parent_version_id`` is left unset.
        """

        if version_id is not None:
            structure = session.get_version_state(
                version_id,
                self._core_bridge,
            )
        else:
            structure = session.knowledge_structure

        branch = self._sessions.create_branch(
            session,
            structure,
            parent_version_id=version_id,
        )

        self._storage.save_session(
            branch,
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


    def close_session(
        self,
        session_id: str,
    ) -> None:
        """
        Close Runtime Session.
        """

        self._sessions.close_session(
            session_id,
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


    def commit_transaction(
        self,
        transaction: RuntimeTransaction,
    ) -> RuntimeVersion:
        """
        Commit Runtime Transaction.
        """

        return self._pipeline.commit(
            transaction,
        )


    def rollback_transaction(
        self,
        transaction: RuntimeTransaction,
    ) -> None:
        """
        Rollback Runtime Transaction.
        """

        self._pipeline.rollback(
            transaction,
        )


    def abort_transaction(
        self,
        transaction: RuntimeTransaction,
    ) -> None:
        """
        Abort Runtime Transaction.
        """

        self._pipeline.abort(
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
