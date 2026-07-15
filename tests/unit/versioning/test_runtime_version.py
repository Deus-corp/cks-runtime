from dataclasses import FrozenInstanceError

from cks_runtime.versioning.version import RuntimeVersion


def create_version():

    return RuntimeVersion(
        session_id="session-1",
        transaction_id="tx-1",
        knowledge_structure={},
        metadata={},
    )


def test_runtime_version_creation():

    version = create_version()

    assert version.version_id is not None
    assert version.created_at is not None

    assert version.session_id == "session-1"
    assert version.transaction_id == "tx-1"


def test_runtime_version_is_frozen():

    version = create_version()

    try:
        version.version_id = "modified"

        assert False

    except FrozenInstanceError:
        pass