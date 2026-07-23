"""
Runtime Operation Dispatcher.

Dispatcher resolves Runtime Operations from the
Operation Registry and delegates execution to the
Operation Executor.

Dispatcher owns routing.

Executor owns execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from cks_runtime.diagnostics.diagnostic import (
    Diagnostic,
    DiagnosticSeverity,
    DiagnosticSource,
)
from cks_runtime.execution.execution_context import (
    ExecutionContext,
)
from cks_runtime.execution.operation_executor import (
    ExecutionResult,
    OperationExecutor,
    OperationStatus,
)
from cks_runtime.operations.operation_registry import (
    OperationRegistry,
)


@dataclass(frozen=True, slots=True)
class DispatchRequest:
    """
    Immutable Runtime dispatch request.
    """

    operation_id: str

    parameters: Mapping[str, Any] = field(default_factory=dict)

    metadata: Mapping[str, Any] = field(default_factory=dict)


class Dispatcher:
    """
    Runtime Operation Dispatcher.

    Responsibilities

        resolve operation

            ↓

        delegate execution

    Dispatcher never executes operations itself.
    """

    def __init__(
        self,
        registry: OperationRegistry,
        executor: OperationExecutor,
    ) -> None:
        self._registry = registry
        self._executor = executor

    @property
    def registry(self) -> OperationRegistry:
        """
        Registered Runtime Operations.
        """
        return self._registry

    @property
    def executor(self) -> OperationExecutor:
        """
        Runtime Operation Executor.
        """
        return self._executor

    def dispatch(
        self,
        request: DispatchRequest,
        context: ExecutionContext,
    ) -> ExecutionResult:
        """
        Resolve and execute a Runtime Operation.
        """

        try:
            operation = self.registry.create(
                request.operation_id,
                **request.parameters,
            )
        except KeyError:
            operation = None

        if operation is None:
            diagnostic = Diagnostic(
                message=(
                    f"Operation '{request.operation_id}' "
                    "is not registered."
                ),
                source=DiagnosticSource.RUNTIME,
                severity=DiagnosticSeverity.ERROR,
                metadata={
                    "operation_id": request.operation_id,
                },
            )

            return ExecutionResult(
                operation_id=request.operation_id,
                status=OperationStatus.FAILED,
                diagnostics=(diagnostic,),
                error=LookupError(
                    f"Unknown operation '{request.operation_id}'."
                ),
            )

        #
        # Delegate execution.
        #

        result = self.executor.execute(
            operation,
            context.session,
        )

        #
        # Preserve diagnostics inside execution context.
        #

        return result