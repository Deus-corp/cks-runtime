"""
Integration tests for ADR-013 Stage 1: GossipAdapter feeding
KnowledgeObjects from an incoming remote session into a CRDTStore
G-Set via ``_merge_crdt_objects``.
"""

from __future__ import annotations

from uuid import uuid4

import cks
import pytest

from cks_runtime.crdt.crdt_store import InMemoryCRDTStore
from cks_runtime.gossip.adapter import GossipAdapter
from cks_runtime.runtime import Runtime
from cks_runtime.session.session import RuntimeSession
from cks_runtime_plugins.cks_core import CksCoreAdapter

pytestmark = pytest.mark.asyncio


def make_structure(ids: list[str]) -> cks.KnowledgeStructure:
    objects = [
        cks.KnowledgeObject(cks.ObjectIdentity(id=i, type="Thing", name=i))
        for i in ids
    ]
    return cks.KnowledgeStructure(objects)


async def _replica_with_crdt_store() -> tuple[Runtime, GossipAdapter, InMemoryCRDTStore]:
    runtime = await Runtime.create(core=CksCoreAdapter())
    store = InMemoryCRDTStore()
    adapter = GossipAdapter(runtime, replica_id=str(uuid4()), crdt_store=store)
    return runtime, adapter, store


async def test_apply_remote_session_populates_crdt_store_on_bootstrap():
    """
    A session this replica has never seen before (bootstrap path)
    still feeds its objects into the CRDT G-Set.
    """
    _runtime, adapter, store = await _replica_with_crdt_store()

    remote_session = RuntimeSession(
        knowledge_structure=make_structure(["a", "b", "c"]),
        session_id=str(uuid4()),
    )

    applied = await adapter.apply_remote_session(remote_session)

    assert applied is True
    assert len(store.list_objects()) == 3


async def test_apply_remote_session_populates_crdt_store_on_fast_forward():
    """
    When two replicas already track the same session_id and the
    remote is strictly ahead (fast-forward path), the CRDT store still
    picks up the remote's objects.
    """
    runtime, adapter, store = await _replica_with_crdt_store()

    session_id = str(uuid4())
    local_session = RuntimeSession(
        knowledge_structure=make_structure(["a"]),
        session_id=session_id,
    )
    local_session.metadata["node_id"] = str(uuid4())
    runtime._sessions.restore(local_session)
    await runtime.storage.save_session(local_session)

    remote_session = RuntimeSession(
        knowledge_structure=make_structure(["a", "b"]),
        session_id=session_id,
    )

    await adapter.apply_remote_session(remote_session)

    object_ids = {record["identity"]["id"] for record in store.list_objects()}
    assert {"a", "b"}.issubset(object_ids)


async def test_merge_crdt_objects_deduplicates_across_calls():
    """
    Gossiping the same session content twice (e.g. two rounds before
    anything new happened) must not double-add to the G-Set.
    """
    _runtime, adapter, store = await _replica_with_crdt_store()

    remote_session = RuntimeSession(
        knowledge_structure=make_structure(["x", "y"]),
        session_id=str(uuid4()),
    )

    first = await adapter._merge_crdt_objects(remote_session)
    second = await adapter._merge_crdt_objects(remote_session)

    assert first == 2
    assert second == 0
    assert len(store.list_objects()) == 2


async def test_apply_remote_session_without_crdt_store_is_unaffected():
    """
    A GossipAdapter built without a crdt_store (the default) behaves
    exactly as before -- no crash, no attribute error.
    """
    runtime = await Runtime.create(core=CksCoreAdapter())
    adapter = GossipAdapter(runtime, replica_id=str(uuid4()))

    remote_session = RuntimeSession(
        knowledge_structure=make_structure(["a"]),
        session_id=str(uuid4()),
    )

    applied = await adapter.apply_remote_session(remote_session)
    assert applied is True


async def test_crdt_store_populated_even_when_session_merge_conflicts():
    """
    The G-Set must reflect every object this replica has ever seen,
    even for a remote session whose session-level reconciliation ends
    in an (unresolved) conflict rather than a clean merge/fast-forward.
    """
    runtime, adapter, store = await _replica_with_crdt_store()

    session_id = str(uuid4())
    local_session = RuntimeSession(
        knowledge_structure=make_structure(["a", "local-only"]),
        session_id=session_id,
    )
    local_session.metadata["node_id"] = str(uuid4())
    runtime._sessions.restore(local_session)
    await runtime.storage.save_session(local_session)

    # Diverging remote content with no resolvable common ancestor and
    # no dominance relation either way -- neither empty vector
    # dominates the other, and structures differ, so this exercises
    # the merge-probe path rather than fast-forward/no-op.
    remote_session = RuntimeSession(
        knowledge_structure=make_structure(["a", "remote-only"]),
        session_id=session_id,
    )

    await adapter.apply_remote_session(remote_session)

    object_ids = {record["identity"]["id"] for record in store.list_objects()}
    # Regardless of how the session-level merge resolved, every
    # KnowledgeObject that ever passed through apply_remote_session
    # is in the G-Set.
    assert {"a", "remote-only"}.issubset(object_ids)
