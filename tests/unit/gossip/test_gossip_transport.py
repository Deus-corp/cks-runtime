"""
Unit tests for ``gossip_exchange_over_transport`` (ADR-008), against a
fake in-memory ``GossipTransport`` -- exercises the orchestration logic
(envelope build/verify/filter/apply) without any real network stack.
``test_http_transport.py`` covers the real aiohttp implementation.
"""

from __future__ import annotations

import time
from uuid import uuid4

import cks
import pytest

from cks_runtime.gossip.adapter import GossipAdapter
from cks_runtime.gossip.envelope import GossipEnvelope
from cks_runtime.gossip.filter import GossipFilter
from cks_runtime.gossip.transport import (
    GossipTransport,
    GossipTransportError,
    gossip_exchange_over_transport,
)
from cks_runtime.operations.operation_types import EvolveOperation
from cks_runtime.runtime import Runtime
from cks_runtime.session.session import RuntimeSession
from cks_runtime_plugins.cks_core import CksCoreAdapter

pytestmark = pytest.mark.asyncio

SECRET = b"shared-test-secret"


def make_structure(ids: list[str]) -> cks.KnowledgeStructure:
    objects = [
        cks.KnowledgeObject(cks.ObjectIdentity(id=i, type="Thing", name=i)) for i in ids
    ]
    return cks.KnowledgeStructure(objects)


def _add(obj_id: str) -> cks.evolution.AddObject:
    return cks.evolution.AddObject(
        cks.KnowledgeObject(cks.ObjectIdentity(id=obj_id, type="Thing", name=obj_id))
    )


async def _evolve(runtime: Runtime, session: RuntimeSession, operations: list) -> None:
    tx = runtime.begin_transaction(session)
    tx.add_operation(
        EvolveOperation("evolve", knowledge_structure=session.knowledge_structure, evolution=operations)
    )
    await runtime.commit_transaction(tx)


async def _paired_replicas() -> tuple[Runtime, Runtime, str]:
    runtime_a = await Runtime.create(core=CksCoreAdapter())
    runtime_b = await Runtime.create(core=CksCoreAdapter())

    session_a = await runtime_a.create_session(make_structure(["root"]))

    session_b = RuntimeSession(
        knowledge_structure=make_structure(["root"]),
        session_id=session_a.session_id,
    )
    session_b.metadata["node_id"] = str(uuid4())
    runtime_b._sessions.restore(session_b)
    await runtime_b.storage.save_session(session_b)

    return runtime_a, runtime_b, session_a.session_id


async def _paired_replicas_with_shared_base() -> tuple[Runtime, Runtime, str]:
    """
    Like ``_paired_replicas``, but both sides additionally carry the
    *same* genesis ``RuntimeVersion`` in their own local
    ``version_history``, with ``parent_version_id`` on both sessions
    pointing at it.

    ``MergeOperation`` resolves its base by looking up
    ``source_session.parent_version_id`` inside the *receiving*
    side's own ``version_history`` (see
    ``cks_runtime.operations.operation_types.MergeOperation.execute``)
    -- so a one-sided fork record (as in
    ``test_gossip_adapter._paired_replicas_with_shared_base``, where
    only the branch's *parent* ever committed the fork point) only
    lets a merge resolve in *one* direction. A full
    ``gossip_exchange_over_transport`` round trip needs both
    directions to resolve (A's snapshot merges into B, then B's reply
    merges into A), so both sides need the shared version recorded
    locally. Real gossip would produce exactly this after a session
    bootstrap (``GossipAdapter.apply_remote_session`` adopting a
    session neither side had merge history for yet); this constructs
    that end state directly for a test that wants to start from
    "already synced once."
    """
    from cks_runtime.versioning.version import RuntimeVersion

    runtime_a = await Runtime.create(core=CksCoreAdapter())
    runtime_b = await Runtime.create(core=CksCoreAdapter())

    session_a = await runtime_a.create_session(make_structure(["root"]))

    genesis = RuntimeVersion(
        session_id=session_a.session_id,
        transaction_id="genesis",
        knowledge_structure=make_structure(["root"]),
        metadata={},
    )
    session_a.version_history.append(genesis)
    session_a.parent_version_id = genesis.version_id
    await runtime_a.storage.save_session(session_a)

    session_b = RuntimeSession(
        knowledge_structure=make_structure(["root"]),
        session_id=session_a.session_id,
        parent_version_id=genesis.version_id,
    )
    session_b.metadata["node_id"] = str(uuid4())
    session_b.version_history.append(genesis)
    runtime_b._sessions.restore(session_b)
    await runtime_b.storage.save_session(session_b)

    return runtime_a, runtime_b, session_a.session_id


