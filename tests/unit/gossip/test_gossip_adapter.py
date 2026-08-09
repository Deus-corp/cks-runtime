"""
Unit tests for GossipAdapter (ADR-008), rewritten against the
session-snapshot design -- see cks_runtime/gossip/adapter.py's module
docstring for why the original field-operation-replay design couldn't
work.

Two independent ``Runtime`` instances (each its own CksCoreAdapter +
in-memory storage) simulate two replicas. For the reconciliation
tests (``TestApplyRemoteSession``, ``TestGossipExchange``), a session
is registered under the *same* session_id in both -- via
``SessionManager.restore``, not ``create_session`` (which always
mints a fresh id) -- to simulate two replicas that already both track
one logical distributed session. The bootstrap tests are the
exception: they exercise the *other* documented scope, adopting a
session_id one replica has never seen at all (see
``_bootstrap_remote_session`` in ``cks_runtime/gossip/adapter.py``).
"""

from __future__ import annotations

import asyncio
import copy
from unittest import mock
from uuid import uuid4

import cks
import pytest

from cks_runtime.events.event_bus import EventBus
from cks_runtime.events.runtime_event import (
    DuplicateReplicaIdDetected,
    GossipConflictDetected,
)
from cks_runtime.gossip.adapter import GossipAdapter
from cks_runtime.gossip.exchange import gossip_exchange
from cks_runtime.operations.operation_types import (
    EMPTY_STATE_VERSION_ID,
    EvolveOperation,
)
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
    async def test_unknown_local_session_bootstraps_it(self):
        """
        ADR-008's bootstrap gap: a session this replica has never
        tracked before is no longer rejected -- it's adopted as a new
        local session, registered the same way a session restored
        from local storage at startup would be.
        """
        runtime = await Runtime.create(core=CksCoreAdapter())
        adapter = GossipAdapter(runtime, "r1")
        remote = RuntimeSession(
            knowledge_structure=make_structure(["root"]), session_id="ghost"
        )
        result = await adapter.apply_remote_session(remote)

        assert result is True
        local = runtime.get_session("ghost")
        assert local is not None
        assert {o.identity.id for o in local.knowledge_structure.objects} == {"root"}
        # Committed as a real local Version, not just an in-memory write.
        assert len(local.version_history) >= 1

    async def test_bootstrap_mints_a_fresh_local_node_id(self):
        """
        The bootstrapped session must not inherit the remote's
        node_id -- a later local commit has to bump *this* replica's
        own clock, not silently masquerade as another commit from the
        remote's node_id (see _bootstrap_remote_session's docstring).
        """
        runtime = await Runtime.create(core=CksCoreAdapter())
        adapter = GossipAdapter(runtime, "r1")
        remote_node_id = str(uuid4())
        remote = RuntimeSession(
            knowledge_structure=make_structure(["root"]),
            session_id="ghost",
            metadata={"node_id": remote_node_id},
        )
        await adapter.apply_remote_session(remote)

        local = runtime.get_session("ghost")
        assert local.metadata["node_id"] != remote_node_id

    async def test_bootstrap_preserves_the_remotes_already_committed_vector(self):
        """
        A remote session that already has commit history behind it
        (a non-empty VersionVector) must hand that history to the
        bootstrapped local session unchanged -- otherwise a
        subsequent gossip round from a third replica that already
        saw the original remote's commits would look like a genuine
        conflict instead of something this replica just hasn't heard
        about yet.
        """
        runtime_source = await Runtime.create(core=CksCoreAdapter())
        source_session = await runtime_source.create_session(make_structure(["root"]))
        await _evolve(runtime_source, source_session, [_add("a")])
        remote_vector = VersionVector.from_metadata(source_session.metadata)
        assert remote_vector.clocks  # sanity: the source really did commit

        runtime = await Runtime.create(core=CksCoreAdapter())
        adapter = GossipAdapter(runtime, "r1")
        await adapter.apply_remote_session(source_session)

        local = runtime.get_session(source_session.session_id)
        local_vector = VersionVector.from_metadata(local.metadata)
        assert local_vector.dominates(remote_vector)
        for node_id, clock in remote_vector.clocks.items():
            assert local_vector.clocks[node_id] >= clock

    async def test_bootstrap_anchors_to_empty_state_regardless_of_remote_parent(self):
        """
        The bootstrapped local copy's parent_version_id is always
        EMPTY_STATE_VERSION_ID -- never copied from the remote's own
        parent_version_id, even when the remote has one. The remote's
        recorded fork point lives in the remote's own version_history,
        which this replica has never seen and never receives (gossip
        carries snapshots, not history) -- so it would be a dangling
        pointer here, not a usable common ancestor.
        """
        runtime = await Runtime.create(core=CksCoreAdapter())
        adapter = GossipAdapter(runtime, "r1")
        remote = RuntimeSession(
            knowledge_structure=make_structure(["root"]),
            session_id="ghost",
            parent_version_id=str(uuid4()),  # some real version on the remote's side
        )
        await adapter.apply_remote_session(remote)

        local = runtime.get_session("ghost")
        assert local.parent_version_id == EMPTY_STATE_VERSION_ID

    async def test_anchor_genesis_sets_parent_version_id(self):
        runtime = await Runtime.create(core=CksCoreAdapter())
        session = await runtime.create_session(make_structure(["root"]))
        assert session.parent_version_id is None

        GossipAdapter.anchor_genesis(session)

        assert session.parent_version_id == EMPTY_STATE_VERSION_ID

    async def test_bootstrap_persists_the_new_session_to_storage(self):
        """
        Not just an in-memory registration -- a restart (or another
        code path reading straight from storage) must see it too,
        matching every other place Runtime persists a session it
        just registered (create_session, create_branch).
        """
        runtime = await Runtime.create(core=CksCoreAdapter())
        adapter = GossipAdapter(runtime, "r1")
        remote = RuntimeSession(
            knowledge_structure=make_structure(["root"]), session_id="ghost"
        )
        await adapter.apply_remote_session(remote)

        stored = await runtime.storage.load_session("ghost")
        assert stored is not None

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
        assert received[0].session_id == session_id
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
        assert received[0].session_id == session_id
        assert received[0].conflicts == ["root"]


