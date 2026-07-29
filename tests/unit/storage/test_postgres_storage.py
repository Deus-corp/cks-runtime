"""
Tests for PostgresStorage (async, JSONB-based).

Requires a reachable PostgreSQL instance -- set CKS_TEST_POSTGRES_DSN
to point at one, or these tests are skipped. Mirrors the equivalent
cases in test_sqlite_storage.py (round-trip, CAS accept/reject,
duplicate version rejection) so the two backends are checked against
the same behavioural contract, not just independently self-consistent.
"""

from __future__ import annotations

import os

import cks
import pytest
import pytest_asyncio

from cks_runtime.session.session import RuntimeSession

try:
    from cks_runtime.storage.postgres_storage import PostgresStorage
except ImportError:
    PostgresStorage = None  # psycopg not installed
from cks_runtime.storage.storage import ConcurrentModificationError
from cks_runtime.versioning.version import RuntimeVersion

_DSN = os.environ.get("CKS_TEST_POSTGRES_DSN")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not _DSN or PostgresStorage is None, reason="CKS_TEST_POSTGRES_DSN not set or psycopg not installed"),
]


def make_ks():
    return cks.parse(
        '{"objects":[{"identity":{"id":"obj-1","type":"Test","name":"t"},"structure":{}}]}'
    )


def make_session(session_id: str = "s1") -> RuntimeSession:
    return RuntimeSession(knowledge_structure=make_ks(), session_id=session_id)


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


@pytest_asyncio.fixture
async def storage():
    store = await PostgresStorage.connect(_DSN, min_size=1, max_size=4)
    await store.clear()
    yield store
    await store.clear()
    await store.close()


async def test_save_and_load_session(storage):
    session = make_session("s1")
    await storage.save_session(session)
    loaded = await storage.load_session("s1")
    assert loaded is not None
    assert loaded.session_id == "s1"


async def test_load_missing_session_returns_none(storage):
    assert await storage.load_session("does-not-exist") is None


async def test_save_session_cas_accepts_matching_expected_version(storage):
    session = make_session("s1")
    session.add_version(make_version("s1", "v1"))
    await storage.save_version(make_version("s1", "v1"))
    await storage.save_session(session)  # initial write, no CAS

    session.add_version(make_version("s1", "v2"))
    await storage.save_version(make_version("s1", "v2"))
    await storage.save_session(session, expected_version_id="v1")

    loaded = await storage.load_session("s1")
    assert [v.version_id for v in loaded.version_history] == ["v1", "v2"]


async def test_save_session_cas_rejects_stale_expected_version(storage):
    session = make_session("s1")
    session.add_version(make_version("s1", "v1"))
    await storage.save_version(make_version("s1", "v1"))
    await storage.save_session(session)

    # Simulate a second writer racing in and committing v2 first.
    racer = make_session("s1")
    racer.add_version(make_version("s1", "v1"))
    racer.add_version(make_version("s1", "v2"))
    await storage.save_version(make_version("s1", "v2"))
    await storage.save_session(racer, expected_version_id="v1")

    # Original writer, still working off v1, tries to commit v3 --
    # must be rejected rather than silently clobbering v2.
    session.add_version(make_version("s1", "v3"))
    with pytest.raises(ConcurrentModificationError):
        await storage.save_session(session, expected_version_id="v1")

    loaded = await storage.load_session("s1")
    assert [v.version_id for v in loaded.version_history] == ["v1", "v2"]


async def test_save_version_rejects_duplicate_version_id(storage):
    import psycopg

    await storage.save_version(make_version("s1", "v1"))
    with pytest.raises(psycopg.errors.UniqueViolation):
        await storage.save_version(make_version("s1", "v1"))


async def test_has_session(storage):
    assert not await storage.has_session("s1")
    await storage.save_session(make_session("s1"))
    assert await storage.has_session("s1")


async def test_list_sessions(storage):
    await storage.save_session(make_session("s1"))
    await storage.save_session(make_session("s2"))
    sessions = await storage.list_sessions()
    assert {s.session_id for s in sessions} == {"s1", "s2"}


async def test_save_and_load_version(storage):
    version = make_version("s1", "v1")
    await storage.save_version(version)
    loaded = await storage.load_version("v1")
    assert loaded is not None
    assert loaded.version_id == "v1"
    assert loaded.session_id == "s1"
    assert loaded.metadata == {"m": 1}


async def test_load_missing_version_returns_none(storage):
    assert await storage.load_version("does-not-exist") is None


async def test_has_version(storage):
    assert not await storage.has_version("v1")
    await storage.save_version(make_version("s1", "v1"))
    assert await storage.has_version("v1")


async def test_list_versions(storage):
    await storage.save_version(make_version("s1", "v1"))
    await storage.save_version(make_version("s1", "v2"))
    versions = await storage.list_versions()
    assert {v.version_id for v in versions} == {"v1", "v2"}


async def test_clear(storage):
    await storage.save_session(make_session("s1"))
    await storage.save_version(make_version("s1", "v1"))
    await storage.clear()
    assert await storage.load_session("s1") is None
    assert await storage.load_version("v1") is None


async def test_delta_version_round_trips_patch(storage):
    """A version with patch (no knowledge_structure) round-trips via patch_codec."""
    # Build a delta version directly with a patch instead of a full snapshot.
    from cks.core import KnowledgeObject, ObjectIdentity
    from cks.evolution import AddObject

    new_obj = KnowledgeObject(
        identity=ObjectIdentity(id="obj-2", type="Test", name="t2"),
        structure={},
    )
    version = RuntimeVersion(
        session_id="s1",
        transaction_id="t1",
        knowledge_structure=None,
        metadata={},
        version_id="v-delta",
        patch=[AddObject(new_obj)],
    )
    await storage.save_version(version)
    loaded = await storage.load_version("v-delta")
    assert loaded is not None
    assert loaded.knowledge_structure is None
    assert loaded.patch is not None
    assert len(loaded.patch) == 1


async def test_concurrent_cas_writes_exactly_one_winner(storage):
    """
    Two concurrent tasks race to commit v2 via CAS from the same base
    (v1). Exactly one must succeed; the other must see
    ConcurrentModificationError -- not a corrupted/merged session row.
    """
    import asyncio

    base = make_session("s1")
    base.add_version(make_version("s1", "v1"))
    await storage.save_version(make_version("s1", "v1"))
    await storage.save_session(base)

    async def _try_commit(version_id: str):
        session = make_session("s1")
        session.add_version(make_version("s1", "v1"))
        session.add_version(make_version("s1", version_id))
        await storage.save_version(make_version("s1", version_id))
        try:
            await storage.save_session(session, expected_version_id="v1")
            return "ok"
        except ConcurrentModificationError:
            return "rejected"

    results = await asyncio.gather(
        _try_commit("v2a"), _try_commit("v2b"), return_exceptions=False
    )
    assert sorted(results) == ["ok", "rejected"]
