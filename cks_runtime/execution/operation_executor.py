"""
Runtime Operation Executor.

Owns Runtime operation execution.

The Executor executes Runtime Operations while remaining
independent from semantic behaviour.

Semantic behaviour belongs exclusively to CKS Core.
"""

from __future__ import annotations

import time
from abc import ABC
from abc import abstractmethod

from dataclasses import dataclass

from enum import Enum
from typing import Any

from cks_runtime.core_api.bridge import CoreBridge

from cks_runtime.diagnostics.diagnostic import (
    Diagnostic,
    DiagnosticSeverity,
    DiagnosticSource,
)

from cks_runtime.session.session import RuntimeSession


# ---------------------------------------------------------------------
# Operation lifecycle
# ---------------------------------------------------------------------


class OperationStatus(Enum):
    """
    Runtime Operation status.
    """

    PENDING = "pending"

    RUNNING = "running"

    COMPLETED = "completed"

    FAILED = "failed"


# ---------------------------------------------------------------------
# Execution Result
# ---------------------------------------------------------------------


@dataclass(slots=True)
class ExecutionResult:
    """
    Result produced by one Runtime Operation.
    """

    operation_id: str

    status: OperationStatus

    payload: Any | None = None

    diagnostics: tuple[
        Diagnostic,
        ...
    ] = ()

    error: Exception | None = None

    @property
    def succeeded(
        self,
    ) -> bool:

        return (
            self.status
            is OperationStatus.COMPLETED
        )

    @property
    def failed(
        self,
    ) -> bool:

        return (
            self.status
            is OperationStatus.FAILED
        )


# ---------------------------------------------------------------------
# Operation
# ---------------------------------------------------------------------


class Operation(ABC):
    """
    Runtime executable Operation.
    """

    def __init__(
        self,
        operation_id: str,
        *,
        metadata: dict[
            str,
            Any,
        ]
        | None = None,
    ) -> None:

        self.operation_id = operation_id

        self.metadata = (
            metadata
            if metadata is not None
            else {}
        )

    @abstractmethod
    def execute(
        self,
        session: RuntimeSession,
        executor: "OperationExecutor",
    ) -> ExecutionResult:
        """
        Execute the Runtime Operation.
        """


# ---------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------


class OperationExecutor:
    """
    Executes Runtime Operations.

    The Executor owns execution only.

    Transactions,
    Sessions,
    Diagnostics,
    Persistence,
    Versions
    remain owned elsewhere.

    ``storage`` is the one exception, and deliberately narrow: it is
    exposed read-only, solely so ``MergeOperation`` can look up the
    operation log (ADR-007) for field-level conflict resolution when
    run directly via ``executor.execute()`` -- e.g. cks-mcp's
    ``merge_branch`` probes a merge this way, outside any transaction,
    specifically to get a conflict result without touching persisted
    state (see ``MergeOperation``'s docstring). The Executor still
    never writes through it; ``ExecutionPipeline`` continues to own
    every ``save_*``/``record_operations`` call.
    """

    def __init__(
        self,
        *,
        core_adapter: CoreBridge,
        metrics: Any = None,
        storage: Any = None,
    ) -> None:

        self._core_adapter = core_adapter
        self._metrics = metrics
        self._storage = storage

    @property
    def core(
        self,
    ) -> CoreBridge:
        """
        Runtime Core Bridge.
        """

        return self._core_adapter

    @property
    def storage(
        self,
    ) -> Any:
        """
        Runtime Storage, read-only (see class docstring). ``None`` if
        this Executor wasn't constructed with one.
        """

        return self._storage

    def execute(
        self,
        operation: Operation,
        session: RuntimeSession,
    ) -> ExecutionResult:
        """Execute one Runtime Operation."""
        start = time.monotonic()
        try:
            result = operation.execute(session, self)
        except Exception as exc:
            result = ExecutionResult(
                operation_id=operation.operation_id,
                status=OperationStatus.FAILED,
                error=exc,
                diagnostics=(
                    Diagnostic(
                        message=str(exc),
                        source=DiagnosticSource.RUNTIME,
                        severity=DiagnosticSeverity.ERROR,
                        metadata={
                            "operation_id": operation.operation_id,
                        },
                    ),
                ),
            )
        duration_ms = (time.monotonic() - start) * 1000
        if self._metrics is not None:
            self._metrics.record(operation.operation_id, duration_ms)
        return result