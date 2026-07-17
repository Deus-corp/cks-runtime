"""
Runtime Transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class TransactionStatus(Enum):
    CREATED = "created"
    EXECUTING = "executing"
    VALIDATING = "validating"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    ABORTED = "aborted"


@dataclass(slots=True)
class RuntimeTransaction:
    session: Any
    transaction_id: str = field(default_factory=lambda: str(uuid4()))
    operations: list[Any] = field(default_factory=list)
    requests: list[Any] = field(default_factory=list)   # <-- список, не tuple
    diagnostics: list[Any] = field(default_factory=list)
    status: TransactionStatus = TransactionStatus.CREATED

    @property
    def completed(self) -> bool:
        return self.status in {
            TransactionStatus.COMMITTED,
            TransactionStatus.ROLLED_BACK,
            TransactionStatus.ABORTED,
        }

    @property
    def operation_count(self) -> int:
        return len(self.operations) + len(self.requests)

    @property
    def has_diagnostics(self) -> bool:
        return bool(self.diagnostics)

    def _ensure_not_completed(self) -> bool:
        return not self.completed

    def add_operation(self, operation: Any) -> None:
        self.operations.append(operation)

    def add_request(self, request: Any) -> None:
        self.requests.append(request)

    def add_diagnostic(self, diagnostic: Any) -> None:
        self.diagnostics.append(diagnostic)

    def mark_executing(self) -> None:
        if not self._ensure_not_completed():
            return
        self.status = TransactionStatus.EXECUTING

    def mark_validating(self) -> None:
        if not self._ensure_not_completed():
            return
        self.status = TransactionStatus.VALIDATING

    def commit(self) -> None:
        if not self._ensure_not_completed():
            return
        self.status = TransactionStatus.COMMITTED

    def rollback(self) -> None:
        if not self._ensure_not_completed():
            return
        self.status = TransactionStatus.ROLLED_BACK

    def abort(self) -> None:
        if not self._ensure_not_completed():
            return
        self.status = TransactionStatus.ABORTED

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"id={self.transaction_id!r}, "
            f"status={self.status.value!r})"
        )