# ---------------------------------------------------------------------------
# Conflict materialization: source_session_id + dedup across retries
# ---------------------------------------------------------------------------


class TestGossipConflictMaterialization:
    """
    ADR-008 status update: a gossip merge conflict used to escalate
    with only a bare list of conflicting object ids -- a subscriber
    had no way to see what the remote side actually contained, since
    the only copy of it was a local variable discarded the instant
    ``GossipConflictDetected`` was published. These tests pin the fix:
    the remote content is registered as a real local branch
    (``source_session_id``), and a gossip round that keeps re-sending
    the same unresolved conflict does not leak a fresh branch or
    re-publish the event every time (``_pending_conflict_vectors``).
    """

    async def test_conflict_materializes_remote_content_as_a_branch(self):
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
        source_session_id = received[0].source_session_id
        assert source_session_id  # non-empty: registration succeeded

        source = runtime_a.get_session(source_session_id)
        assert source is not None
        assert source.parent_session_id == session_id
        assert source.knowledge_structure == session_b.knowledge_structure

    async def test_repeated_gossip_round_with_same_conflict_is_not_rematerialized(self):
        """
        A gossip cycle keeps retrying every tracked session on a fixed
        interval regardless of whether the last attempt conflicted, so
        the same unresolved remote content arrives again on the very
        next round. That must not leak a second branch or re-publish a
        second event for content a subscriber has already been told
        about.
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

        result_1 = await adapter_a.apply_remote_session(session_b)
        result_2 = await adapter_a.apply_remote_session(session_b)

        assert result_1 is False
        assert result_2 is False
        # Only the first round registered a branch and published.
        assert len(received) == 1
        first_source_session_id = received[0].source_session_id

        # No second RuntimeSession leaked into the registry beyond the
        # one branch from the first round.
        sessions_seen = {
            s.session_id
            for s in runtime_a.list_sessions()
            if s.parent_session_id == session_id
        }
        assert sessions_seen == {first_source_session_id}

    async def test_new_divergence_after_a_resolved_conflict_is_rematerialized(self):
        """
        Once a session_id's conflict is actually resolved (any of the
        no-op/fast-forward/merge success paths), a *new* conflict on
        the same session_id afterwards must register and publish
        fresh -- the dedup guard is per unresolved conflict, not
        permanent.
        """
        runtime_a, runtime_b, session_id = await _paired_replicas()
        session_a = runtime_a.get_session(session_id)
        session_b = runtime_b.get_session(session_id)

        await _evolve(runtime_a, session_a, [_add("a")])
        await _evolve(runtime_b, session_b, [_add("b")])

        received: list[GossipConflictDetected] = []
        runtime_a.events.subscribe(GossipConflictDetected, received.append)

        adapter_a = GossipAdapter(runtime_a, "replica-a")

        # First divergence: no common ancestor, escalated.
        assert await adapter_a.apply_remote_session(session_b) is False
        assert len(received) == 1

        # Resolve it locally by fast-forwarding straight to B's state
        # (simulating however the conflict eventually got resolved),
        # then converge A's vector so the next round sees a genuine
        # no-op instead of another escalation.
        session_a.knowledge_structure = copy.deepcopy(session_b.knowledge_structure)
        VersionVector.from_metadata(session_b.metadata).to_metadata(session_a.metadata)
        tx = runtime_a.begin_transaction(session_a)
        await runtime_a.commit_transaction(tx)

        assert await adapter_a.apply_remote_session(session_b) is True
        assert len(received) == 1  # still just the first conflict

        # Now a brand-new divergence on the same session_id.
        await _evolve(runtime_a, session_a, [_add("c")])
        await _evolve(runtime_b, session_b, [_add("d")])

        assert await adapter_a.apply_remote_session(session_b) is False
        assert len(received) == 2
        assert received[1].source_session_id
        assert received[1].source_session_id != received[0].source_session_id

    async def test_registration_failure_still_escalates_with_empty_source_session_id(
        self,
    ):
        """
        Defensive path: if register_foreign_branch itself fails (e.g. a
        storage hiccup), the conflict must still be escalated -- an
        empty source_session_id ("no diff available") beats losing the
        escalation entirely.
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
        with mock.patch.object(
            type(runtime_a),
            "register_foreign_branch",
            side_effect=RuntimeError("storage hiccup"),
        ):
            result = await adapter_a.apply_remote_session(session_b)

        assert result is False
        assert len(received) == 1
        assert received[0].source_session_id == ""
        assert received[0].conflicts == ["root"]


