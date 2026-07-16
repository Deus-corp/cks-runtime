"""
Canonical Runtime.

SPEC-001 Runtime Overview.

Runtime coordinates operational behaviour.

Runtime never owns semantics.
"""

from __future__ import annotations

from typing import Any

from cks_runtime.config import RuntimeConfig
from cks_runtime.core_api.interfaces import CoreInterface
from cks_runtime.diagnostics.aggregator import (
    DiagnosticAggregator,
)
from cks_runtime.session.session import RuntimeSession
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
from cks_runtime.pipeline.execution_pipeline import (
    ExecutionPipeline,
)
from cks_runtime.core_api.adapter import (
    CoreAdapter,
)


class Runtime:
    """
    Canonical Runtime façade.

    Runtime owns orchestration.

    Subsystems own implementation.

    Runtime is the single public entry point
    into Runtime behaviour.
    """

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

        self.core = core

        self.core_adapter = CoreAdapter(
            self.core,
        )

        self.storage = (
            storage
            if storage is not None
            else InMemoryStorage()
        )

        self.sessions = SessionManager()

        self.transactions = TransactionManager()

        self.versions = VersionManager()

        self.diagnostics = DiagnosticAggregator()

        self.pipeline = ExecutionPipeline(
            self,
        )

    @property
    def has_core(self) -> bool:
        """
        Whether a semantic Core is attached.
        """

        return self.core_adapter.attached

    #
    # Session façade
    #

    def create_session(
        self,
        knowledge_structure: Any,
    ) -> RuntimeSession:
        """
        Create and persist a Runtime Session.
        """

        session = self.sessions.create_session(
            knowledge_structure
        )

        self.storage.save_session(
            session
        )

        return session

    def get_session(
        self,
        session_id: str,
    ) -> RuntimeSession | None:
        """
        Retrieve an active Runtime Session.
        """

        return self.sessions.get_session(
            session_id
        )

    def list_sessions(
        self,
    ) -> list[RuntimeSession]:
        """
        Return active Runtime Sessions.
        """

        return self.sessions.list_sessions()

    def close_session(
        self,
        session_id: str,
    ) -> None:
        """
        Close a Runtime Session.
        """

        self.sessions.close_session(
            session_id
        )

    #
    # Transaction façade
    #

    def begin_transaction(
        self,
        session: RuntimeSession,
    ) -> RuntimeTransaction:
        """
        Begin a Runtime Transaction.
        """

        return self.transactions.begin(
            session
        )

    def commit_transaction(
        self,
        transaction: RuntimeTransaction,
    ) -> RuntimeVersion:
        """
        Commit a Runtime Transaction.
        """

        return self.pipeline.commit(
            transaction,
        )

    def rollback_transaction(
        self,
        transaction: RuntimeTransaction,
    ) -> None:
        """
       Roll back a Runtime Transaction.
        """

        self.pipeline.rollback(
            transaction,
        )

    def abort_transaction(
        self,
        transaction: RuntimeTransaction,
    ) -> None:
        """
        Abort a Runtime Transaction.
        """

        self.pipeline.abort(
            transaction,
        )

    #
    # Version façade
    #

    def latest_version(
        self,
        session: RuntimeSession,
    ) -> RuntimeVersion | None:
        """
        Return the latest Runtime Version.
        """

        return self.versions.latest(
            session
        )