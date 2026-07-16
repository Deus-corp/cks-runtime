from cks_runtime.versioning.version import RuntimeVersion
from cks_runtime import Runtime

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