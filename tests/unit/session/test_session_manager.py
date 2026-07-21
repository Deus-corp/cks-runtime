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


def test_create_branch():

    manager = SessionManager()

    parent = manager.create_session(
        knowledge_structure={"a": 1}
    )

    branch = manager.create_branch(
        parent,
        {"a": 1, "b": 2},
    )

    assert branch is not None
    assert branch.knowledge_structure == {"a": 1, "b": 2}
    assert branch.parent_session_id == parent.session_id
    assert branch.parent_version_id is None
    assert branch.is_branch is True

    assert len(
        manager.list_sessions()
    ) == 2


def test_create_branch_records_parent_version_id():

    manager = SessionManager()

    parent = manager.create_session(
        knowledge_structure={"a": 1}
    )

    branch = manager.create_branch(
        parent,
        {"a": 1},
        parent_version_id="v-1",
    )

    assert branch.parent_version_id == "v-1"


def test_create_branch_has_its_own_identifier():

    manager = SessionManager()

    parent = manager.create_session(
        knowledge_structure={}
    )

    branch = manager.create_branch(
        parent,
        {},
    )

    assert branch.session_id != parent.session_id

    assert manager.get_session(
        branch.session_id
    ) is branch


def test_create_branch_does_not_mutate_parent():

    manager = SessionManager()

    parent = manager.create_session(
        knowledge_structure={"a": 1}
    )

    manager.create_branch(
        parent,
        {"a": 1, "b": 2},
    )

    assert parent.knowledge_structure == {"a": 1}
    assert parent.is_branch is False


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