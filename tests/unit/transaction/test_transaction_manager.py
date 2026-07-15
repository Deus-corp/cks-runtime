import pytest

from cks_runtime.session.session_manager import SessionManager
from cks_runtime.transaction.transaction import (
    TransactionStatus,
)
from cks_runtime.transaction.transaction_manager import (
    TransactionManager,
)


def test_begin_transaction():

    sessions = SessionManager()
    manager = TransactionManager()

    session = sessions.create({})

    tx = manager.begin(session)

    assert session.active_transaction is tx
    assert manager.retrieve(
        tx.transaction_id
    ) is tx


def test_commit_transaction():

    sessions = SessionManager()
    manager = TransactionManager()

    session = sessions.create({})

    tx = manager.begin(session)

    tx.add_operation(
        {"action": "example"}
    )

    manager.commit(tx)

    assert tx.status == TransactionStatus.COMMITTED
    assert session.active_transaction is None


def test_rollback_transaction():

    sessions = SessionManager()
    manager = TransactionManager()

    session = sessions.create({})

    tx = manager.begin(session)

    manager.rollback(tx)

    assert tx.status == TransactionStatus.ROLLED_BACK
    assert session.active_transaction is None


def test_abort_transaction():

    sessions = SessionManager()
    manager = TransactionManager()

    session = sessions.create({})

    tx = manager.begin(session)

    manager.abort(tx)

    assert tx.status == TransactionStatus.ABORTED
    assert session.active_transaction is None


def test_cannot_begin_second_transaction():

    sessions = SessionManager()
    manager = TransactionManager()

    session = sessions.create({})

    manager.begin(session)

    with pytest.raises(RuntimeError):
        manager.begin(session)


def test_list_transactions():

    sessions = SessionManager()
    manager = TransactionManager()

    session = sessions.create({})

    tx = manager.begin(session)

    transactions = manager.list_transactions()

    assert tx in transactions
    assert len(transactions) == 1