"""
Runtime Transaction model.

A Transaction is the exclusive mechanism through which
Runtime Session operational state may evolve.

Transactions coordinate execution.
They do not define semantic behaviour.
"""

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


@dataclass
class RuntimeTransaction:
    """
    Represents one Runtime Transaction.

    Ownership:

    - exactly one Runtime Session owns a Transaction;
    - Transaction modifies operational state only;
    - semantic validation belongs to CKS Core.
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
        Return False when the Transaction has
        already reached a terminal state.
        """

        return not self.completed

    def add_operation(
        self,
        operation: Any,
    ) -> None:
        """
        Register a Runtime operation.
        """

        self.operations.append(operation)

    def add_diagnostic(
        self,
        diagnostic: Any,
    ) -> None:
        """
        Register a Runtime or Core diagnostic.
        """

        self.diagnostics.append(diagnostic)

    def mark_executing(self) -> None:
        """
        Mark Transaction as executing.
        """

        if not self._ensure_not_completed():
            return

        self.status = TransactionStatus.EXECUTING

    def mark_validating(self) -> None:
        """
        Mark Transaction as validating.
        """

        if not self._ensure_not_completed():
            return

        self.status = TransactionStatus.VALIDATING

    def commit(self) -> None:
        """
        Complete Transaction successfully.
        """

        if not self._ensure_not_completed():
            return

        self.status = TransactionStatus.COMMITTED

    def rollback(self) -> None:
        """
        Roll back pending operational changes.
        """

        if not self._ensure_not_completed():
            return

        self.status = TransactionStatus.ROLLED_BACK

    def abort(self) -> None:
        """
        Abort Transaction execution.
        """

        if not self._ensure_not_completed():
            return

        self.status = TransactionStatus.ABORTED

    @property
    def completed(self) -> bool:
        """
        Whether the Transaction has reached
        a terminal state.
        """

        return self.status in {
            TransactionStatus.COMMITTED,
            TransactionStatus.ROLLED_BACK,
            TransactionStatus.ABORTED,
        }