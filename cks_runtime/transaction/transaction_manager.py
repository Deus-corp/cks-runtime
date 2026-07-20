"""
Runtime Transaction Manager.

Owns RuntimeTransaction lifecycle.

Responsibilities:

- begin RuntimeTransactions;
- retrieve RuntimeTransactions;
- finalize RuntimeTransactions;
- enumerate active RuntimeTransactions.

Does not own:

- semantic validation;
- persistence;
- version creation.
"""

from __future__ import annotations

from typing import Any

from .transaction import RuntimeTransaction


class TransactionManager:
    """
    Owns RuntimeTransaction lifecycle.
    """

    def __init__(self) -> None:
        self._transactions: dict[str, RuntimeTransaction] = {}

    #
    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------
    #

    def begin(
        self,
        session: Any,
    ) -> RuntimeTransaction:
        """
        Begin a RuntimeTransaction.

        Exactly one active transaction may exist
        per RuntimeSession.
        """

        if session.has_active_transaction:
            raise RuntimeError(
                "Session already has an active transaction."
            )

        transaction = RuntimeTransaction(
            session=session,
        )

        self._transactions[
            transaction.transaction_id
        ] = transaction

        session.attach_transaction(
            transaction,
        )

        return transaction

    #
    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------
    #

    def retrieve(
        self,
        transaction_id: str,
    ) -> RuntimeTransaction | None:
        """
        Retrieve a RuntimeTransaction.
        """

        return self._transactions.get(
            transaction_id,
        )

    def has_transaction(
        self,
        transaction_id: str,
    ) -> bool:
        """
        Whether a RuntimeTransaction exists.
        """

        return transaction_id in self._transactions

    @property
    def transaction_count(self) -> int:
        """
        Number of tracked RuntimeTransactions.
        """

        return len(self._transactions)

    def list_transactions(
        self,
    ) -> tuple[RuntimeTransaction, ...]:
        """
        Return tracked RuntimeTransactions.

        An immutable tuple is returned to prevent
        accidental external mutation.
        """

        return tuple(
            self._transactions.values(),
        )

    #
    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    #

    def commit(
        self,
        transaction: RuntimeTransaction,
    ) -> None:
        """
        Commit a RuntimeTransaction.
        """

        transaction.commit()

        self._finish(
            transaction,
        )

    def rollback(
        self,
        transaction: RuntimeTransaction,
    ) -> None:
        """
        Roll back a RuntimeTransaction.
        """

        transaction.rollback()

        self._finish(
            transaction,
        )

    def abort(
        self,
        transaction: RuntimeTransaction,
    ) -> None:
        """
        Abort a RuntimeTransaction.
        """

        transaction.abort()

        self._finish(
            transaction,
        )

    #
    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    #

    def _finish(
        self,
        transaction: RuntimeTransaction,
    ) -> None:
        """Finish RuntimeTransaction lifecycle."""
        transaction.session.detach_transaction()
        self._transactions.pop(transaction.transaction_id, None)

    def clear(self) -> None:
        """
        Remove all tracked RuntimeTransactions.

        Primarily intended for testing.
        """

        for transaction in self._transactions.values():
            transaction.session.detach_transaction()

        self._transactions.clear()
    
    def get(self, transaction_id: str) -> RuntimeTransaction:
        """Return the transaction with the given id.
        
        Raises KeyError if not found.
        """
        transaction = self._transactions.get(transaction_id)
        if transaction is None:
            raise KeyError(f"Transaction '{transaction_id}' not found.")
        return transaction