class FakeTransport(GossipTransport):
    """
    Routes ``exchange`` directly to a peer replica's own
    ``GossipServer``-equivalent handling, entirely in-process --
    verifies + filters + applies via the peer's own adapter, then
    replies with the peer's own signed envelope. Deliberately
    reimplements (in miniature) what ``GossipServer._handle_gossip``
    does, rather than importing it, so this test doesn't depend on
    ``http_transport``'s aiohttp requirement at all.
    """

    def __init__(self, peer_adapters: dict[str, GossipAdapter], secret: bytes) -> None:
        self._peer_adapters = peer_adapters
        self._secret = secret
        self._seq_no = 0
        self.calls: list[tuple[str, str]] = []
        self.fail_for_peer: str | None = None

    def _next_seq_no(self) -> int:
        self._seq_no += 1
        return self._seq_no

    async def exchange(self, peer: str, envelope: GossipEnvelope) -> GossipEnvelope | None:
        self.calls.append((peer, envelope.session_id))
        if peer == self.fail_for_peer:
            raise GossipTransportError(f"simulated failure reaching {peer}")

        adapter = self._peer_adapters.get(peer)
        if adapter is None:
            raise GossipTransportError(f"unknown peer {peer!r}")

        if not envelope.verify(self._secret):
            raise GossipTransportError("signature verification failed")

        await adapter.apply_remote_session(envelope.to_session())

        local_session = adapter._runtime.get_session(envelope.session_id)
        if local_session is None:
            return None

        return GossipEnvelope.from_session(
            local_session,
            sender_replica_id=adapter.replica_id,
            seq_no=self._next_seq_no(),
            secret=self._secret,
        )


