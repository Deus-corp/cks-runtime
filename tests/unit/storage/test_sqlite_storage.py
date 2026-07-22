"""
Tests for SQLiteStorage (JSON-based).
"""

from __future__ import annotations

import pytest
from cks_runtime.session.session import RuntimeSession
from cks_runtime.storage.sqlite_storage import SQLiteStorage
from cks_runtime.versioning.version import RuntimeVersion
import cks


@pytest.fixture
def storage():
    """Create a fresh in-memory SQLiteStorage for each test."""
    store = SQLiteStorage(":memory:")
    yield store
    store.clear()


def make_ks():
    """Minimal valid knowledge structure for testing."""
    return cks.parse(
        '{"objects":[{"identity":{"id":"obj-1","type":"Test","name":"t"},"structure":{}}]}'
    )


def make_session(session_id: str = "s1") -> RuntimeSession:
    return RuntimeSession(
        knowledge_structure=make_ks(),
        session_id=session_id,
    )


def make_version(
    session_id: str = "s1",
    version_id: str = "v1",
    ks=None,
) -> RuntimeVersion:
    if ks is None:
        ks = make_ks()
    return RuntimeVersion(
        session_id=session_id,
        transaction_id="t1",
        knowledge_structure=ks,
        metadata={"m": 1},
        version_id=version_id,
    )


def test_save_and_load_session(storage):
    session = make_session("s1")
    storage.save_session(session)
    loaded = storage.load_session("s1")
    assert loaded is not None
    assert loaded.session_id == "s1"
    # Compare serialized forms to avoid deep structure issues
    assert cks.serialize(loaded.knowledge_structure) == cks.serialize(make_ks())


def test_load_missing_session_returns_none(storage):
    assert storage.load_session("missing") is None


def test_has_session(storage):
    assert not storage.has_session("s1")
    storage.save_session(make_session("s1"))
    assert storage.has_session("s1")


def test_list_sessions(storage):
    storage.save_session(make_session("s1"))
    storage.save_session(make_session("s2"))
    sessions = storage.list_sessions()
    assert len(sessions) == 2
    ids = {s.session_id for s in sessions}
    assert ids == {"s1", "s2"}


def test_save_and_load_version(storage):
    version = make_version("s1", "v1")
    storage.save_version(version)
    loaded = storage.load_version("v1")
    assert loaded is not None
    assert loaded.version_id == "v1"
    assert loaded.session_id == "s1"
    assert cks.serialize(loaded.knowledge_structure) == cks.serialize(make_ks())


def test_load_missing_version_returns_none(storage):
    assert storage.load_version("missing") is None


def test_has_version(storage):
    assert not storage.has_version("v1")
    storage.save_version(make_version("s1", "v1"))
    assert storage.has_version("v1")


def test_list_versions(storage):
    storage.save_version(make_version("s1", "v1"))
    storage.save_version(make_version("s2", "v2"))
    versions = storage.list_versions()
    assert len(versions) == 2
    vids = {v.version_id for v in versions}
    assert vids == {"v1", "v2"}


def test_clear(storage):
    storage.save_session(make_session("s1"))
    storage.save_version(make_version("s1", "v1"))
    storage.clear()
    assert not storage.has_session("s1")
    assert not storage.has_version("v1")