# ---------------------------------------------------------------------------
# Concurrent apply_remote_session (per-session_id locking)
# ---------------------------------------------------------------------------


class TestApplyRemoteSessionSerialization:
    """
    Regression coverage for the race apply_remote_session had before
    GossipAdapter serialized it per session_id: two inbound gossip
    requests for the same session_id, arriving concurrently, could
    both pass TransactionManager.begin's "no active transaction yet"
    check before either committed -- the second raising
    RuntimeError("Session already has an active transaction."). See
    GossipAdapter._lock_for's docstring for the full explanation.
    """

    async def test_lock_for_is_per_session_not_global(self):
        runtime = await Runtime.create(core=CksCoreAdapter())
        adapter = GossipAdapter(runtime, "r1")

        lock_a1 = adapter._lock_for("session-a")
        lock_a2 = adapter._lock_for("session-a")
        lock_b = adapter._lock_for("session-b")

        assert lock_a1 is lock_a2
        assert lock_a1 is not lock_b

    async def test_unlocked_body_can_raise_when_run_concurrently(self):
        """
        Confirms the hazard is real, not hypothetical: calling
        ``_apply_remote_session_locked`` (the reconciliation body,
        bypassing ``apply_remote_session``'s new lock) twice
        concurrently for one session_id can still raise
        "Session already has an active transaction." This is what the
        paired test below (going through the public,
        now-lock-guarded ``apply_remote_session``) proves no longer
        happens.

        Two *different* remote snapshots are used -- one from replica
        B (fast-forward source), one from a from-scratch replica C
        anchored to ``EMPTY_STATE_VERSION_ID`` (merge-probe source) --
        because a single snapshot applied twice would let the second
        call's own vector comparison see the first call's
        already-mutated local metadata and short-circuit as a no-op
        before ever reaching ``begin_transaction``; that would prove
        nothing about the race. ``commit_transaction`` is patched
        with a real (if short) delay so the first call's
        begin/commit window stays open long enough for the second
        call's ``begin_transaction`` to land inside it, instead of
        relying on incidental event-loop scheduling.
        """
        runtime_a, runtime_b, session_id = await _paired_replicas()
        session_a = runtime_a.get_session(session_id)
        session_b = runtime_b.get_session(session_id)
        await _evolve(runtime_b, session_b, [_add("from-b")])

        runtime_c = await Runtime.create(core=CksCoreAdapter())
        session_c = RuntimeSession(
            knowledge_structure=copy.deepcopy(session_a.knowledge_structure),
            session_id=session_id,
            parent_version_id=EMPTY_STATE_VERSION_ID,
        )
        session_c.metadata["node_id"] = str(uuid4())
        runtime_c._sessions.restore(session_c)
        await runtime_c.storage.save_session(session_c)
        await _evolve(runtime_c, session_c, [_add("from-c")])

        adapter_a = GossipAdapter(runtime_a, "replica-a")

        # Runtime uses __slots__, so an instance can't be monkeypatched
        # directly -- patch the class method instead (affects only
        # this Runtime instance's *behavior* for the duration of the
        # context manager, same net effect).
        original_commit = Runtime.commit_transaction

        async def slow_commit(self, transaction):
            await asyncio.sleep(0.05)
            return await original_commit(self, transaction)

        with mock.patch.object(Runtime, "commit_transaction", slow_commit):
            results = await asyncio.gather(
                adapter_a._apply_remote_session_locked(session_b),
                adapter_a._apply_remote_session_locked(session_c),
                return_exceptions=True,
            )

        assert any(isinstance(r, RuntimeError) for r in results), results

    async def test_locked_public_method_does_not_raise_for_the_same_race(self):
        """
        Same setup as the previous test, but through the public,
        lock-guarded ``apply_remote_session`` instead of the raw
        body: the second call now waits for the lock instead of
        racing the first one's open transaction, so both calls
        succeed and neither raises.
        """
        runtime_a, runtime_b, session_id = await _paired_replicas()
        session_a = runtime_a.get_session(session_id)
        session_b = runtime_b.get_session(session_id)
        await _evolve(runtime_b, session_b, [_add("from-b")])

        runtime_c = await Runtime.create(core=CksCoreAdapter())
        session_c = RuntimeSession(
            knowledge_structure=copy.deepcopy(session_a.knowledge_structure),
            session_id=session_id,
            parent_version_id=EMPTY_STATE_VERSION_ID,
        )
        session_c.metadata["node_id"] = str(uuid4())
        runtime_c._sessions.restore(session_c)
        await runtime_c.storage.save_session(session_c)
        await _evolve(runtime_c, session_c, [_add("from-c")])

        adapter_a = GossipAdapter(runtime_a, "replica-a")

        original_commit = Runtime.commit_transaction

        async def slow_commit(self, transaction):
            await asyncio.sleep(0.05)
            return await original_commit(self, transaction)

        with mock.patch.object(Runtime, "commit_transaction", slow_commit):
            results = await asyncio.gather(
                adapter_a.apply_remote_session(session_b),
                adapter_a.apply_remote_session(session_c),
            )

        assert results == [True, True]
        ids = {o.identity.id for o in session_a.knowledge_structure.objects}
        assert ids == {"root", "from-b", "from-c"}

    async def test_concurrent_calls_for_different_sessions_are_not_serialized(self):
        """
        The other half of the guarantee: locking is per session_id,
        not one lock across the whole adapter -- two different
        sessions must still be able to be inside the reconciliation
        body at the same time. Would time out if a single adapter-wide
        lock serialized unrelated sessions against each other.
        """
        runtime = await Runtime.create(core=CksCoreAdapter())
        adapter = GossipAdapter(runtime, "r1")

        both_inside = asyncio.Event()
        active: set[str] = set()
        original = adapter._apply_remote_session_locked

        async def tracked(remote_session):
            sid = remote_session.session_id
            active.add(sid)
            if len(active) == 2:
                both_inside.set()
            await asyncio.wait_for(both_inside.wait(), timeout=1)
            try:
                return await original(remote_session)
            finally:
                active.discard(sid)

        adapter._apply_remote_session_locked = tracked

        remote_1 = RuntimeSession(
            knowledge_structure=make_structure(["root"]), session_id="session-1"
        )
        remote_2 = RuntimeSession(
            knowledge_structure=make_structure(["root"]), session_id="session-2"
        )

        results = await asyncio.gather(
            adapter.apply_remote_session(remote_1),
            adapter.apply_remote_session(remote_2),
        )

        assert results == [True, True]


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

