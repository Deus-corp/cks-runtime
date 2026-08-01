"""
Unit tests for GossipAdapter (ADR-008), rewritten against the
session-snapshot design -- see cks_runtime/gossip/adapter.py's module
docstring for why the original field-operation-replay design couldn't
work.

Two independent ``Runtime`` instances (each its own CksCoreAdapter +
in-memory storage) simulate two replicas. A session is registered
under the *same* session_id in both -- via ``SessionManager.restore``,
not ``create_session`` (which always mints a fresh id) -- to simulate
two replicas that already both track one logical distributed session,
matching ``GossipAdapter``'s documented scope (it reconciles a session
that already exists locally; it does not bootstrap a brand-new one).
"""

from __future__ import annotations

import copy
from uuid import uuid4

import cks
import pytest

from cks_runtime.events.event_bus import EventBus
from cks_runtime.events.runtime_event import GossipConflictDetected
from cks_runtime.gossip.adapter import GossipAdapter
from cks_runtime.gossip.exchange import gossip_exchange
from cks_runtime.operations.operation_types import EvolveOperation
from cks_runtime.runtime import Runtime
from cks_runtime.session.session import RuntimeSession
from cks_runtime.versioning.version_vector import VersionVector
from cks_runtime_plugins.cks_core import CksCoreAdapter

pytestmark = pytest.mark.asyncio


def make_structure(ids: list[str]) -> cks.KnowledgeStructure:
    objects = [
        cks.KnowledgeObject(cks.ObjectIdentity(id=i, type="Thing", name=i))
        for i in ids
    ]
    return cks.KnowledgeStructure(objects)


async def _paired_replicas() -> tuple[Runtime, Runtime, str]:
    """
    Two independent Runtimes, each with its own session registered
    under the same session_id -- simulating two replicas that already
    track one logical distributed session (see module docstring).
    """
    runtime_a = await Runtime.create(core=CksCoreAdapter())
    runtime_b = await Runtime.create(core=CksCoreAdapter())

    session_a = await runtime_a.create_session(make_structure(["root"]))

    session_b = RuntimeSession(
        knowledge_structure=make_structure(["root"]),
        session_id=session_a.session_id,
    )
    # Bypassing SessionManager.create_session (the only place that
    # normally mints one) means session_b starts with no node_id in
    # its metadata. Without it, ExecutionPipeline._persist's
    # `node_id = transaction.session.metadata.get("node_id")` is
    # None, so VersionManager.create() silently skips vector.bump()
    # (see its docstring) -- session_b's VersionVector would stay
    # permanently empty even after real commits, breaking every
    # dominates()/fast-forward comparison below.
    session_b.metadata["node_id"] = str(uuid4())
    runtime_b._sessions.restore(session_b)
    await runtime_b.storage.save_session(session_b)

    return runtime_a, runtime_b, session_a.session_id


async def _paired_replicas_with_shared_base() -> tuple[Runtime, Runtime, str, str]:
    """
    Like ``_paired_replicas``, but the two replicas share a genuine
    common-ancestor *version* (not just an identical starting
    structure): replica A commits one version, then replica B is
    constructed as of that exact version with its ``parent_version_id``
    pointing at it. That lets ``MergeOperation`` resolve a real base
    (via ``source_session.parent_version_id``, looked up in local
    replica A's own ``version_history``) instead of failing with
    "could not determine a merge base" -- needed to exercise the
    genuine field-level ``RuntimeMergeConflictError`` path rather than
    the no-common-ancestor path already covered by
    ``test_concurrent_divergence_with_no_common_ancestor_is_escalated``.
    """
    runtime_a = await Runtime.create(core=CksCoreAdapter())
    runtime_b = await Runtime.create(core=CksCoreAdapter())

    session_a = await runtime_a.create_session(make_structure(["root"]))
    await _evolve(runtime_a, session_a, [_add("shared")])
    fork_version = runtime_a.latest_version(session_a)

    session_b = RuntimeSession(
        knowledge_structure=copy.deepcopy(session_a.knowledge_structure),
        session_id=session_a.session_id,
        parent_version_id=fork_version.version_id,
    )
    session_b.metadata["node_id"] = str(uuid4())
    runtime_b._sessions.restore(session_b)
    await runtime_b.storage.save_session(session_b)

    return runtime_a, runtime_b, session_a.session_id, fork_version.version_id


