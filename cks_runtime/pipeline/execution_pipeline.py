"""
Runtime Execution Pipeline.

Owns Runtime execution orchestration.

The Pipeline coordinates Runtime subsystems while
remaining completely independent from semantic logic.

Semantic behaviour belongs exclusively to CKS Core.
"""

from __future__ import annotations

from cks_runtime.core_api.validation_result import (
    RuntimeValidationResult,
)
from cks_runtime.transaction.transaction import (
    RuntimeTransaction,
)
from cks_runtime.versioning.version import (
    RuntimeVersion,
)


class ExecutionPipeline:
    """
    Coordinates Runtime execution.

    Managers own implementation.

    Pipeline owns execution order.
    """

    def __init__(
        self,
        runtime,
    ) -> None:
        self._runtime = runtime

    def commit(
        self,
        transaction: RuntimeTransaction,
    ) -> RuntimeVersion:
        """
        Execute Runtime commit pipeline.

        Current execution order:

            validate

                ↓

            collect diagnostics

                ↓

            quality gate

                ↓

            create version

                ↓

            persist version

                ↓

            persist session

                ↓

            commit transaction
        """

        validation = RuntimeValidationResult(
            valid=True,
        )

        #
        # Semantic validation.
        #

        if self._runtime.has_core:

            validation = (
                self._runtime.core_adapter.validate(
                    transaction.session.knowledge_structure,
                )
            )

        #
        # Aggregate diagnostics.
        #

        if validation.has_diagnostics:

            self._runtime.diagnostics.extend(
                validation.diagnostics,
            )

        #
        # Quality gate.
        #

        if not validation.valid:

            self.rollback(
                transaction,
            )

            raise RuntimeError(
                "Runtime commit aborted because semantic validation failed."
            )

        #
        # Create Runtime Version.
        #

        version = self._runtime.versions.create(
            transaction.session,
        )

        #
        # Persist Runtime state.
        #

        self._runtime.storage.save_version(
            version,
        )

        self._runtime.storage.save_session(
            transaction.session,
        )

        #
        # Finish Transaction.
        #

        self._runtime.transactions.commit(
            transaction,
        )

        return version

    def rollback(
        self,
        transaction: RuntimeTransaction,
    ) -> None:
        """
        Execute Runtime rollback pipeline.
        """

        self._runtime.transactions.rollback(
            transaction,
        )

        self._runtime.storage.save_session(
            transaction.session,
        )

    def abort(
        self,
        transaction: RuntimeTransaction,
    ) -> None:
        """
        Execute Runtime abort pipeline.
        """

        self._runtime.transactions.abort(
            transaction,
        )

        self._runtime.storage.save_session(
            transaction.session,
        )