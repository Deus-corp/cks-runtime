"""
Unit tests for ``GossipService`` (ADR-008) -- previously untested as
its own unit, only exercised end-to-end by ``test_http_transport.py``.
Uses in-process fakes for both ``GossipTransport`` and
``PeerDiscovery`` (mirroring ``test_gossip_transport.py``'s
``FakeTransport`` / ``test_gossip_discovery.py``'s
``FakePeerDiscovery``) so these tests exercise ``GossipService``'s
own orchestration logic -- peer choice, session iteration, success/
failure recording, and now startup discovery -- without any real
network stack.
"""

from __future__ import annotations

import cks
import pytest

from cks_runtime.gossip.adapter import GossipAdapter
from cks_runtime.gossip.discovery import PeerDiscovery, PeerDiscoveryError
from cks_runtime.gossip.envelope import GossipEnvelope
from cks_runtime.gossip.scheduling import PeerScheduler
from cks_runtime.gossip.service import GossipService
from cks_runtime.gossip.transport import GossipTransport, GossipTransportError
from cks_runtime.operations.operation_types import EvolveOperation
from cks_runtime.runtime import Runtime
from cks_runtime.session.session import RuntimeSession
from cks_runtime_plugins.cks_core import CksCoreAdapter

pytestmark = pytest.mark.asyncio

SECRET = b"gossip-service-test-secret"


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
        EvolveOperation(
            "evolve", knowledge_structure=session.knowledge_structure, evolution=operations
        )
    )
    await runtime.commit_transaction(tx)


