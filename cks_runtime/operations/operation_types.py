"""
Canonical Runtime Operations.
"""

from __future__ import annotations
from typing import Any
from cks_runtime.execution.operation_executor import (
    Operation,
    ExecutionResult,
    OperationStatus,
)
from cks_runtime.session.session import RuntimeSession


class ValidateOperation(Operation):
    """Validate a Knowledge Structure."""
    operation_id: str = "validate"

    def __init__(
        self,
        operation_id: str = "validate",
        *,
        knowledge_structure: Any = None,
        extra_constraints: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(operation_id, metadata=metadata)
        self.knowledge_structure = knowledge_structure
        self.extra_constraints = extra_constraints

    def execute(
        self,
        session: RuntimeSession,
        executor,
    ) -> ExecutionResult:
        result = executor.core.validate(
            self.knowledge_structure,
            extra_constraints=self.extra_constraints,
        )
        return ExecutionResult(
            operation_id=self.operation_id,
            status=OperationStatus.COMPLETED if result.valid else OperationStatus.FAILED,
            payload=result,
            diagnostics=result.diagnostics,
            error=None if result.valid else RuntimeError("Validation failed"),
        )


class EvolveOperation(Operation):
    """Apply a semantic evolution."""
    operation_id: str = "evolve"

    def __init__(
        self,
        operation_id: str = "evolve",
        *,
        knowledge_structure: Any = None,
        evolution: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(operation_id, metadata=metadata)
        self.knowledge_structure = knowledge_structure
        self.evolution = evolution

    def execute(
        self,
        session: RuntimeSession,
        executor,
    ) -> ExecutionResult:
        evolved = executor.core.evolve(self.knowledge_structure, self.evolution)
        return ExecutionResult(
            operation_id=self.operation_id,
            status=OperationStatus.COMPLETED,
            payload=evolved,
        )


class SerializeOperation(Operation):
    """Serialize a Knowledge Structure."""
    operation_id: str = "serialize"

    def __init__(
        self,
        operation_id: str = "serialize",
        *,
        knowledge_structure: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(operation_id, metadata=metadata)
        self.knowledge_structure = knowledge_structure

    def execute(
        self,
        session: RuntimeSession,
        executor,
    ) -> ExecutionResult:
        serialized = executor.core.serialize(self.knowledge_structure)
        return ExecutionResult(
            operation_id=self.operation_id,
            status=OperationStatus.COMPLETED,
            payload=serialized,
        )


class ExplainOperation(Operation):
    """Produce a semantic explanation."""
    operation_id: str = "explain"

    def __init__(
        self,
        operation_id: str = "explain",
        *,
        knowledge_structure: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(operation_id, metadata=metadata)
        self.knowledge_structure = knowledge_structure

    def execute(
        self,
        session: RuntimeSession,
        executor,
    ) -> ExecutionResult:
        explanation = executor.core.explain(self.knowledge_structure)
        return ExecutionResult(
            operation_id=self.operation_id,
            status=OperationStatus.COMPLETED,
            payload=explanation,
        )


class ListVersionsOperation(Operation):
    """List all versions in the current session history."""
    operation_id: str = "list_versions"

    def execute(
        self,
        session: RuntimeSession,
        executor,
    ) -> ExecutionResult:
        versions_data = [
            {
                "version_id": v.version_id,
                "created_at": v.created_at.isoformat(),
                "transaction_id": v.transaction_id,
                "metadata": dict(v.metadata),
            }
            for v in session.version_history
        ]
        return ExecutionResult(
            operation_id=self.operation_id,
            status=OperationStatus.COMPLETED,
            payload=versions_data,
        )


class RevertVersionOperation(Operation):
    """Revert the Knowledge Structure to a specific previous version."""
    operation_id: str = "revert_version"

    def __init__(
        self,
        operation_id: str = "revert_version",
        *,
        target_version_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(operation_id, metadata=metadata)
        self.target_version_id = target_version_id

    def execute(
        self,
        session: RuntimeSession,
        executor,
    ) -> ExecutionResult:
        try:
            target_structure = session.get_version_state(
                self.target_version_id,
                executor.core,
            )
        except ValueError as exc:
            return ExecutionResult(
                operation_id=self.operation_id,
                status=OperationStatus.FAILED,
                error=exc,
            )

        return ExecutionResult(
            operation_id=self.operation_id,
            status=OperationStatus.COMPLETED,
            payload=target_structure,
        )


class DiffOperation(Operation):
    """Compute structural delta between current session and a target state/version."""
    operation_id: str = "diff"

    def __init__(
        self,
        operation_id: str = "diff",
        *,
        target_version_id: str | None = None,
        target_structure: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(operation_id, metadata=metadata)
        self.target_version_id = target_version_id
        self.target_structure = target_structure

    def execute(
        self,
        session: RuntimeSession,
        executor,
    ) -> ExecutionResult:
        try:
            target = session.get_version_state(
                self.target_version_id,
                executor.core,
            )
        except ValueError as exc:
            return ExecutionResult(
                operation_id=self.operation_id,
                status=OperationStatus.FAILED,
                error=exc,
            )

        try:
            diff_patch = executor.core.diff(
                source=session.knowledge_structure,
                target=target,
            )
        except Exception as e:
            return ExecutionResult(
                operation_id=self.operation_id,
                status=OperationStatus.FAILED,
                error=e,
            )

        return ExecutionResult(
            operation_id=self.operation_id,
            status=OperationStatus.COMPLETED,
            payload=diff_patch,
        )