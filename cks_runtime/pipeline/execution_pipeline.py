"""
Runtime Execution Pipeline.

Owns Runtime execution orchestration.

The Pipeline coordinates Runtime subsystems while remaining
completely independent from semantic logic.

Semantic behaviour belongs exclusively to CKS Core.
"""

from __future__ import annotations

from cks_runtime.core_api.validation_result import RuntimeValidationResult
from cks_runtime.transaction.transaction import RuntimeTransaction
from cks_runtime.versioning.version import RuntimeVersion
from cks_runtime.execution.operation_executor import OperationStatus
from cks_runtime.operations.operation_types import ValidateOperation, EvolveOperation, RevertVersionOperation
from cks_runtime.execution.execution_context import ExecutionContext
from cks_runtime.events.runtime_event import (
    SessionCreated,
    TransactionCommitted,
    TransactionRolledBack,
    TransactionAborted,
    VersionCreated,
    ValidationFailed,
)
from typing import Any


class ExecutionPipeline:
    """
    Coordinates Runtime execution.

    The pipeline defines execution order only.

    Runtime managers own the implementation of each step.
    """

    def __init__(self, runtime) -> None:
        self._runtime = runtime

    #
    # ------------------------------------------------------------------
    # Commit
    # ------------------------------------------------------------------
    #

    def commit(
        self,
        transaction: RuntimeTransaction,
    ) -> RuntimeVersion:
        initial_state = transaction.session.knowledge_structure

        if transaction.operations or transaction.requests:
            self._execute_operations(transaction)
        else:
            validation = self._validate(transaction)
            self._collect_diagnostics(validation)
            self._quality_gate(validation, transaction)

        version = self._create_version(transaction, initial_state)
        self._persist(version, transaction)
        self._finalize(transaction)

        self._runtime.events.publish(
            TransactionCommitted(
                transaction_id=transaction.transaction_id,
                session_id=transaction.session.session_id,
            )
        )

        return version
    
    #
    # ------------------------------------------------------------------
    # Rollback
    # ------------------------------------------------------------------
    #

    def rollback(
        self,
        transaction: RuntimeTransaction,
    ) -> None:
        """
        Execute Runtime rollback.
        """

        self._runtime.transactions.rollback(transaction)

        self._runtime.storage.save_session(
            transaction.session,
        )

        self._runtime.events.publish(
            TransactionRolledBack(
                transaction_id=transaction.transaction_id,
                session_id=transaction.session.session_id,
            )
        )

    #
    # ------------------------------------------------------------------
    # Abort
    # ------------------------------------------------------------------
    #

    def abort(
        self,
        transaction: RuntimeTransaction,
    ) -> None:
        """
        Abort Runtime execution.
        """

        self._runtime.transactions.abort(transaction)

        self._runtime.storage.save_session(
            transaction.session,
        )

        self._runtime.events.publish(
            TransactionAborted(
                transaction_id=transaction.transaction_id,
                session_id=transaction.session.session_id,
            )
        )

    #
    # ==================================================================
    # Internal steps
    # ==================================================================
    #

    def _validate(
        self,
        transaction: RuntimeTransaction,
    ) -> RuntimeValidationResult:
        """
        Execute semantic validation.
        """

        return self._runtime.core_bridge.validate(
            transaction.session.knowledge_structure,
        )

    def _collect_diagnostics(
        self,
        validation: RuntimeValidationResult,
    ) -> None:
        """
        Aggregate validation diagnostics.
        """

        if validation.has_diagnostics:
            self._runtime.diagnostics.extend(
                validation.diagnostics,
            )

    def _quality_gate(
        self,
        validation: RuntimeValidationResult,
        transaction: RuntimeTransaction,
    ) -> None:
        """
        Stop execution when semantic validation fails.
        """

        if validation.valid:
            return

        self.rollback(transaction)

        self._runtime.events.publish(
            ValidationFailed(
                transaction_id=transaction.transaction_id,
                session_id=transaction.session.session_id,
                message="Validation failed",
            )
        )

        raise RuntimeError(
            "Runtime commit aborted because semantic validation failed."
        )

    def _create_version(
        self,
        transaction: RuntimeTransaction,
        initial_state: Any,
    ) -> RuntimeVersion:
        """Create a Runtime version, passing the pre-transaction state for delta computation."""

        version = self._runtime.versions.create(
            transaction.session,
            core_bridge=self._runtime.core_bridge,
            previous_state=initial_state,
        )

        self._runtime.events.publish(
            VersionCreated(
                version_id=version.version_id,
                session_id=transaction.session.session_id,
                transaction_id=transaction.transaction_id,
            )
        )

        return version

    def _persist(
        self,
        version: RuntimeVersion,
        transaction: RuntimeTransaction,
    ) -> None:
        """
        Persist Runtime state.
        """

        self._runtime.storage.save_version(version)

        self._runtime.storage.save_session(
            transaction.session,
        )

    def _finalize(
        self,
        transaction: RuntimeTransaction,
    ) -> None:
        """
        Commit the Runtime transaction.
        """

        self._runtime.transactions.commit(
            transaction,
        )
    

    def _execute_operations(self, transaction) -> None:
        executor = self._runtime.executor
        dispatcher = getattr(self._runtime, 'dispatcher', None)
        session = transaction.session

        # 1. Готовые операции (старый путь)
        for op in transaction.operations:
            result = executor.execute(op, session)
            self._handle_result(result, op.operation_id, transaction)
            self._apply_state_mutation(op, result, session)
            transaction.add_result(result)

        # 2. DispatchRequest (новый путь)
        if dispatcher is not None and transaction.requests:
            for req in transaction.requests:
                context = ExecutionContext(session=session, executor=executor)
                result = dispatcher.dispatch(req, context)
                self._handle_result(result, req.operation_id, transaction)

    def _handle_result(self, result, operation_id, transaction):
        if result.status == OperationStatus.FAILED:
            self.rollback(transaction)
            raise RuntimeError(f"Operation {operation_id} failed: {result.error}")
        if result.diagnostics:
            self._runtime.diagnostics.extend(result.diagnostics)
            for diagnostic in result.diagnostics:
                transaction.session.add_diagnostic(diagnostic)

    def _apply_state_mutation(self, operation, result, session) -> None:
        """
        Write operation results that change the canonical Knowledge
        Structure back onto the owning session.
        """
        if result.status != OperationStatus.COMPLETED:
            return

        if isinstance(operation, EvolveOperation):
            session.knowledge_structure = result.payload

        elif isinstance(operation, RevertVersionOperation):
            session.knowledge_structure = result.payload