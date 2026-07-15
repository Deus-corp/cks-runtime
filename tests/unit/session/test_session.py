from cks_runtime.session.session import RuntimeSession


def test_session_defaults() -> None:
    session = RuntimeSession(
        knowledge_structure={}
    )

    assert session.is_active

    assert session.closed is False

    assert session.metadata == {}

    assert session.diagnostics == []

    assert session.version_history == []

    assert session.active_transaction is None


def test_session_close() -> None:
    session = RuntimeSession(
        knowledge_structure={}
    )

    session.close()

    assert session.closed is True

    assert session.is_active is False


def test_session_has_unique_identifier() -> None:
    session1 = RuntimeSession(
        knowledge_structure={}
    )

    session2 = RuntimeSession(
        knowledge_structure={}
    )

    assert session1.session_id != session2.session_id