from cks_runtime.session.session_manager import SessionManager


def test_create_session():

    manager = SessionManager()

    session = manager.create_session(
        knowledge_structure={}
    )

    assert session is not None
    assert session.knowledge_structure == {}

    assert len(
        manager.list_sessions()
    ) == 1


def test_get_session():

    manager = SessionManager()

    created = manager.create_session(
        knowledge_structure={}
    )

    loaded = manager.get_session(
        created.session_id
    )

    assert loaded is created


def test_close_session():

    manager = SessionManager()

    session = manager.create_session(
        knowledge_structure={}
    )

    assert manager.close_session(
        session.session_id
    ) is True

    assert manager.get_session(
        session.session_id
    ) is None

    assert len(
        manager.list_sessions()
    ) == 0


def test_close_unknown_session():

    manager = SessionManager()

    assert manager.close_session(
        "unknown"
    ) is False


def test_unknown_session_returns_none():

    manager = SessionManager()

    assert manager.get_session(
        "unknown"
    ) is None


def test_list_sessions_returns_tuple():

    manager = SessionManager()

    manager.create_session({})

    sessions = manager.list_sessions()

    assert isinstance(
        sessions,
        tuple,
    )


def test_list_sessions_returns_new_tuple():

    manager = SessionManager()

    manager.create_session({})

    first = manager.list_sessions()

    second = manager.list_sessions()

    assert first == second

    assert first is not second