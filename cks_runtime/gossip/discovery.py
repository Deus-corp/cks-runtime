"""
``PeerDiscovery`` -- the peer-membership boundary ADR-008 left open
("Peer discovery... currently a static list", per this module's own
follow-up work item).

``PeerScheduler`` (``scheduling.py``) already tracks a *set* of peers
and how healthy each one is, but that set only ever changes through
``add_peer``/``remove_peer`` -- calls a deployment's own configuration
has to make. Nothing lets a replica learn about a peer nobody told it
about directly. This module adds that: a small peer-exchange (PEX)
protocol, the same idea epidemic/gossip membership protocols have used
for decades to grow a full membership view from a handful of seed
addresses -- ask a peer you already know "which other peers do you
know about", and merge the answer into your own set.

Deliberately a **separate exchange** from ``GossipEnvelope``
(``envelope.py``): folding peer lists into the signed session-snapshot
wire format would mean every envelope consumer -- including the
already-tested in-process ``FakeTransport`` in
``test_gossip_transport.py`` -- would need new required fields for
something that has nothing to do with a session snapshot's
authenticity. Keeping ``PeerDiscovery`` a protocol of its own, mirror-
ing ``GossipTransport``'s own mechanism/policy split (``transport.py``),
means the two exchanges can be adopted independently: a deployment can
run signed session gossip with a fixed peer list and never touch this
module at all.

Also deliberately minimal, matching ADR-008's own description of the
reference transport ("intentionally minimal, not a recommendation"):
no membership pruning or expiry, and no verification that a
discovered address is reachable before it's added to the scheduler.
``PeerScheduler``'s existing exponential backoff already demotes an
address that turns out to be unreachable the first time
``GossipService`` actually gossips with it (``scheduling.py``), so a
bad discovered address costs at most one wasted attempt, never a
permanent bad entry that has to be manually removed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cks_runtime.gossip.scheduling import PeerScheduler


class PeerDiscoveryError(Exception):
    """
    Raised by a ``PeerDiscovery`` implementation when a peer's
    known-peers list could not be retrieved (connection failure,
    timeout, malformed reply).

    Deliberately a single exception type across every implementation,
    mirroring ``GossipTransportError``'s role for session gossip:
    callers doing peer scheduling only need to know "this discovery
    attempt failed", not which lower-level exception caused it.
    """


class PeerDiscovery(ABC):
    """
    Asks one already-known peer which other peers it knows about.

    A single method, matching ``GossipTransport``'s own shape
    (``transport.py``): mechanism -- getting a peer list back from one
    address -- lives here; policy -- which peer to ask, how often, and
    what to do with the answer -- lives in ``GossipService`` and
    ``merge_discovered_peers`` below.
    """

    @abstractmethod
    async def fetch_peers(self, peer: str) -> list[str]:
        """
        Return the peer addresses ``peer`` reports knowing about.

        Implementations are expected (though not required by this
        protocol itself) to have the reachable side advertise its own
        address too -- see ``http_transport.GossipServer``'s
        ``self_address`` -- so a replica that was only ever dialed
        into, and never listed in anyone's static configuration, can
        still be discovered by a third party.

        Raises ``PeerDiscoveryError`` when ``peer`` could not be
        reached, or replied with something this implementation could
        not interpret as a peer list at all.
        """
        raise NotImplementedError


def merge_discovered_peers(
    scheduler: PeerScheduler,
    discovered: list[str],
    *,
    self_address: str | None = None,
) -> list[str]:
    """
    Add every address in ``discovered`` that ``scheduler`` doesn't
    already track, skipping ``self_address`` -- a replica must never
    schedule a gossip round against itself, which a naive merge could
    otherwise produce the moment a third peer's answer includes this
    replica's own advertised address.

    Returns the addresses that were actually new, for logging/testing
    only -- ``PeerScheduler.add_peer`` is already idempotent
    (``scheduling.py``), so calling this with addresses already known
    is always safe and a no-op for those.
    """
    known = set(scheduler.peers)
    newly_added: list[str] = []
    for address in discovered:
        if address == self_address or address in known:
            continue
        scheduler.add_peer(address)
        known.add(address)
        newly_added.append(address)
    return newly_added