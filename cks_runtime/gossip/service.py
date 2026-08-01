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
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from cks_runtime.gossip.adapter import GossipAdapter
from cks_runtime.gossip.filter import GossipFilter
from cks_runtime.gossip.scheduling import PeerScheduler
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

        self._seq_no = 0
        self._running = False
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._running

    @property
    def tracked_sessions(self) -> tuple[str, ...]:
        return tuple(self._session_ids)

    def track_session(self, session_id: str) -> None:
        """Add a session to the set gossiped every round, if not already tracked."""
        if session_id not in self._session_ids:
            self._session_ids.append(session_id)

    def untrack_session(self, session_id: str) -> None:
        """Stop gossiping a session (e.g. it was closed locally)."""
        if session_id in self._session_ids:
            self._session_ids.remove(session_id)

    def _next_seq_no(self) -> int:
        self._seq_no += 1
        return self._seq_no

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

        return peer

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Launch the background gossip loop. No-op if already running."""
        if self._running:
            logger.debug("GossipService already running.")
            return
        self._running = True
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