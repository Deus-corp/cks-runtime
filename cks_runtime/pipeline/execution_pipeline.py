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
from cks_runtime.operations.operation_types import ValidateOperation
from cks_runtime.execution.execution_context import ExecutionContext


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
        if transaction.operations:
            self._execute_operations(transaction)
        else:
            # обратная совместимость: старый путь валидации
            validation = self._validate(transaction)
            self._collect_diagnostics(validation)
            self._quality_gate(validation, transaction)

        version = self._create_version(transaction)
        self._persist(version, transaction)
        self._finalize(transaction)
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

        raise RuntimeError(
            "Runtime commit aborted because semantic validation failed."
        )

    def _create_version(
        self,
        transaction: RuntimeTransaction,
    ) -> RuntimeVersion:
        """
        Create a Runtime version.
        """

        return self._runtime.versions.create(
            transaction.session,
        )

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