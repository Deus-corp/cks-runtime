import pytest

from cks_runtime.session.session import RuntimeSession
from cks_runtime.storage.memory_storage import InMemoryStorage
from cks_runtime.versioning.version import RuntimeVersion


def test_save_and_restore_session():
    storage = InMemoryStorage()
    session = RuntimeSession(knowledge_structure={})
    storage.save_session(session)
    restored = storage.load_session(session.session_id)
    assert restored.session_id == session.session_id
    assert restored is not session


def test_save_and_restore_version():
    storage = InMemoryStorage()
    version = RuntimeVersion(
        session_id="s", transaction_id="t",
        knowledge_structure={}, metadata={},
    )
    storage.save_version(version)
    restored = storage.load_version(version.version_id)
    assert restored.version_id == version.version_id
    assert restored is not version


def test_has_session():
    storage = InMemoryStorage()
    session = RuntimeSession(knowledge_structure={})
    storage.save_session(session)
    assert storage.has_session(session.session_id)


def test_has_version():
    storage = InMemoryStorage()
    version = RuntimeVersion(
        session_id="s", transaction_id="t",
        knowledge_structure={}, metadata={},
    )
    storage.save_version(version)
    assert storage.has_version(version.version_id)


def test_clear():
    storage = InMemoryStorage()
    session = RuntimeSession(knowledge_structure={})
    storage.save_session(session)
    storage.clear()
    assert storage.has_session(session.session_id) is False


def test_missing_session():
    storage = InMemoryStorage()
    assert storage.load_session("missing") is None


def test_missing_version():
    storage = InMemoryStorage()
    assert storage.load_version("missing") is None


def test_storage_returns_deep_copy():
    storage = InMemoryStorage()
    session = RuntimeSession(knowledge_structure={"value": 1})
    storage.save_session(session)
    restored = storage.load_session(session.session_id)
    restored.knowledge_structure["value"] = 42
    restored_again = storage.load_session(session.session_id)
    assert restored_again.knowledge_structure["value"] == 1