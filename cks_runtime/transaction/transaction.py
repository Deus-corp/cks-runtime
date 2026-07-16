"""
Runtime Transaction model.

A Transaction is the exclusive mechanism through which
Runtime Session operational state may evolve.

Transactions coordinate execution.
They do not define semantic behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class TransactionStatus(Enum):
    """
    Canonical Runtime Transaction states.
    """

    CREATED = "created"
    EXECUTING = "executing"
    VALIDATING = "validating"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    ABORTED = "aborted"


@dataclass(slots=True)
class RuntimeTransaction:
    """
    Represents one Runtime Transaction.
    """

    session: Any

    transaction_id: str = field(
        default_factory=lambda: str(uuid4())
    )

    operations: list[Any] = field(
        default_factory=list
    )

    diagnostics: list[Any] = field(
        default_factory=list
    )

    status: TransactionStatus = (
        TransactionStatus.CREATED
    )

    def _ensure_not_completed(self) -> bool:
        """
        Returns False once a terminal state
        has been reached.
        """

        return not self.completed

    def add_operation(
        self,
        operation: Any,
    ) -> None:

        self.operations.append(operation)

    def add_diagnostic(
        self,
        diagnostic: Any,
    ) -> None:

        self.diagnostics.append(diagnostic)

    def mark_executing(self) -> None:

        if self._ensure_not_completed():
            self.status = (
                TransactionStatus.EXECUTING
            )

    def mark_validating(self) -> None:

        if self._ensure_not_completed():
            self.status = (
                TransactionStatus.VALIDATING
            )

    def commit(self) -> None:

        if self._ensure_not_completed():
            self.status = (
                TransactionStatus.COMMITTED
            )

    def rollback(self) -> None:

        if self._ensure_not_completed():
            self.status = (
                TransactionStatus.ROLLED_BACK
            )

    def abort(self) -> None:

        if self._ensure_not_completed():
            self.status = (
                TransactionStatus.ABORTED
            )

    @property
    def completed(self) -> bool:

        return self.status in {
            TransactionStatus.COMMITTED,
            TransactionStatus.ROLLED_BACK,
            TransactionStatus.ABORTED,
        }