async def _evolve(runtime: Runtime, session: RuntimeSession, operations: list) -> None:
    tx = runtime.begin_transaction(session)
    tx.add_operation(
        EvolveOperation(
            "evolve",
            knowledge_structure=session.knowledge_structure,
            evolution=operations,
        )
    )
    await runtime.commit_transaction(tx)


def _add(obj_id: str) -> cks.evolution.AddObject:
    return cks.evolution.AddObject(
        cks.KnowledgeObject(cks.ObjectIdentity(id=obj_id, type="Thing", name=obj_id))
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestGossipAdapterConstruction:
    async def test_replica_id_property(self):
        runtime = await Runtime.create(core=CksCoreAdapter())
        adapter = GossipAdapter(runtime, "replica-42")
        assert adapter.replica_id == "replica-42"

    async def test_defaults_to_runtime_event_bus(self):
        runtime = await Runtime.create(core=CksCoreAdapter())
        adapter = GossipAdapter(runtime, "r1")
        assert adapter._event_bus is runtime.events

    async def test_accepts_explicit_event_bus(self):
        runtime = await Runtime.create(core=CksCoreAdapter())
        bus = EventBus()
        adapter = GossipAdapter(runtime, "r1", event_bus=bus)
        assert adapter._event_bus is bus


# ---------------------------------------------------------------------------
# get_local_vector
# ---------------------------------------------------------------------------


class TestGetLocalVector:
    async def test_returns_empty_vector_for_unknown_session(self):
        runtime = await Runtime.create(core=CksCoreAdapter())
        adapter = GossipAdapter(runtime, "r1")
        vector = await adapter.get_local_vector("no-such-session")
        assert isinstance(vector, VersionVector)
        assert vector.clocks == {}

    async def test_returns_the_sessions_vector(self):
        runtime = await Runtime.create(core=CksCoreAdapter())
        session = await runtime.create_session(make_structure(["root"]))
        await _evolve(runtime, session, [_add("a")])
        adapter = GossipAdapter(runtime, "r1")
        vector = await adapter.get_local_vector(session.session_id)
        assert vector.clocks == VersionVector.from_metadata(session.metadata).clocks
        assert vector.clocks  # non-empty: at least one commit was recorded


# ---------------------------------------------------------------------------
# apply_remote_session
# ---------------------------------------------------------------------------


class TestApplyRemoteSession:
    async def test_unknown_local_session_returns_false(self):
        runtime = await Runtime.create(core=CksCoreAdapter())
        adapter = GossipAdapter(runtime, "r1")
        remote = RuntimeSession(
            knowledge_structure=make_structure(["root"]), session_id="ghost"
        )
        result = await adapter.apply_remote_session(remote)
        assert result is False

    async def test_no_op_when_local_already_dominates(self):
        runtime_a, runtime_b, session_id = await _paired_replicas()
        session_a = runtime_a.get_session(session_id)
        session_b = runtime_b.get_session(session_id)

        # Only A has committed anything -- A's vector dominates B's
        # (empty) vector, so B's snapshot carries nothing new.
        await _evolve(runtime_a, session_a, [_add("a")])

        adapter_a = GossipAdapter(runtime_a, "replica-a")
        result = await adapter_a.apply_remote_session(session_b)

        assert result is True
        # Unaffected: still just root + a, no gossip-merge version added.
        assert {o.identity.id for o in session_a.knowledge_structure.objects} == {
            "root",
            "a",
        }
    async def test_fast_forwards_when_remote_dominates(self):
        runtime_a, runtime_b, session_id = await _paired_replicas()
        session_a = runtime_a.get_session(session_id)
        session_b = runtime_b.get_session(session_id)

        # Only B has committed -- B's vector dominates A's (empty).
        await _evolve(runtime_b, session_b, [_add("b")])

        adapter_a = GossipAdapter(runtime_a, "replica-a")
        result = await adapter_a.apply_remote_session(session_b)

        assert result is True
        assert {o.identity.id for o in session_a.knowledge_structure.objects} == {
            "root",
            "b",
        }
        # Fast-forward is committed as a real local Version, not just
        # an in-memory mutation.
        assert len(session_a.version_history) >= 1

    async def test_concurrent_divergence_with_no_common_ancestor_is_escalated(self):
        """
        Two replicas that both evolved independently, with no branch
        relationship between their sessions, have no common ancestor
        MergeOperation can resolve automatically (see the adapter
        module docstring's status update). This must be escalated,
        not silently guessed at.
        """
        runtime_a, runtime_b, session_id = await _paired_replicas()
        session_a = runtime_a.get_session(session_id)
        session_b = runtime_b.get_session(session_id)

        await _evolve(runtime_a, session_a, [_add("a")])
        await _evolve(runtime_b, session_b, [_add("b")])

        received: list[GossipConflictDetected] = []
        runtime_a.events.subscribe(GossipConflictDetected, received.append)

        adapter_a = GossipAdapter(runtime_a, "replica-a")
        result = await adapter_a.apply_remote_session(session_b)

        assert result is False
        assert len(received) == 1
        assert received[0].source_replica_id == "replica-a"
        assert received[0].conflicts  # some description of what went wrong

    async def test_real_merge_conflict_is_escalated_not_raised(self):
        """
        A genuine field-level conflict (both sides change the same
        identity's structure differently) surfaces as
        RuntimeMergeConflictError inside MergeOperation -- must be
        escalated the same way, not raised out of apply_remote_session.

        Uses ``_paired_replicas_with_shared_base`` rather than
        ``create_branch``: a branch always mints its own session_id
        (see ``SessionManager.create_branch``), and
        ``apply_remote_session`` looks up the local session via
        ``remote_session.session_id`` -- passing a branch straight in
        would resolve "local" right back to the branch itself (a
        trivial self-comparison), never reaching a real merge. Two
        replicas sharing one session_id, as gossip actually works, is
        the only way to exercise this path.
        """
        from cks.evolution import UpdateObject

        runtime_a, runtime_b, session_id, _fork_version_id = (
            await _paired_replicas_with_shared_base()
        )
        session_a = runtime_a.get_session(session_id)
        session_b = runtime_b.get_session(session_id)

        await _evolve(
            runtime_a,
            session_a,
            [UpdateObject("root", structure_patch={"k": "from-a"})],
        )
        await _evolve(
            runtime_b,
            session_b,
            [UpdateObject("root", structure_patch={"k": "from-b"})],
        )

        received: list[GossipConflictDetected] = []
        runtime_a.events.subscribe(GossipConflictDetected, received.append)

        adapter_a = GossipAdapter(runtime_a, "replica-a")
        result = await adapter_a.apply_remote_session(session_b)

        assert result is False
        assert len(received) == 1
        assert received[0].conflicts == ["root"]


# ---------------------------------------------------------------------------
# gossip_exchange
# ---------------------------------------------------------------------------


class TestGossipExchange:
    async def test_both_sides_converge(self):
        runtime_a, runtime_b, session_id = await _paired_replicas()
        session_a = runtime_a.get_session(session_id)
        session_b = runtime_b.get_session(session_id)

        await _evolve(runtime_a, session_a, [_add("a")])
        await _evolve(runtime_b, session_b, [_add("b")])

        adapter_a = GossipAdapter(runtime_a, "replica-a")
        adapter_b = GossipAdapter(runtime_b, "replica-b")

        # Concurrent divergence with no common ancestor: exchange
        # escalates on both sides rather than corrupting either.
        await gossip_exchange(session_id, adapter_a, adapter_b)

        assert {o.identity.id for o in session_a.knowledge_structure.objects} == {
            "root",
            "a",
        }
        assert {o.identity.id for o in session_b.knowledge_structure.objects} == {
            "root",
            "b",
        }

    async def test_one_sided_update_converges_the_other(self):
        runtime_a, runtime_b, session_id = await _paired_replicas()
        session_a = runtime_a.get_session(session_id)
        session_b = runtime_b.get_session(session_id)

        await _evolve(runtime_a, session_a, [_add("a")])

        adapter_a = GossipAdapter(runtime_a, "replica-a")
        adapter_b = GossipAdapter(runtime_b, "replica-b")

        await gossip_exchange(session_id, adapter_a, adapter_b)

        assert {o.identity.id for o in session_b.knowledge_structure.objects} == {
            "root",
            "a",
        }