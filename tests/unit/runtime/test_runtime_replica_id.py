"""
Unit tests for ``Runtime.replica_id`` (ADR-008 §1) -- the durable
per-process identity, sourced from storage at ``Runtime.create()``,
and the VersionVector bump alongside the existing per-session
``node_id``.

Problem this closes: ``node_id`` alone is minted fresh per Session,
so a replica that restarts (or opens a second working branch) looks,
to a gossip peer doing anti-entropy, like an unrelated clock source
every time -- there is nothing durable to recognize "this is the same
replica I talked to yesterday." ``replica_id`` is that durable
identity; these tests check it survives a process restart against the
same storage, and that ordinary commits bump both keys, not just one.
"""

from __future__ import annotations

import cks
import pytest

from cks_runtime.operations.operation_types import EvolveOperation
from cks_runtime.runtime import Runtime
from cks_runtime.storage.memory_storage import InMemoryStorage
from cks_runtime.storage.sqlite_storage import SQLiteStorage
from cks_runtime.versioning.version_vector import VersionVector
from cks_runtime_plugins.cks_core import CksCoreAdapter

pytestmark = pytest.mark.asyncio


def make_structure(ids: list[str]) -> cks.KnowledgeStructure:
    objects = [
        cks.KnowledgeObject(cks.ObjectIdentity(id=i, type="Thing", name=i)) for i in ids
    ]
    return cks.KnowledgeStructure(objects)


async def test_bare_runtime_has_no_replica_id():
    """Plain Runtime(...) never ran create()'s async startup: no behaviour change."""
    runtime = Runtime(core=CksCoreAdapter())
    assert runtime.replica_id is None


async def test_runtime_create_sources_replica_id():
    runtime = await Runtime.create(core=CksCoreAdapter(), storage=InMemoryStorage())
    assert runtime.replica_id is not None


async def test_replica_id_durable_across_restart(tmp_path):
    """
    Two separate Runtime.create() calls against the same on-disk
    storage -- simulating a replica process restarting -- must agree
    on replica_id. This is the concrete failure ADR-008 §1 names:
    node_id alone would mint a fresh, unrelated value each time.
    """
    db_path = str(tmp_path / "replica.db")

    runtime_a = await Runtime.create(core=CksCoreAdapter(), storage=SQLiteStorage(db_path))
    first_id = runtime_a.replica_id
    assert first_id is not None

    # Simulate the process restarting: a fresh Runtime against the
    # same underlying database, as a real restart would reopen it.
    runtime_b = await Runtime.create(core=CksCoreAdapter(), storage=SQLiteStorage(db_path))
    assert runtime_b.replica_id == first_id


async def test_commit_bumps_vector_under_both_node_id_and_replica_id():
    runtime = await Runtime.create(core=CksCoreAdapter(), storage=InMemoryStorage())
    session = await runtime.create_session(make_structure(["root"]))
    node_id = session.metadata["node_id"]
    replica_id = runtime.replica_id
    assert replica_id is not None
    assert replica_id != node_id

    tx = runtime.begin_transaction(session)
    tx.add_operation(
        EvolveOperation(
            "evolve",
            knowledge_structure=session.knowledge_structure,
            evolution=[
                cks.evolution.AddObject(
                    cks.KnowledgeObject(cks.ObjectIdentity(id="x", type="Thing", name="x"))
                )
            ],
        )
    )
    await runtime.commit_transaction(tx)

    vector = VersionVector.from_metadata(session.metadata)
    assert vector.clocks.get(node_id) == 1
    assert vector.clocks.get(replica_id) == 1


async def test_bare_runtime_commit_leaves_vector_untouched():
    """
    A Runtime that never went through create() (replica_id is None)
    behaves exactly as before this change: only node_id is bumped,
    matching every pre-existing VersionManager test's expectations.
    """
    runtime = Runtime(core=CksCoreAdapter(), storage=InMemoryStorage())
    session = await runtime.create_session(make_structure(["root"]))
    node_id = session.metadata["node_id"]

    tx = runtime.begin_transaction(session)
    tx.add_operation(
        EvolveOperation(
            "evolve",
            knowledge_structure=session.knowledge_structure,
            evolution=[
                cks.evolution.AddObject(
                    cks.KnowledgeObject(cks.ObjectIdentity(id="x", type="Thing", name="x"))
                )
            ],
        )
    )
    await runtime.commit_transaction(tx)

    vector = VersionVector.from_metadata(session.metadata)
    assert vector.clocks == {node_id: 1}
