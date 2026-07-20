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

    session = sessions.create_session(
        knowledge_structure={}
    )

    tx = manager.begin(session)

    assert session.active_transaction is tx

    assert manager.retrieve(
        tx.transaction_id
    ) is tx


def test_commit_transaction():

    sessions = SessionManager()
    manager = TransactionManager()

    session = sessions.create_session(
        knowledge_structure={}
    )

    tx = manager.begin(session)

    tx.add_operation(
        {"action": "example"}
    )

    manager.commit(tx)

    assert tx.status == TransactionStatus.COMMITTED
    assert tx.completed is True

    assert session.active_transaction is None


def test_rollback_transaction():

    sessions = SessionManager()
    manager = TransactionManager()

    session = sessions.create_session(
        knowledge_structure={}
    )

    tx = manager.begin(session)

    manager.rollback(tx)

    assert tx.status == TransactionStatus.ROLLED_BACK
    assert tx.completed is True

    assert session.active_transaction is None


def test_abort_transaction():

    sessions = SessionManager()
    manager = TransactionManager()

    session = sessions.create_session(
        knowledge_structure={}
    )

    tx = manager.begin(session)

    manager.abort(tx)

    assert tx.status == TransactionStatus.ABORTED
    assert tx.completed is True

    assert session.active_transaction is None


def test_cannot_begin_second_transaction():

    sessions = SessionManager()
    manager = TransactionManager()

    session = sessions.create_session(
        knowledge_structure={}
    )

    manager.begin(session)

    with pytest.raises(RuntimeError):
        manager.begin(session)


def test_list_transactions():

    sessions = SessionManager()
    manager = TransactionManager()

    session = sessions.create_session(
        knowledge_structure={}
    )

    tx = manager.begin(session)

    transactions = manager.list_transactions()

    assert tx in transactions
    assert len(transactions) == 1


def test_list_transactions_returns_copy():
    sessions = SessionManager()
    manager = TransactionManager()
    session = sessions.create_session(knowledge_structure={})
    manager.begin(session)
    first = manager.list_transactions()
    second = manager.list_transactions()
    assert first == second
    assert first is not second


def test_completed_transaction_is_not_retrievable():
    sessions = SessionManager()
    manager = TransactionManager()
    session = sessions.create_session(knowledge_structure={})
    tx = manager.begin(session)
    manager.commit(tx)
    # После завершения транзакция удаляется из реестра
    assert manager.retrieve(tx.transaction_id) is None