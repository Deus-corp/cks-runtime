"""
``GossipService`` -- the periodic anti-entropy loop ADR-008 names but
leaves undecided ("Nothing decides when to gossip, to whom, or how to
escalate a conflict found outside a synchronous caller" -- the
conflict-escalation half is already handled inside
``GossipAdapter.apply_remote_session`` itself; this module is the
"when" and "to whom" half).

Ties together the pieces the rest of this package defines:
``GossipAdapter`` (merge semantics, already implemented), a
``GossipTransport`` (network mechanics, ``http_transport.py``),
``PeerScheduler`` (peer choice + backoff, ``scheduling.py``), and
``GossipFilter`` (replay protection, ``filter.py``) into a background
loop. One round chooses a peer via the scheduler and gossips every
tracked session with it, generalizing the single-peer,
single-round ``gossip_round()`` shape from another project's gossip
adapter (full source available and reused for the pattern) over a
*set* of tracked ``RuntimeSession``\\ s -- gossip here is per-session,
unlike that project's single global store, so one round talks to one
peer about however many sessions this replica is tracking.

Also, optionally, folds in peer discovery (``discovery.py``): after a
successful round with a peer, that same peer is asked which other
peers it knows about (piggy-backing discovery on a round that already
proved the peer reachable, rather than a separate scheduled activity
of its own), and any newly learned addresses are merged into
``scheduler``. Omitting ``discovery`` (the default) leaves peer
membership exactly as static as before this feature existed --
nothing about the core anti-entropy loop depends on it.

``seq_no``, one shared source per replica: this service draws every
outgoing ``seq_no`` from a ``SeqNoCounter`` (``seq_no.py``) -- by
default one built from ``adapter.replica_id``, persisted, and safe to
share (even implicitly, via the same default file) with a
``GossipServer`` replying on this same replica's behalf. See
``seq_no.py``'s module docstring for why a single in-memory counter
here was not enough: this replica also replies to peer-initiated
rounds through ``GossipServer``, under the same ``sender_replica_id``,
and the two must never hand out overlapping values.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from cks_runtime.gossip.adapter import GossipAdapter
from cks_runtime.gossip.discovery import (
    PeerDiscovery,
    PeerDiscoveryError,
    merge_discovered_peers,
)
from cks_runtime.gossip.filter import GossipFilter
from cks_runtime.gossip.scheduling import PeerScheduler
from cks_runtime.gossip.seq_no import SeqNoCounter
from cks_runtime.gossip.transport import (
    GossipTransport,
    GossipTransportError,
    gossip_exchange_over_transport,
)

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 5.0


class GossipService:
    """
    Runs periodic gossip rounds for a fixed ``GossipAdapter`` against
    a configurable set of peers (via ``scheduler``) and tracked
    sessions.

    Not started automatically -- call ``start()`` to launch the
    background loop (``asyncio.create_task``) or call ``gossip_round()``
    directly for a single explicit round (e.g. from a test, or a
    caller that wants to control timing itself rather than run a free
    background loop).

    ``discovery``, when supplied, is consulted once per round after a
    successful exchange (see ``gossip_round``) to grow ``scheduler``'s
    peer set beyond whatever it was configured with -- ADR-008's own
    "Peer discovery" follow-up item. ``self_address`` (this replica's
    own externally-reachable address) is passed through to
    ``merge_discovered_peers`` so a peer's answer that happens to
    include this replica's own address is never scheduled against
    itself.
    """

    def __init__(
        self,
        adapter: GossipAdapter,
        transport: GossipTransport,
        scheduler: PeerScheduler,
        *,
        secret: bytes,
        session_ids: list[str] | None = None,
        gossip_filter: GossipFilter | None = None,
        interval_s: float = DEFAULT_INTERVAL_S,
        discovery: PeerDiscovery | None = None,
        self_address: str | None = None,
        seq_no_counter: SeqNoCounter | None = None,
    ) -> None:
        if interval_s <= 0:
            raise ValueError(f"interval_s must be > 0, got {interval_s!r}.")

        self._adapter = adapter
        self._transport = transport
        self._scheduler = scheduler
        self._secret = secret
        self._session_ids: list[str] = list(session_ids) if session_ids else []
        self._filter = gossip_filter if gossip_filter is not None else GossipFilter()
        self.interval_s = interval_s
        self._discovery = discovery
        self._self_address = self_address

        # See seq_no.py's module docstring: defaulting to a fresh
        # SeqNoCounter for adapter.replica_id (persisted, unless the
        # caller overrides) is what lets this run alongside a
        # GossipServer for the same replica without either one having
        # to be told about the other explicitly.
        self._seq_no_counter = (
            seq_no_counter if seq_no_counter is not None else SeqNoCounter(adapter.replica_id)
        )
        self._running = False
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._running

    @property
    def tracked_sessions(self) -> tuple[str, ...]:
        return tuple(self._session_ids)

    @property
    def seq_no_counter(self) -> SeqNoCounter:
        """
        The ``SeqNoCounter`` this service signs outgoing envelopes
        with. Pass it to a ``GossipServer`` for the same
        ``adapter.replica_id`` (``GossipServer(..., seq_no_counter=
        service.seq_no_counter)``) to share it explicitly and skip the
        file round-trip ``SeqNoCounter.next()`` otherwise does each
        call to reconcile against a sibling instance -- not required
        for correctness (see ``seq_no.py``), just an optimization.
        """
        return self._seq_no_counter

    def track_session(self, session_id: str) -> None:
        """Add a session to the set gossiped every round, if not already tracked."""
        if session_id not in self._session_ids:
            self._session_ids.append(session_id)

    def untrack_session(self, session_id: str) -> None:
        """Stop gossiping a session (e.g. it was closed locally)."""
        if session_id in self._session_ids:
            self._session_ids.remove(session_id)

    def _next_seq_no(self) -> int:
        return self._seq_no_counter.next()

    # ------------------------------------------------------------------
    # One explicit round
    # ------------------------------------------------------------------

    async def gossip_round(self) -> str | None:
        """
        Choose one eligible peer via ``scheduler`` and gossip every
        tracked session with it.

        Returns the chosen peer (or ``None`` if every known peer is
        currently backed off / there are no peers). Stops early --
        without attempting remaining sessions -- on the first
        ``GossipTransportError`` from that peer this round, recording
        the failure so ``scheduler`` backs off from it; a peer that's
        actually down will fail identically for every session, so
        there's nothing to learn from retrying it several times in
        one round.

        When ``discovery`` was supplied and every tracked session
        exchanged cleanly (or there were none to begin with -- a
        freshly bootstrapping replica with no sessions yet should
        still be able to grow its peer list), that same peer is also
        asked which other peers it knows about (see ``_discover_from``).
        A peer that failed session gossip this round is never also
        asked for peers -- it already needed backing off.
        """
        peer = self._scheduler.choose_peer()
        if peer is None:
            logger.debug("GossipService.gossip_round: no eligible peer.")
            return None

        for session_id in list(self._session_ids):
            try:
                await gossip_exchange_over_transport(
                    session_id,
                    self._adapter,
                    self._transport,
                    peer,
                    secret=self._secret,
                    seq_no=self._next_seq_no(),
                    gossip_filter=self._filter,
                )
            except GossipTransportError as exc:
                logger.warning(
                    "GossipService: peer=%s failed during session=%s: %s",
                    peer,
                    session_id,
                    exc,
                )
                self._scheduler.record_failure(peer)
                return peer
            else:
                self._scheduler.record_success(peer)

        if self._discovery is not None:
            await self._discover_from(peer)

        return peer

    async def _discover_from(self, peer: str) -> list[str]:
        """
        Ask ``peer`` which other peers it knows about (peer-exchange,
        ``discovery.py``) and merge any new addresses into
        ``scheduler``.

        Best-effort and strictly additive, matching how
        ``fetch_operations_since``/the operation log are opt-in
        accelerants elsewhere in this package: a peer that doesn't
        support discovery (an older deployment, or a
        ``GossipTransport``/``PeerDiscovery`` pairing where discovery
        was never wired up on its end) must not stop session gossip
        from working, so a ``PeerDiscoveryError`` here is logged and
        swallowed rather than propagated out of the caller (both
        ``gossip_round`` and ``discover_peers`` below rely on that).

        Returns the addresses newly added to ``scheduler`` (possibly
        empty), for ``discover_peers`` to aggregate across several
        peers in one bootstrap pass.
        """
        assert self._discovery is not None
        try:
            discovered = await self._discovery.fetch_peers(peer)
        except PeerDiscoveryError as exc:
            logger.warning("GossipService: peer discovery via %s failed: %s", peer, exc)
            return []

        newly_added = merge_discovered_peers(
            self._scheduler, discovered, self_address=self._self_address
        )
        if newly_added:
            logger.info("GossipService: discovered new peers via %s: %s", peer, newly_added)
        return newly_added

    async def discover_peers(self) -> list[str]:
        """
        Ask every peer ``scheduler`` currently knows about for their
        own known-peer lists, and merge every answer in.

        This is the "at start" half of the peer-discovery follow-up
        item ADR-008 itself names: ``gossip_round``'s own discovery
        step only ever asks the *one* peer chosen for that round,
        which means a service configured with nothing but a handful
        of static seed addresses (the PEX bootstrap model
        ``discovery.py`` describes -- "grow a full membership view
        from a handful of seed addresses") would otherwise have to
        wait for its first successful session-gossip round before
        membership could grow at all. ``start()`` calls this once,
        before the periodic loop begins, so a fresh replica's peer
        set is as complete as its seeds can make it from the first
        round onward -- every later round's own post-round discovery
        (``gossip_round`` / ``_discover_from``) keeps it growing
        epidemically from there.

        Queries ``scheduler.peers`` as it stood at the moment this was
        called (a snapshot -- ``PeerScheduler.peers`` already returns
        a tuple, not a live view) rather than transitively following
        newly discovered addresses within the same pass; a peer
        discovered via this call is asked in turn the next time
        ``discover_peers`` or a round's own ``_discover_from`` runs,
        matching how epidemic membership protocols converge over
        several rounds rather than one BFS traversal. A no-op,
        returning ``[]``, when ``discovery`` was never configured or
        ``scheduler`` starts with no peers at all (nothing to ask).

        Best-effort per peer, same as ``_discover_from``: one seed
        failing to answer does not stop the others from being asked.
        """
        if self._discovery is None:
            return []

        newly_added: list[str] = []
        for peer in self._scheduler.peers:
            newly_added.extend(await self._discover_from(peer))
        return newly_added

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """
        Launch the background gossip loop. No-op if already running.

        When ``discovery`` was supplied, this first runs
        ``discover_peers()`` once -- growing ``scheduler`` beyond its
        static seed list immediately, rather than only reactively
        after each round's chosen peer succeeds (see
        ``discover_peers``'s docstring) -- before the periodic loop
        begins.
        """
        if self._running:
            logger.debug("GossipService already running.")
            return
        self._running = True
        if self._discovery is not None:
            await self.discover_peers()
        self._task = asyncio.ensure_future(self._run_forever())

    async def stop(self) -> None:
        """Stop the background gossip loop and await its cancellation."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run_forever(self) -> None:
        while self._running:
            try:
                await self.gossip_round()
            except Exception:
                # A round failing outright (as opposed to a single
                # peer's GossipTransportError, already handled inside
                # gossip_round) should not kill the background loop --
                # log it and try again next interval.
                logger.exception("GossipService: unhandled error in gossip_round.")
            await asyncio.sleep(self.interval_s)