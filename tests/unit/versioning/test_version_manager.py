from cks_runtime.session.session_manager import SessionManager
from cks_runtime.versioning.version_manager import VersionManager
from cks_runtime.versioning.version_vector import VersionVector


def create_session():

    sessions = SessionManager()

    return sessions.create_session(
        knowledge_structure={}
    )


def test_create_version():

    session = create_session()

    manager = VersionManager()

    version = manager.create(session)

    assert version in session.version_history


def test_latest_version():

    session = create_session()

    manager = VersionManager()

    assert manager.latest(session) is None

    first = manager.create(session)

    assert manager.latest(session) == first

    second = manager.create(session)

    assert manager.latest(session) == second


def test_retrieve_version():

    session = create_session()

    manager = VersionManager()

    version = manager.create(session)

    retrieved = manager.retrieve(
        session,
        version.version_id,
    )

    assert retrieved == version


def test_retrieve_unknown_version():

    session = create_session()

    manager = VersionManager()

    assert manager.retrieve(
        session,
        "unknown",
    ) is None


def test_list_versions_empty():

    session = create_session()

    manager = VersionManager()

    history = manager.list_versions(session)

    assert history == ()


def test_list_versions():

    session = create_session()

    manager = VersionManager()

    manager.create(session)
    manager.create(session)

    history = manager.list_versions(session)

    assert len(history) == 2

    assert isinstance(history, tuple)


def test_list_versions_returns_new_tuple():

    session = create_session()

    manager = VersionManager()

    manager.create(session)

    history1 = manager.list_versions(session)
    history2 = manager.list_versions(session)

    assert history1 == history2
    assert history1 is not history2


def test_version_is_snapshot():

    session = create_session()

    manager = VersionManager()

    version = manager.create(session)

    session.knowledge_structure["new"] = 123

    session.metadata["user"] = "runtime"

    assert version.knowledge_structure == {}

    assert version.metadata.get("node_id") is not None
    assert "user" not in version.metadata

def test_create_bumps_vector_for_node_id_only_by_default():

    session = create_session()

    manager = VersionManager()

    manager.create(session, node_id="node-a")

    vector = VersionVector.from_metadata(session.metadata)

    assert vector.clocks == {"node-a": 1}


def test_create_bumps_vector_for_replica_id_in_addition_to_node_id():
    """ADR-008 §1: replica_id is bumped *in addition to*, not instead of, node_id."""

    session = create_session()

    manager = VersionManager()

    manager.create(session, node_id="node-a", replica_id="replica-x")

    vector = VersionVector.from_metadata(session.metadata)

    assert vector.clocks == {"node-a": 1, "replica-x": 1}


def test_create_bumps_vector_for_replica_id_alone_when_node_id_absent():

    session = create_session()

    manager = VersionManager()

    manager.create(session, replica_id="replica-x")

    vector = VersionVector.from_metadata(session.metadata)

    assert vector.clocks == {"replica-x": 1}


def test_create_leaves_vector_untouched_when_neither_id_given():

    session = create_session()

    manager = VersionManager()

    manager.create(session)

    vector = VersionVector.from_metadata(session.metadata)

    assert vector.clocks == {}
