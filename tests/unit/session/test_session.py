from cks_runtime.session.session import RuntimeSession


def test_session_defaults():

    session = RuntimeSession(
        knowledge_structure={}
    )

    assert session.knowledge_structure == {}

    assert session.is_active

    assert session.closed is False

    assert session.metadata == {}

    assert session.diagnostics == []

    assert session.version_history == []

    assert session.active_transaction is None

    assert session.parent_session_id is None

    assert session.parent_version_id is None

    assert session.is_branch is False


def test_session_records_branch_parentage():

    session = RuntimeSession(
        knowledge_structure={},
        parent_session_id="parent-1",
        parent_version_id="v-1",
    )

    assert session.parent_session_id == "parent-1"

    assert session.parent_version_id == "v-1"

    assert session.is_branch is True


def test_session_is_branch_requires_parent_session_id():

    session = RuntimeSession(
        knowledge_structure={},
        parent_version_id="v-1",
    )

    # A dangling parent_version_id with no parent_session_id does not
    # make this a branch -- is_branch tracks parent session lineage.
    assert session.is_branch is False


def test_session_close():

    session = RuntimeSession(
        knowledge_structure={}
    )

    session.close()

    assert session.closed is True

    assert session.is_active is False


def test_session_has_unique_identifier():

    session1 = RuntimeSession(
        knowledge_structure={}
    )

    session2 = RuntimeSession(
        knowledge_structure={}
    )

    assert session1.session_id != session2.session_id


def test_close_is_idempotent():

    session = RuntimeSession(
        knowledge_structure={}
    )

    session.close()

    session.close()

    assert session.closed is True

    assert session.is_active is False