class TestThreeReplicaConvergenceViaGenesis:
    """
    End-to-end reproduction of the exact scenario that used to hang
    forever with "could not determine a merge base": three replicas
    (Supervisor/Critic/Worker), one true origin, two gossip-only
    joiners, concurrent field-disjoint edits on two of them after
    everyone has bootstrapped.
    """

    async def test_supervisor_critic_worker_converge_after_concurrent_edits(self):
        runtime_sup = await Runtime.create(core=CksCoreAdapter())
        runtime_critic = await Runtime.create(core=CksCoreAdapter())
        runtime_worker = await Runtime.create(core=CksCoreAdapter())

        # Supervisor is the true origin: created locally, not received
        # via gossip, so it's the one call site that must explicitly
        # anchor_genesis() (see GossipAdapter.anchor_genesis docstring).
        sup_session = await runtime_sup.create_session(make_structure(["root"]))
        GossipAdapter.anchor_genesis(sup_session)

        adapter_sup = GossipAdapter(runtime_sup, "replica-supervisor")
        adapter_critic = GossipAdapter(runtime_critic, "replica-critic")
        adapter_worker = GossipAdapter(runtime_worker, "replica-worker")

        # First contact: Critic and Worker have never seen this
        # session_id, so this exercises the real
        # _bootstrap_remote_session path, not the paired-replica test
        # shortcut.
        assert runtime_critic.get_session(sup_session.session_id) is None
        assert runtime_worker.get_session(sup_session.session_id) is None
        await adapter_critic.apply_remote_session(sup_session)
        await adapter_worker.apply_remote_session(sup_session)

        critic_session = runtime_critic.get_session(sup_session.session_id)
        worker_session = runtime_worker.get_session(sup_session.session_id)
        assert critic_session is not None
        assert worker_session is not None

        # Now Supervisor and Worker independently commit field-disjoint
        # changes, with no further contact before Critic gets involved
        # -- the concurrent-edit case the earlier ad hoc reproduction
        # (three manually-seeded sessions, no real bootstrap) could
        # never converge.
        await _evolve(runtime_sup, sup_session, [_add("from-supervisor")])
        await _evolve(runtime_worker, worker_session, [_add("from-worker")])

        received_conflicts: list[GossipConflictDetected] = []
        for rt in (runtime_sup, runtime_critic, runtime_worker):
            rt.events.subscribe(GossipConflictDetected, received_conflicts.append)

        # A handful of pairwise rounds, full mesh -- mirroring what
        # PeerScheduler would do over several intervals, just without
        # the real transport or the randomness.
        pairs = [
            (adapter_sup, sup_session.session_id, adapter_critic),
            (adapter_sup, sup_session.session_id, adapter_worker),
            (adapter_critic, sup_session.session_id, adapter_worker),
        ]
        for _round in range(3):
            for a, sid, b in pairs:
                await gossip_exchange(sid, a, b)

        assert received_conflicts == []

        expected = {"root", "from-supervisor", "from-worker"}
        for rt in (runtime_sup, runtime_critic, runtime_worker):
            session = rt.get_session(sup_session.session_id)
            ids = {o.identity.id for o in session.knowledge_structure.objects}
            assert ids == expected, f"{rt} has {ids}"


