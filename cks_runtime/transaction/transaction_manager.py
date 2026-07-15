"""
Runtime Transaction Manager.

Owns Transaction lifecycle.

Responsibilities:

- begin Transactions;
- retrieve Transactions;
- coordinate completion;
- preserve Session ownership.

Does not:

- perform semantic validation;
- create Versions;
- persist Runtime state;
- modify CKS Core behaviour.
"""

from typing import Any

from .transaction import RuntimeTransaction


class TransactionManager:
    """
    Coordinates Runtime Transactions.
    """

    def __init__(self) -> None:
        self._transactions: dict[
            str,
            RuntimeTransaction
        ] = {}

    def begin(
        self,
        session: Any,
    ) -> RuntimeTransaction:
        """
        Begin a Transaction for a Session.

        Exactly one active Transaction may exist
        per Session.
        """

        if session.active_transaction is not None:
            raise RuntimeError(
                "Session already has an active transaction."
            )

        transaction = RuntimeTransaction(
            session=session
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
        """
        Retrieve a Transaction by identity.
        """

        return self._transactions.get(
            transaction_id
        )

    def commit(
        self,
        transaction: RuntimeTransaction,
    ) -> None:
        """
        Commit a Transaction.
        """

        transaction.commit()

        transaction.session.active_transaction = None

    def rollback(
        self,
        transaction: RuntimeTransaction,
    ) -> None:
        """
        Roll back a Transaction.
        """

        transaction.rollback()

        transaction.session.active_transaction = None

    def abort(
        self,
        transaction: RuntimeTransaction,
    ) -> None:
        """
        Abort a Transaction.
        """

        transaction.abort()

        transaction.session.active_transaction = None

    def list_transactions(
        self,
    ) -> list[RuntimeTransaction]:
        """
        Return known Transactions.

        Primarily intended for Runtime
        management and testing.
        """

        return list(
            self._transactions.values()
        )