class FakeTransport(GossipTransport):
    """Records every call; replies ``None`` (peer has nothing new) unless told to fail."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_for_peers: set[str] = set()

    async def exchange(self, peer: str, envelope: GossipEnvelope) -> GossipEnvelope | None:
        self.calls.append((peer, envelope.session_id))
        if peer in self.fail_for_peers:
            raise GossipTransportError(f"simulated failure reaching {peer}")
        return None


class FakePeerDiscovery(PeerDiscovery):
    """Returns a canned peer list per address queried, or raises per address."""

    def __init__(
        self,
        answers: dict[str, list[str]] | None = None,
        *,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self._answers = answers if answers is not None else {}
        self._errors = errors if errors is not None else {}
        self.calls: list[str] = []

    async def fetch_peers(self, peer: str) -> list[str]:
        self.calls.append(peer)
        if peer in self._errors:
            raise self._errors[peer]
        return self._answers.get(peer, [])


async def _adapter_with_session() -> tuple[GossipAdapter, str]:
    runtime = await Runtime.create(core=CksCoreAdapter())
    session = await runtime.create_session(make_structure(["root"]))
    adapter = GossipAdapter(runtime, "replica-a")
    return adapter, session.session_id


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    async def test_rejects_non_positive_interval(self):
        adapter, _session_id = await _adapter_with_session()
        with pytest.raises(ValueError):
            GossipService(adapter, FakeTransport(), PeerScheduler([]), secret=SECRET, interval_s=0)

    async def test_starts_not_running(self):
        adapter, _session_id = await _adapter_with_session()
        service = GossipService(adapter, FakeTransport(), PeerScheduler([]), secret=SECRET)
        assert service.running is False

    async def test_tracked_sessions_defaults_empty(self):
        adapter, _session_id = await _adapter_with_session()
        service = GossipService(adapter, FakeTransport(), PeerScheduler([]), secret=SECRET)
        assert service.tracked_sessions == ()

    async def test_tracked_sessions_from_constructor(self):
        adapter, session_id = await _adapter_with_session()
        service = GossipService(
            adapter, FakeTransport(), PeerScheduler([]), secret=SECRET, session_ids=[session_id]
        )
        assert service.tracked_sessions == (session_id,)


class TestTrackUntrackSession:
    async def test_track_session_adds_it(self):
        adapter, session_id = await _adapter_with_session()
        service = GossipService(adapter, FakeTransport(), PeerScheduler([]), secret=SECRET)
        service.track_session(session_id)
        assert service.tracked_sessions == (session_id,)

    async def test_track_session_is_idempotent(self):
        adapter, session_id = await _adapter_with_session()
        service = GossipService(adapter, FakeTransport(), PeerScheduler([]), secret=SECRET)
        service.track_session(session_id)
        service.track_session(session_id)
        assert service.tracked_sessions == (session_id,)

    async def test_untrack_session_removes_it(self):
        adapter, session_id = await _adapter_with_session()
        service = GossipService(
            adapter, FakeTransport(), PeerScheduler([]), secret=SECRET, session_ids=[session_id]
        )
        service.untrack_session(session_id)
        assert service.tracked_sessions == ()

    async def test_untrack_unknown_session_is_a_no_op(self):
        adapter, _session_id = await _adapter_with_session()
        service = GossipService(adapter, FakeTransport(), PeerScheduler([]), secret=SECRET)
        service.untrack_session("no-such-session")  # must not raise
        assert service.tracked_sessions == ()


# ---------------------------------------------------------------------------
# gossip_round
# ---------------------------------------------------------------------------


class TestGossipRound:
    async def test_returns_none_when_no_eligible_peer(self):
        adapter, session_id = await _adapter_with_session()
        service = GossipService(
            adapter, FakeTransport(), PeerScheduler([]), secret=SECRET, session_ids=[session_id]
        )
        result = await service.gossip_round()
        assert result is None

    async def test_gossips_every_tracked_session_with_the_chosen_peer(self):
        adapter, session_id = await _adapter_with_session()
        second_session = await adapter._runtime.create_session(make_structure(["root2"]))
        transport = FakeTransport()
        service = GossipService(
            adapter,
            transport,
            PeerScheduler(["http://peer-b"]),
            secret=SECRET,
            session_ids=[session_id, second_session.session_id],
        )
        result = await service.gossip_round()
        assert result == "http://peer-b"
        assert transport.calls == [
            ("http://peer-b", session_id),
            ("http://peer-b", second_session.session_id),
        ]

    async def test_skips_tracked_sessions_with_no_local_state(self):
        """
        ``gossip_exchange_over_transport`` silently does nothing for a
        tracked session_id this replica has no local copy of (see its
        own docstring) -- confirms ``gossip_round`` doesn't error on
        that, and simply never calls the transport for it.
        """
        adapter, session_id = await _adapter_with_session()
        transport = FakeTransport()
        service = GossipService(
            adapter,
            transport,
            PeerScheduler(["http://peer-b"]),
            secret=SECRET,
            session_ids=[session_id, "session-with-no-local-copy"],
        )
        result = await service.gossip_round()
        assert result == "http://peer-b"
        assert transport.calls == [("http://peer-b", session_id)]

    async def test_records_success_on_clean_round(self):
        adapter, session_id = await _adapter_with_session()
        scheduler = PeerScheduler(["http://peer-b"])
        service = GossipService(
            adapter, FakeTransport(), scheduler, secret=SECRET, session_ids=[session_id]
        )
        await service.gossip_round()
        assert scheduler.stats_for("http://peer-b").successes == 1
        assert scheduler.stats_for("http://peer-b").failures == 0

    async def test_stops_early_and_records_failure_on_transport_error(self):
        adapter, session_id = await _adapter_with_session()
        transport = FakeTransport()
        transport.fail_for_peers.add("http://peer-b")
        scheduler = PeerScheduler(["http://peer-b"])
        service = GossipService(
            adapter,
            transport,
            scheduler,
            secret=SECRET,
            session_ids=[session_id, "second-session"],
        )
        result = await service.gossip_round()

        assert result == "http://peer-b"
        # Failed on the first session -- the second must never be attempted.
        assert transport.calls == [("http://peer-b", session_id)]
        assert scheduler.stats_for("http://peer-b").failures == 1
        assert scheduler.stats_for("http://peer-b").successes == 0

    async def test_asks_the_round_peer_for_its_peers_after_a_clean_round(self):
        adapter, session_id = await _adapter_with_session()
        discovery = FakePeerDiscovery({"http://peer-b": ["http://peer-c"]})
        scheduler = PeerScheduler(["http://peer-b"])
        service = GossipService(
            adapter,
            FakeTransport(),
            scheduler,
            secret=SECRET,
            session_ids=[session_id],
            discovery=discovery,
        )
        await service.gossip_round()

        assert discovery.calls == ["http://peer-b"]
        assert "http://peer-c" in scheduler.peers

    async def test_does_not_ask_for_peers_after_a_failed_round(self):
        adapter, session_id = await _adapter_with_session()
        transport = FakeTransport()
        transport.fail_for_peers.add("http://peer-b")
        discovery = FakePeerDiscovery({"http://peer-b": ["http://peer-c"]})
        scheduler = PeerScheduler(["http://peer-b"])
        service = GossipService(
            adapter,
            transport,
            scheduler,
            secret=SECRET,
            session_ids=[session_id],
            discovery=discovery,
        )
        await service.gossip_round()

        assert discovery.calls == []
        assert "http://peer-c" not in scheduler.peers

    async def test_discovery_failure_does_not_fail_the_round(self):
        adapter, session_id = await _adapter_with_session()
        discovery = FakePeerDiscovery(errors={"http://peer-b": PeerDiscoveryError("down")})
        scheduler = PeerScheduler(["http://peer-b"])
        service = GossipService(
            adapter,
            FakeTransport(),
            scheduler,
            secret=SECRET,
            session_ids=[session_id],
            discovery=discovery,
        )
        result = await service.gossip_round()
        assert result == "http://peer-b"  # round itself still reported as completed


# ---------------------------------------------------------------------------
# discover_peers -- the new "at start" bootstrap discovery
# ---------------------------------------------------------------------------


class TestDiscoverPeers:
    async def test_no_op_without_discovery_configured(self):
        adapter, _session_id = await _adapter_with_session()
        service = GossipService(
            adapter, FakeTransport(), PeerScheduler(["http://peer-b"]), secret=SECRET
        )
        result = await service.discover_peers()
        assert result == []

    async def test_no_op_with_no_known_peers(self):
        adapter, _session_id = await _adapter_with_session()
        discovery = FakePeerDiscovery()
        service = GossipService(
            adapter, FakeTransport(), PeerScheduler([]), secret=SECRET, discovery=discovery
        )
        result = await service.discover_peers()
        assert result == []
        assert discovery.calls == []

    async def test_queries_every_known_seed_peer(self):
        adapter, _session_id = await _adapter_with_session()
        discovery = FakePeerDiscovery(
            {"http://seed-1": ["http://peer-x"], "http://seed-2": ["http://peer-y"]}
        )
        scheduler = PeerScheduler(["http://seed-1", "http://seed-2"])
        service = GossipService(
            adapter, FakeTransport(), scheduler, secret=SECRET, discovery=discovery
        )

        newly_added = await service.discover_peers()

        assert set(discovery.calls) == {"http://seed-1", "http://seed-2"}
        assert set(newly_added) == {"http://peer-x", "http://peer-y"}
        assert set(scheduler.peers) == {
            "http://seed-1",
            "http://seed-2",
            "http://peer-x",
            "http://peer-y",
        }

    async def test_one_seed_failing_does_not_stop_the_others(self):
        adapter, _session_id = await _adapter_with_session()
        discovery = FakePeerDiscovery(
            {"http://seed-2": ["http://peer-y"]},
            errors={"http://seed-1": PeerDiscoveryError("unreachable")},
        )
        scheduler = PeerScheduler(["http://seed-1", "http://seed-2"])
        service = GossipService(
            adapter, FakeTransport(), scheduler, secret=SECRET, discovery=discovery
        )

        newly_added = await service.discover_peers()

        assert set(discovery.calls) == {"http://seed-1", "http://seed-2"}
        assert newly_added == ["http://peer-y"]

    async def test_never_adds_self_address(self):
        adapter, _session_id = await _adapter_with_session()
        discovery = FakePeerDiscovery({"http://seed-1": ["http://self", "http://peer-x"]})
        scheduler = PeerScheduler(["http://seed-1"])
        service = GossipService(
            adapter,
            FakeTransport(),
            scheduler,
            secret=SECRET,
            discovery=discovery,
            self_address="http://self",
        )

        await service.discover_peers()

        assert "http://self" not in scheduler.peers
        assert "http://peer-x" in scheduler.peers

    async def test_does_not_transitively_follow_up_within_one_pass(self):
        """
        A peer discovered via seed-1 is not itself queried in the
        same ``discover_peers`` call -- only the peers ``scheduler``
        already knew about at call time are asked (see the method's
        own docstring on why this is a snapshot, not a BFS).
        """
        adapter, _session_id = await _adapter_with_session()
        discovery = FakePeerDiscovery(
            {
                "http://seed-1": ["http://peer-x"],
                "http://peer-x": ["http://peer-z"],  # would only be reached on a *later* pass
            }
        )
        scheduler = PeerScheduler(["http://seed-1"])
        service = GossipService(
            adapter, FakeTransport(), scheduler, secret=SECRET, discovery=discovery
        )

        await service.discover_peers()

        assert discovery.calls == ["http://seed-1"]
        assert "http://peer-z" not in scheduler.peers


# ---------------------------------------------------------------------------
# start() / stop()
# ---------------------------------------------------------------------------


class TestStart:
    async def test_start_runs_discovery_once_before_the_loop_begins(self):
        adapter, _session_id = await _adapter_with_session()
        discovery = FakePeerDiscovery({"http://seed-1": ["http://peer-x"]})
        scheduler = PeerScheduler(["http://seed-1"])
        service = GossipService(
            adapter,
            FakeTransport(),
            scheduler,
            secret=SECRET,
            discovery=discovery,
            interval_s=999,  # long enough that the background loop won't tick during this test
        )
        try:
            await service.start()
            # discover_peers() is awaited synchronously inside start(),
            # before the background task is even scheduled to run --
            # so its effects must already be visible here.
            assert discovery.calls == ["http://seed-1"]
            assert "http://peer-x" in scheduler.peers
        finally:
            await service.stop()

    async def test_start_without_discovery_configured_does_not_error(self):
        adapter, _session_id = await _adapter_with_session()
        service = GossipService(
            adapter, FakeTransport(), PeerScheduler(["http://seed-1"]), secret=SECRET, interval_s=999
        )
        try:
            await service.start()
            assert service.running is True
        finally:
            await service.stop()

    async def test_second_start_is_a_no_op_and_does_not_repeat_discovery(self):
        adapter, _session_id = await _adapter_with_session()
        discovery = FakePeerDiscovery({"http://seed-1": ["http://peer-x"]})
        scheduler = PeerScheduler(["http://seed-1"])
        service = GossipService(
            adapter,
            FakeTransport(),
            scheduler,
            secret=SECRET,
            discovery=discovery,
            interval_s=999,
        )
        try:
            await service.start()
            await service.start()
            assert discovery.calls == ["http://seed-1"]  # not called twice
        finally:
            await service.stop()

    async def test_stop_then_start_runs_discovery_again(self):
        adapter, _session_id = await _adapter_with_session()
        discovery = FakePeerDiscovery({"http://seed-1": ["http://peer-x"]})
        scheduler = PeerScheduler(["http://seed-1"])
        service = GossipService(
            adapter,
            FakeTransport(),
            scheduler,
            secret=SECRET,
            discovery=discovery,
            interval_s=999,
        )
        await service.start()
        await service.stop()
        await service.start()
        try:
            # The first pass discovers "http://peer-x" and adds it to
            # scheduler, so the second pass's discover_peers() -- which
            # queries every peer scheduler currently knows about, not
            # just the original seeds -- asks both.
            assert discovery.calls == ["http://seed-1", "http://seed-1", "http://peer-x"]
        finally:
            await service.stop()

    async def test_stop_before_start_is_a_no_op(self):
        adapter, _session_id = await _adapter_with_session()
        service = GossipService(adapter, FakeTransport(), PeerScheduler([]), secret=SECRET)
        await service.stop()  # must not raise
        assert service.running is False