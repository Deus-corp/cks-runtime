from cks_runtime.session.session_manager import SessionManager
from cks_runtime.versioning.version_manager import VersionManager


def create_session():

    sessions = SessionManager()

    return sessions.create({})


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