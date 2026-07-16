"""
Runtime Transaction Manager.
"""

from __future__ import annotations

from typing import Any

from .transaction import RuntimeTransaction


class TransactionManager:
    """
    Coordinates Runtime Transactions.
    """

    def __init__(self) -> None:

        self._transactions: dict[
            str,
            RuntimeTransaction,
        ] = {}

    def begin(
        self,
        session: Any,
    ) -> RuntimeTransaction:
        """
        Begin a Transaction.

        Exactly one active Transaction
        may exist per Session.
        """

        if session.active_transaction is not None:
            raise RuntimeError(
                "Session already has an active transaction."
            )

        transaction = RuntimeTransaction(
            session=session,
        )

        self._transactions[
            transaction.transaction_id
        ] = transaction

        session.active_transaction = transaction

        return transaction

    def retrieve(
        self,
        transaction_id: str,
    ) -> RuntimeTransaction | None:

        return self._transactions.get(
            transaction_id,
        )

    def commit(
        self,
        transaction: RuntimeTransaction,
    ) -> None:
        """
        Commit Transaction.

        Session ownership is released only
        after successful completion.
        """

        transaction.commit()

        self._finish(transaction)

    def rollback(
        self,
        transaction: RuntimeTransaction,
    ) -> None:

        transaction.rollback()

        self._finish(transaction)

    def abort(
        self,
        transaction: RuntimeTransaction,
    ) -> None:

        transaction.abort()

        self._finish(transaction)

    def _finish(
        self,
        transaction: RuntimeTransaction,
    ) -> None:
        """
        Finish Transaction lifecycle.
        """

        transaction.session.active_transaction = None

    def list_transactions(
        self,
    ) -> list[RuntimeTransaction]:

        return list(
            self._transactions.values()
        )