"""
Runtime Transaction.

A RuntimeTransaction is the exclusive mechanism through which
RuntimeSession operational state may evolve.

Transactions coordinate execution.

They never define semantic behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from typing import Any
from uuid import uuid4


class TransactionStatus(Enum):
    """
    Canonical RuntimeTransaction states.
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
    Represents one RuntimeTransaction.

    A transaction owns only operational execution state.

    Semantic behaviour always belongs to CKS Core.
    """

    session: Any

    transaction_id: str = field(
        default_factory=lambda: str(uuid4()),
    )

    operations: list[Any] = field(
        default_factory=list,
    )

    diagnostics: list[Any] = field(
        default_factory=list,
    )

    status: TransactionStatus = (
        TransactionStatus.CREATED
    )

    #
    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------
    #

    @property
    def completed(self) -> bool:
        """
        Whether this transaction has reached
        a terminal state.
        """

        return self.status in {
            TransactionStatus.COMMITTED,
            TransactionStatus.ROLLED_BACK,
            TransactionStatus.ABORTED,
        }

    @property
    def operation_count(self) -> int:
        """
        Number of attached operations.
        """

        return len(self.operations)

    @property
    def has_diagnostics(self) -> bool:
        """
        Whether Runtime diagnostics were collected.
        """

        return bool(self.diagnostics)

    #
    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    #

    def _ensure_not_completed(self) -> bool:
        """
        Guard against state changes after completion.
        """

        return not self.completed

    #
    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------
    #

    def add_operation(
        self,
        operation: Any,
    ) -> None:
        """
        Attach an operation.
        """

        self.operations.append(operation)

    #
    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------
    #

    def add_diagnostic(
        self,
        diagnostic: Any,
    ) -> None:
        """
        Attach a Runtime diagnostic.
        """

        self.diagnostics.append(diagnostic)

    #
    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    #

    def mark_executing(self) -> None:
        """
        Transition into EXECUTING.
        """

        if not self._ensure_not_completed():
            return

        self.status = TransactionStatus.EXECUTING

    def mark_validating(self) -> None:
        """
        Transition into VALIDATING.
        """

        if not self._ensure_not_completed():
            return

        self.status = TransactionStatus.VALIDATING

    def commit(self) -> None:
        """
        Mark transaction as committed.
        """

        if not self._ensure_not_completed():
            return

        self.status = TransactionStatus.COMMITTED

    def rollback(self) -> None:
        """
        Mark transaction as rolled back.
        """

        if not self._ensure_not_completed():
            return

        self.status = TransactionStatus.ROLLED_BACK

    def abort(self) -> None:
        """
        Abort transaction execution.
        """

        if not self._ensure_not_completed():
            return

        self.status = TransactionStatus.ABORTED

    #
    # ------------------------------------------------------------------
    # Debugging
    # ------------------------------------------------------------------
    #

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"id={self.transaction_id!r}, "
            f"status={self.status.value!r})"
        )