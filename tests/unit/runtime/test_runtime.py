import pytest

from cks_runtime.versioning.version import RuntimeVersion
from cks_runtime import Runtime
from cks_runtime.core_api.interfaces import CoreInterface
from cks_runtime.core_api.validation_result import RuntimeValidationResult
from cks_runtime.operations.operation_types import (
    ValidateOperation,
    SerializeOperation,
    ExplainOperation,
)


def test_runtime_create_session():

    runtime = Runtime()

    session = runtime.create_session({})

    assert session.knowledge_structure == {}

    assert runtime.get_session(
        session.session_id,
    ) is session


def test_runtime_list_sessions():

    runtime = Runtime()

    runtime.create_session({})
    runtime.create_session({})

    assert len(
        runtime.list_sessions()
    ) == 2


def test_runtime_close_session():

    runtime = Runtime()

    session = runtime.create_session({})

    runtime.close_session(
        session.session_id,
    )

    assert runtime.get_session(
        session.session_id,
    ) is None


def test_runtime_begin_transaction():

    runtime = Runtime()

    session = runtime.create_session({})

    tx = runtime.begin_transaction(
        session,
    )

    assert session.active_transaction is tx


def test_runtime_commit_transaction():

    runtime = Runtime()

    session = runtime.create_session({})

    tx = runtime.begin_transaction(
        session,
    )

    version = runtime.commit_transaction(
        tx,
    )

    assert isinstance(
        version,
        RuntimeVersion,
    )

    assert session.active_transaction is None

    assert runtime.latest_version(
        session,
    ) is version


def test_runtime_rollback_transaction():

    runtime = Runtime()

    session = runtime.create_session({})

    tx = runtime.begin_transaction(
        session,
    )

    runtime.rollback_transaction(tx)

    assert session.active_transaction is None


def test_runtime_abort_transaction():

    runtime = Runtime()

    session = runtime.create_session({})

    tx = runtime.begin_transaction(
        session,
    )

    runtime.abort_transaction(tx)

    assert session.active_transaction is None


def test_runtime_has_execution_pipeline():

    runtime = Runtime()

    assert runtime.pipeline is not None


def test_runtime_commit_delegates_to_pipeline(monkeypatch):

    runtime = Runtime()

    session = runtime.create_session({})

    tx = runtime.begin_transaction(session)

    called = False

    def fake_commit(transaction):
        nonlocal called

        called = True

        return RuntimeVersion(
            session_id=session.session_id,
            transaction_id="pipeline",
            knowledge_structure={},
            metadata={},
        )

    monkeypatch.setattr(
        runtime.pipeline,
        "commit",
        fake_commit,
    )

    runtime.commit_transaction(tx)

    assert called is True


# Фиктивный Core для тестов
class FakeCore(CoreInterface):
    def __init__(self, valid=True):
        self._valid = valid

    def validate(self, knowledge_structure):
        return RuntimeValidationResult(
            valid=self._valid,
            diagnostics=("test_diag",) if not self._valid else (),
            metadata={"source": "fake"},
        )

    def evolve(self, knowledge_structure, operation):
        return {"evolved": True}

    def serialize(self, knowledge_structure):
        return '{"serialized": true}'

    def explain(self, knowledge_structure):
        return {"summary": "fake explanation"}


# Новые тесты
def test_commit_with_validate_operation_success():
    """Успешная валидация через ValidateOperation должна создавать версию."""
    core = FakeCore(valid=True)
    runtime = Runtime(core=core)
    session = runtime.create_session({"test": True})
    tx = runtime.begin_transaction(session)
    tx.add_operation(ValidateOperation("op1", knowledge_structure=session.knowledge_structure))

    version = runtime.commit_transaction(tx)
    assert isinstance(version, RuntimeVersion)
    assert session.active_transaction is None
    assert runtime.latest_version(session) is version


def test_commit_with_validate_operation_failure():
    """
    Невалидная структура — это не сбой операции, а её результат:
    коммит должен пройти успешно, диагностики должны быть собраны,
    а не потеряны в необработанном исключении.
    """
    core = FakeCore(valid=False)
    runtime = Runtime(core=core)
    session = runtime.create_session({"invalid": True})
    tx = runtime.begin_transaction(session)
    tx.add_operation(ValidateOperation("op1", knowledge_structure=session.knowledge_structure))

    version = runtime.commit_transaction(tx)

    assert isinstance(version, RuntimeVersion)
    assert session.active_transaction is None
    assert runtime.latest_version(session) is version
    assert len(session.diagnostics) > 0


def test_commit_with_serialize_operation():
    """Операция сериализации должна выполняться без ошибок."""
    core = FakeCore(valid=True)
    runtime = Runtime(core=core)
    session = runtime.create_session({"data": "test"})
    tx = runtime.begin_transaction(session)
    tx.add_operation(SerializeOperation("op2", knowledge_structure=session.knowledge_structure))

    version = runtime.commit_transaction(tx)
    assert version is not None


def test_commit_with_explain_operation():
    """Операция объяснения должна выполняться без ошибок."""
    core = FakeCore(valid=True)
    runtime = Runtime(core=core)
    session = runtime.create_session({"x": 1})
    tx = runtime.begin_transaction(session)
    tx.add_operation(ExplainOperation("op3", knowledge_structure=session.knowledge_structure))

    version = runtime.commit_transaction(tx)
    assert version is not None


def test_commit_with_multiple_operations():
    """Несколько операций в транзакции должны выполняться последовательно."""
    core = FakeCore(valid=True)
    runtime = Runtime(core=core)
    session = runtime.create_session({"a": 1})
    tx = runtime.begin_transaction(session)
    tx.add_operation(ValidateOperation("v", knowledge_structure=session.knowledge_structure))
    tx.add_operation(SerializeOperation("s", knowledge_structure=session.knowledge_structure))

    version = runtime.commit_transaction(tx)
    assert version is not None
    # Дополнительно можно проверить, что диагностики собраны, но тут FakeCore их не возвращает.


def test_commit_with_dispatcher_request():
    from cks_runtime.dispatcher.dispatcher import DispatchRequest
    runtime = Runtime(core=FakeCore(valid=True))
    # регистрируем операцию
    runtime.operation_registry.register(ValidateOperation)
    session = runtime.create_session({"x": 1})
    tx = runtime.begin_transaction(session)
    # создаём запрос с нужными параметрами
    req = DispatchRequest(
        operation_id="validate",
        parameters={"knowledge_structure": {"x": 1}},
    )
    tx.add_request(req)
    version = runtime.commit_transaction(tx)
    assert version is not None