# ---------------------------------------------------------------------------
# Duplicate replica_id (audit finding #2): two physically distinct
# replicas sharing one replica_id -- most commonly two clones of a
# deployment template/image, each carrying the same baked-in identity
# instead of generating its own via storage.get_or_create_replica_id().
#
# Unlike every other reconciliation test above, this deliberately
# constructs both replicas with SQLiteStorage rather than the default
# in-memory backend: VersionVector only ever gets a `replica_id` entry
# via ExecutionPipeline._persist's `replica_id=self._runtime.replica_id`
# -- and `Runtime.replica_id` for the default in-memory backend is
# always None (InMemoryStorage has no durable identity to report), so
# the guard under test (keyed on that exact clock entry) would never
# see anything to compare against without a backend that actually
# reports one.
# ---------------------------------------------------------------------------


class TestDuplicateReplicaIdGuard:
    @staticmethod
    async def _shared_id_replica(
        tmp_path,
        db_name: str,
        replica_id: str,
    ) -> tuple[Runtime, GossipAdapter]:
        from cks_runtime.storage.sqlite_storage import SQLiteStorage

        storage = SQLiteStorage(str(tmp_path / db_name))
        # Force both replicas' durable identity row to the same value
        # -- simulates a cloned template rather than each installation
        # generating its own via get_or_create_replica_id().
        storage._conn.execute("DELETE FROM cks_runtime_identity")
        storage._conn.execute(
            "INSERT INTO cks_runtime_identity (id, replica_id) VALUES (1, ?)",
            (replica_id,),
        )
        storage._conn.commit()

        runtime = await Runtime.create(core=CksCoreAdapter(), storage=storage)
        assert runtime.replica_id == replica_id
        adapter = GossipAdapter(runtime, runtime.replica_id)
        return runtime, adapter

    async def test_higher_remote_clock_under_own_key_is_detected(self, tmp_path):
        """
        The straightforward case: remote's vector shows a higher clock
        under our own replica_id than we ourselves have ever reached
        -- only possible if some other process committed under our
        identity, since our own clock only ever advances via our own
        commits.
        """
        shared_id = "dup-replica-higher"
        runtime_a, _adapter_a = await self._shared_id_replica(tmp_path, "a.db", shared_id)
        runtime_b, adapter_b = await self._shared_id_replica(tmp_path, "b.db", shared_id)

        structure = make_structure(["root"])
        session_a = await runtime_a.create_session(structure)
        session_id = session_a.session_id
        GossipAdapter.anchor_genesis(session_a)

        session_b = RuntimeSession(knowledge_structure=structure, session_id=session_id)
        session_b.metadata["node_id"] = str(uuid4())
        runtime_b._sessions.restore(session_b)
        await runtime_b.storage.save_session(session_b)
        GossipAdapter.anchor_genesis(session_b)

        # A commits twice (higher own-key clock), B commits zero times
        # beyond genesis -- A's snapshot, applied to B, should trip
        # the guard rather than fast-forward B onto it.
        await _evolve(runtime_a, session_a, [_add("from-a-1")])
        await _evolve(runtime_a, session_a, [_add("from-a-2")])

        received: list[DuplicateReplicaIdDetected] = []
        runtime_b.events.subscribe(DuplicateReplicaIdDetected, received.append)

        result = await adapter_b.apply_remote_session(session_a)

        assert result is False
        assert len(received) == 1
        assert received[0].session_id == session_id
        assert received[0].own_replica_id == shared_id
        assert received[0].remote_clock > received[0].local_clock

        # Refused entirely -- B's own content is untouched.
        assert {o.identity.id for o in session_b.knowledge_structure.objects} == {"root"}

    async def test_equal_clock_different_content_is_detected(self, tmp_path):
        """
        The subtler case this guard specifically had to be widened
        for: two colliding replicas that each make exactly the same
        number of commits since a shared genesis land on an *equal*
        clock under the shared key, with genuinely different content.
        A strict `remote > local` check alone misses this -- see
        adapter.py's own comment on why equal-and-different is just as
        conclusive as higher-and-newer.
        """
        shared_id = "dup-replica-equal"
        runtime_a, adapter_a = await self._shared_id_replica(tmp_path, "a.db", shared_id)
        runtime_b, adapter_b = await self._shared_id_replica(tmp_path, "b.db", shared_id)

        structure = make_structure(["root"])
        session_a = await runtime_a.create_session(structure)
        session_id = session_a.session_id
        GossipAdapter.anchor_genesis(session_a)

        session_b = RuntimeSession(knowledge_structure=structure, session_id=session_id)
        session_b.metadata["node_id"] = str(uuid4())
        runtime_b._sessions.restore(session_b)
        await runtime_b.storage.save_session(session_b)
        GossipAdapter.anchor_genesis(session_b)

        # Exactly one commit each -- same resulting clock under the
        # shared key, different content.
        await _evolve(runtime_a, session_a, [_add("from-a")])
        await _evolve(runtime_b, session_b, [_add("from-b")])

        received_a: list[DuplicateReplicaIdDetected] = []
        received_b: list[DuplicateReplicaIdDetected] = []
        runtime_a.events.subscribe(DuplicateReplicaIdDetected, received_a.append)
        runtime_b.events.subscribe(DuplicateReplicaIdDetected, received_b.append)

        result_b = await adapter_b.apply_remote_session(session_a)
        result_a = await adapter_a.apply_remote_session(session_b)

        assert result_a is False
        assert result_b is False
        assert len(received_a) == 1
        assert len(received_b) == 1
        assert received_a[0].local_clock == received_a[0].remote_clock
        assert received_b[0].local_clock == received_b[0].remote_clock

        # Neither side's content changed -- no silent, asymmetric
        # divergence (the exact failure mode from the original audit
        # repro).
        assert {o.identity.id for o in session_a.knowledge_structure.objects} == {
            "root",
            "from-a",
        }
        assert {o.identity.id for o in session_b.knowledge_structure.objects} == {
            "root",
            "from-b",
        }

    async def test_repeated_rounds_do_not_re_publish_the_same_collision(self, tmp_path):
        """
        Mirrors TestGossipConflictMaterialization's dedup test for
        GossipConflictDetected: a duplicate-id collision does not
        resolve itself, so a background gossip loop retrying the same
        exchange every interval must not re-publish the event every
        single round forever.
        """
        shared_id = "dup-replica-dedup"
        runtime_a, _adapter_a = await self._shared_id_replica(tmp_path, "a.db", shared_id)
        runtime_b, adapter_b = await self._shared_id_replica(tmp_path, "b.db", shared_id)

        structure = make_structure(["root"])
        session_a = await runtime_a.create_session(structure)
        session_id = session_a.session_id
        GossipAdapter.anchor_genesis(session_a)

        session_b = RuntimeSession(knowledge_structure=structure, session_id=session_id)
        session_b.metadata["node_id"] = str(uuid4())
        runtime_b._sessions.restore(session_b)
        await runtime_b.storage.save_session(session_b)
        GossipAdapter.anchor_genesis(session_b)

        await _evolve(runtime_a, session_a, [_add("from-a-1")])
        await _evolve(runtime_a, session_a, [_add("from-a-2")])

        received: list[DuplicateReplicaIdDetected] = []
        runtime_b.events.subscribe(DuplicateReplicaIdDetected, received.append)

        for _ in range(5):
            result = await adapter_b.apply_remote_session(session_a)
            assert result is False

        assert len(received) == 1

        # A further commit on A advances remote_clock past what was
        # already reported -- that's a materially new escalation
        # (still unresolved, but the operator's earlier report is now
        # stale), so it must be reported again, not swallowed by the
        # same dedup entry.
        await _evolve(runtime_a, session_a, [_add("from-a-3")])
        result = await adapter_b.apply_remote_session(session_a)
        assert result is False
        assert len(received) == 2
        assert received[1].remote_clock > received[0].remote_clock