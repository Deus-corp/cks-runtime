"""
Runtime Execution Context.

SPEC-004 Runtime Execution.

ExecutionContext binds together the Runtime state required
to execute operations.

It contains no execution logic and is immutable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cks_runtime.execution.operation_executor import OperationExecutor
from cks_runtime.session.session import RuntimeSession
from cks_runtime.transaction.transaction import RuntimeTransaction


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """
    Immutable Runtime execution context.

    The execution context represents one execution scope.

    It bundles together:

    - active Runtime Session;
    - current Runtime Transaction (optional);
    - OperationExecutor;
    - arbitrary execution metadata.

    The context owns no business logic.
    """

    session: RuntimeSession

    executor: OperationExecutor

    transaction: RuntimeTransaction | None = None

    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def has_transaction(self) -> bool:
        """
        Whether execution currently occurs inside a transaction.
        """
        return self.transaction is not None

    @property
    def transaction_id(self) -> str | None:
        """
        Identifier of the active transaction.
        """
        if self.transaction is None:
            return None

        return self.transaction.transaction_id