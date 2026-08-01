"""
Unit tests for the peer-discovery protocol (ADR-008, ``discovery.py``):
``merge_discovered_peers`` against a real ``PeerScheduler``, plus a
fake ``PeerDiscovery`` implementation exercising the abstract
protocol's contract. The HTTP wire implementation
(``HTTPPeerDiscovery`` / ``GossipServer``'s ``/gossip/peers`` route)
is covered separately in ``test_http_transport.py``, against real
aiohttp servers.
"""

from __future__ import annotations

import pytest

from cks_runtime.gossip.discovery import (
    PeerDiscovery,
    PeerDiscoveryError,
    merge_discovered_peers,
)
from cks_runtime.gossip.scheduling import PeerScheduler


class FakePeerDiscovery(PeerDiscovery):
    """Returns a fixed peer list, or raises, per test setup."""

    def __init__(self, peers: list[str] | None = None, *, error: Exception | None = None) -> None:
        self._peers = peers if peers is not None else []
        self._error = error
        self.calls: list[str] = []

    async def fetch_peers(self, peer: str) -> list[str]:
        self.calls.append(peer)
        if self._error is not None:
            raise self._error
        return self._peers


class TestPeerDiscoveryProtocol:
    @pytest.mark.asyncio
    async def test_fake_implementation_satisfies_the_protocol(self):
        discovery = FakePeerDiscovery(["http://b", "http://c"])
        result = await discovery.fetch_peers("http://a")
        assert result == ["http://b", "http://c"]
        assert discovery.calls == ["http://a"]

    @pytest.mark.asyncio
    async def test_fake_implementation_can_raise_peer_discovery_error(self):
        discovery = FakePeerDiscovery(error=PeerDiscoveryError("unreachable"))
        with pytest.raises(PeerDiscoveryError):
            await discovery.fetch_peers("http://a")

    def test_cannot_instantiate_the_abstract_base_directly(self):
        with pytest.raises(TypeError):
            PeerDiscovery()  # type: ignore[abstract]


class TestMergeDiscoveredPeers:
    def test_adds_new_peers_to_the_scheduler(self):
        scheduler = PeerScheduler(["http://a"])
        newly_added = merge_discovered_peers(scheduler, ["http://a", "http://b", "http://c"])

        assert set(scheduler.peers) == {"http://a", "http://b", "http://c"}
        assert set(newly_added) == {"http://b", "http://c"}

    def test_returns_empty_list_when_nothing_is_new(self):
        scheduler = PeerScheduler(["http://a", "http://b"])
        newly_added = merge_discovered_peers(scheduler, ["http://a", "http://b"])

        assert newly_added == []
        assert set(scheduler.peers) == {"http://a", "http://b"}

    def test_skips_self_address(self):
        scheduler = PeerScheduler(["http://a"])
        newly_added = merge_discovered_peers(
            scheduler, ["http://a", "http://self", "http://b"], self_address="http://self"
        )

        assert "http://self" not in scheduler.peers
        assert set(newly_added) == {"http://b"}

    def test_newly_added_peers_start_with_fresh_stats(self):
        scheduler = PeerScheduler(["http://a"])
        scheduler.record_failure("http://a")
        merge_discovered_peers(scheduler, ["http://b"])

        stats = scheduler.stats_for("http://b")
        assert stats.successes == 0
        assert stats.failures == 0
        assert stats.backoff_until_ms == 0

    def test_is_idempotent_across_repeated_merges(self):
        scheduler = PeerScheduler([])
        first = merge_discovered_peers(scheduler, ["http://a", "http://b"])
        second = merge_discovered_peers(scheduler, ["http://a", "http://b"])

        assert set(first) == {"http://a", "http://b"}
        assert second == []

    def test_empty_discovered_list_is_a_no_op(self):
        scheduler = PeerScheduler(["http://a"])
        newly_added = merge_discovered_peers(scheduler, [])

        assert newly_added == []
        assert set(scheduler.peers) == {"http://a"}