class TestGossipExchangeOverTransport:
    async def test_returns_false_when_no_local_session(self):
        runtime = await Runtime.create(core=CksCoreAdapter())
        adapter = GossipAdapter(runtime, "replica-a")
        transport = FakeTransport({}, SECRET)

        result = await gossip_exchange_over_transport(
            "no-such-session", adapter, transport, "peer-b", secret=SECRET, seq_no=1
        )
        assert result is False
        assert transport.calls == []

    async def test_converges_two_replicas_that_each_committed(self):
        runtime_a, runtime_b, session_id = await _paired_replicas_with_shared_base()
        session_a = runtime_a.get_session(session_id)
        session_b = runtime_b.get_session(session_id)

        await _evolve(runtime_a, session_a, [_add("a")])
        await _evolve(runtime_b, session_b, [_add("b")])

        adapter_a = GossipAdapter(runtime_a, "replica-a")
        adapter_b = GossipAdapter(runtime_b, "replica-b")
        transport = FakeTransport({"replica-b": adapter_b}, SECRET)

        result = await gossip_exchange_over_transport(
            session_id, adapter_a, transport, "replica-b", secret=SECRET, seq_no=1
        )

        assert result is True
        ids_a = {o.identity.id for o in session_a.knowledge_structure.objects}
        ids_b = {o.identity.id for o in session_b.knowledge_structure.objects}
        assert ids_a == {"root", "a", "b"}
        assert ids_b == {"root", "a", "b"}

    async def test_returns_true_when_peer_has_nothing_new(self):
        runtime_a, runtime_b, session_id = await _paired_replicas()
        adapter_a = GossipAdapter(runtime_a, "replica-a")
        adapter_b = GossipAdapter(runtime_b, "replica-b")
        transport = FakeTransport({"replica-b": adapter_b}, SECRET)

        result = await gossip_exchange_over_transport(
            session_id, adapter_a, transport, "replica-b", secret=SECRET, seq_no=1
        )
        assert result is True

    async def test_propagates_transport_error(self):
        runtime_a, _runtime_b, session_id = await _paired_replicas()
        adapter_a = GossipAdapter(runtime_a, "replica-a")
        transport = FakeTransport({}, SECRET)
        transport.fail_for_peer = "replica-b"

        with pytest.raises(GossipTransportError):
            await gossip_exchange_over_transport(
                session_id, adapter_a, transport, "replica-b", secret=SECRET, seq_no=1
            )

    async def test_rejects_reply_signed_with_wrong_secret(self):
        runtime_a, runtime_b, session_id = await _paired_replicas()
        adapter_a = GossipAdapter(runtime_a, "replica-a")
        adapter_b = GossipAdapter(runtime_b, "replica-b")
        await _evolve(runtime_b, runtime_b.get_session(session_id), [_add("b")])

        # Peer signs its reply with a different secret than the one
        # the caller verifies against.
        transport = FakeTransport({"replica-b": adapter_b}, secret=b"wrong-secret")

        with pytest.raises(GossipTransportError):
            # FakeTransport itself raises when verifying the outgoing
            # envelope against its own (wrong) secret, which is a
            # reasonable stand-in for "peer rejected our signature" --
            # the interesting assertion is in the next test, where the
            # peer accepts our envelope but signs its own reply with a
            # secret our side won't accept.
            await gossip_exchange_over_transport(
                session_id, adapter_a, transport, "replica-b", secret=SECRET, seq_no=1
            )

    async def test_rejects_reply_when_caller_secret_mismatches_peer_signing_secret(self):
        runtime_a, runtime_b, session_id = await _paired_replicas()
        adapter_a = GossipAdapter(runtime_a, "replica-a")
        adapter_b = GossipAdapter(runtime_b, "replica-b")
        await _evolve(runtime_b, runtime_b.get_session(session_id), [_add("b")])

        class WrongSigningTransport(GossipTransport):
            """Verifies incoming envelopes with SECRET but replies signed with a different secret."""

            def __init__(self, adapter: GossipAdapter, reply_secret: bytes) -> None:
                self._adapter = adapter
                self._reply_secret = reply_secret

            async def exchange(self, peer: str, envelope: GossipEnvelope) -> GossipEnvelope | None:
                assert envelope.verify(SECRET)
                await self._adapter.apply_remote_session(envelope.to_session())
                local_session = self._adapter._runtime.get_session(envelope.session_id)
                assert local_session is not None
                return GossipEnvelope.from_session(
                    local_session,
                    sender_replica_id=self._adapter.replica_id,
                    seq_no=1,
                    secret=self._reply_secret,
                )

        transport = WrongSigningTransport(adapter_b, reply_secret=b"a-different-secret")

        result = await gossip_exchange_over_transport(
            session_id, adapter_a, transport, "replica-b", secret=SECRET, seq_no=1
        )
        assert result is False
        # Local state unaffected -- the unverifiable reply was never applied.
        ids_a = {o.identity.id for o in runtime_a.get_session(session_id).knowledge_structure.objects}
        assert ids_a == {"root"}

    async def test_replay_filter_rejects_a_resent_reply(self):
        runtime_a, runtime_b, session_id = await _paired_replicas()
        adapter_a = GossipAdapter(runtime_a, "replica-a")
        await _evolve(runtime_b, runtime_b.get_session(session_id), [_add("b")])

        fixed_envelope = GossipEnvelope.from_session(
            runtime_b.get_session(session_id),
            sender_replica_id="replica-b",
            seq_no=1,
            secret=SECRET,
            nonce="fixed-nonce",
            # A fixed-but-*current* timestamp: this test is about
            # nonce/seq replay protection, not clock-skew rejection,
            # so it must stay within GossipFilter's default skew
            # tolerance no matter when the suite actually runs --
            # a hardcoded past literal would eventually (and did)
            # start failing here instead of at the replay check this
            # test is actually for.
            timestamp_ms=int(time.time() * 1000),
        )

        class ReplayingTransport(GossipTransport):
            async def exchange(self, peer: str, envelope: GossipEnvelope) -> GossipEnvelope | None:
                return fixed_envelope

        transport = ReplayingTransport()
        gossip_filter = GossipFilter()

        first = await gossip_exchange_over_transport(
            session_id,
            adapter_a,
            transport,
            "replica-b",
            secret=SECRET,
            seq_no=1,
            gossip_filter=gossip_filter,
        )
        assert first is True

        # Same envelope (same nonce/seq) resent -- the filter must
        # reject it as a replay on the second attempt.
        second = await gossip_exchange_over_transport(
            session_id,
            adapter_a,
            transport,
            "replica-b",
            secret=SECRET,
            seq_no=2,
            gossip_filter=gossip_filter,
        )
        assert second is False