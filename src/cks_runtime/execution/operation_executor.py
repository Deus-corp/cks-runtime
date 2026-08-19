"""
Runtime Operation Executor.

Owns Runtime operation execution.

The Executor executes Runtime Operations while remaining
independent from semantic behaviour.

Semantic behaviour belongs exclusively to CKS Core.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
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

    # The Operation instance that produced this result. Set centrally
    # by OperationExecutor.execute() below (regardless of whether it
    # was called directly from the transaction.operations loop or via
    # Dispatcher.dispatch()'s DispatchRequest path), so
    # ExecutionPipeline._apply_state_mutation can do its
    # isinstance(operation, EvolveOperation/...) check from the result
    # alone -- the dispatch path never had the constructed Operation
    # in scope otherwise, which is why it used to skip state mutation
    # entirely.
    operation: Any | None = None

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
    async def execute(
        self,
        session: RuntimeSession,
        executor: OperationExecutor,
    ) -> ExecutionResult:
        """
        Execute the Runtime Operation.

        ``async`` uniformly across every subclass, even though most
        never await anything: only ``MergeOperation``'s ADR-007
        fast-path reads the operation log from storage. Python doesn't
        allow a caller to ``await`` some subclasses' ``execute()`` and
        call others directly when dispatch is polymorphic (as it is
        in ``OperationExecutor.execute``/``Dispatcher.dispatch``), so
        the signature has to be async everywhere for the one subclass
        that needs it.
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

    async def execute(
        self,
        operation: Operation,
        session: RuntimeSession,
        *,
        record_metrics: bool = True,
    ) -> ExecutionResult:
        """
        Execute one Runtime Operation.

        ``record_metrics`` defaults to True for every ordinary
        execution (including the one ``ExecutionPipeline`` performs on
        commit). Callers that execute an operation purely as a
        pre-commit probe -- to detect a conflict or validate
        provenance before deciding whether to commit at all, e.g.
        cks-mcp's ``evolve_knowledge``/``merge_branch`` -- should pass
        ``record_metrics=False`` for that probe call. Otherwise every
        successful evolve/merge is executed (and thus metered) twice:
        once for the probe, once again when ``ExecutionPipeline``
        replays the same operation during commit, silently doubling
        ``get_metrics``' counts and average-time figures relative to
        the number of MCP tool calls actually made.
        """
        start = time.monotonic()
        try:
            result = await operation.execute(session, self)
        except Exception as exc:  # noqa: BLE001 -- plugin boundary; captured below, not swallowed
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
        result.operation = operation
        duration_ms = (time.monotonic() - start) * 1000
        if self._metrics is not None and record_metrics:
            self._metrics.record(operation.operation_id, duration_ms)
        return result