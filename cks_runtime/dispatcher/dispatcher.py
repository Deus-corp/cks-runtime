"""
Runtime Operation Dispatcher.

Dispatcher resolves Runtime Operations from the
Operation Registry and delegates execution to the
Operation Executor.

Dispatcher owns routing.

Executor owns execution.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

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

    async def dispatch(
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
        except TypeError as exc:
            # request.parameters comes from outside the process (an
            # API/MCP request body, typically) -- a missing, extra, or
            # misspelled key raises TypeError from the Operation's
            # __init__. Without this, the exception propagates straight
            # out of dispatch(), which means _handle_result never runs
            # and the transaction is never rolled back -- unlike every
            # other failure mode in this method, which reaches
            # _handle_result via a FAILED ExecutionResult and rolls back
            # cleanly.
            diagnostic = Diagnostic(
                message=(
                    f"Operation '{request.operation_id}' could not be "
                    f"constructed from the given parameters: {exc}"
                ),
                source=DiagnosticSource.RUNTIME,
                severity=DiagnosticSeverity.ERROR,
                metadata={
                    "operation_id": request.operation_id,
                    "parameters": dict(request.parameters),
                },
            )

            return ExecutionResult(
                operation_id=request.operation_id,
                status=OperationStatus.FAILED,
                diagnostics=(diagnostic,),
                error=exc,
            )

        #
        # Delegate execution.
        #

        result = await self.executor.execute(
            operation,
            context.session,
        )

        #
        # Preserve diagnostics inside execution context.
        #

        return result