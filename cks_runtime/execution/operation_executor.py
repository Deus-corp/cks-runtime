"""
Runtime Operation Executor.

Owns Runtime operation execution.

The Executor executes Runtime Operations while remaining
independent from semantic behaviour.

Semantic behaviour belongs exclusively to CKS Core.
"""

from __future__ import annotations

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
    """

    def __init__(
        self,
        *,
        core_adapter: CoreBridge,
    ) -> None:

        self._core_adapter = core_adapter

    @property
    def core(
        self,
    ) -> CoreBridge:
        """
        Runtime Core Bridge.
        """

        return self._core_adapter

    def execute(
        self,
        operation: Operation,
        session: RuntimeSession,
    ) -> ExecutionResult:
        """
        Execute one Runtime Operation.
        """

        try:

            result = operation.execute(
                session,
                self,
            )

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

        for diagnostic in result.diagnostics:

            session.diagnostics.append(
                diagnostic,
            )

        return result