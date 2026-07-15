from cks_runtime.session.session_manager import SessionManager
from cks_runtime.transaction.transaction import (
    RuntimeTransaction,
    TransactionStatus,
)


def create_transaction():

    sessions = SessionManager()

    session = sessions.create({})

    return RuntimeTransaction(session=session)


def test_transaction_initial_state():

    tx = create_transaction()

    assert tx.status == TransactionStatus.CREATED
    assert tx.operations == []
    assert tx.diagnostics == []
    assert tx.completed is False


def test_add_operation():

    tx = create_transaction()

    operation = {"action": "load"}

    tx.add_operation(operation)

    assert tx.operations == [operation]


def test_add_diagnostic():

    tx = create_transaction()

    diagnostic = {"message": "warning"}

    tx.add_diagnostic(diagnostic)

    assert tx.diagnostics == [diagnostic]


def test_mark_executing():

    tx = create_transaction()

    tx.mark_executing()

    assert tx.status == TransactionStatus.EXECUTING


def test_mark_validating():

    tx = create_transaction()

    tx.mark_validating()

    assert tx.status == TransactionStatus.VALIDATING


def test_commit():

    tx = create_transaction()

    tx.commit()

    assert tx.status == TransactionStatus.COMMITTED
    assert tx.completed is True


def test_rollback():

    tx = create_transaction()

    tx.rollback()

    assert tx.status == TransactionStatus.ROLLED_BACK
    assert tx.completed is True


def test_abort():

    tx = create_transaction()

    tx.abort()

    assert tx.status == TransactionStatus.ABORTED
    assert tx.completed is True