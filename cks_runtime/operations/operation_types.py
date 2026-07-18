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
            status=OperationStatus.COMPLETED,
            payload=result,
            diagnostics=result.diagnostics,
            error=None,
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