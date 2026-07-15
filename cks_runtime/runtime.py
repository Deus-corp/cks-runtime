"""
Canonical Runtime.

SPEC-001 Runtime Overview.

Runtime coordinates operational behaviour.

Runtime never owns semantics.
"""

from __future__ import annotations

from cks_runtime.config import RuntimeConfig

from cks_runtime.core.interfaces import CoreInterface

from cks_runtime.diagnostics.aggregator import (
    DiagnosticAggregator,
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

from cks_runtime.transaction.transaction_manager import (
    TransactionManager,
)

from cks_runtime.versioning.version_manager import (
    VersionManager,
)


class Runtime:
    """
    Canonical Runtime.

    Coordinates Runtime subsystems.

    Ownership:

    - Sessions;
    - Transactions;
    - Version History;
    - Diagnostics;
    - Storage.

    Runtime never owns semantic behaviour.
    Semantic behaviour belongs exclusively
    to CKS Core.
    """

    def __init__(
        self,
        *,
        core: CoreInterface | None = None,
        storage: RuntimeStorage | None = None,
        config: RuntimeConfig | None = None,
    ) -> None:

        self.config = config or RuntimeConfig()

        self.core = core

        self.storage = (
            storage
            if storage is not None
            else InMemoryStorage()
        )

        self.sessions = SessionManager()

        self.transactions = TransactionManager()

        self.versions = VersionManager()

        self.diagnostics = DiagnosticAggregator()