"""
``GossipTransport`` -- the network boundary ADR-008 left open.

ADR-008's Decision section describes ``GossipTransport`` as "a
protocol... kept swappable (reference implementation: HTTP long-poll)"
-- Runtime's transport-independence invariant (see the ADR's Context
section) means nothing above this module may assume a specific wire
mechanism. This module defines that boundary; ``http_transport.py``
supplies the reference HTTP implementation.

``gossip_exchange_over_transport`` below is the network analogue of
``exchange.gossip_exchange``: that in-process function's own docstring
says it exists "as the reference sequence a real transport should
reproduce once one exists" -- fetch each side's snapshot of a shared
session and hand it to the other side's
``GossipAdapter.apply_remote_session``. This function reproduces that
sequence over a ``GossipTransport`` instead of two same-process
``Runtime`` instances, adding the envelope signing/verification and
replay-filtering a real network boundary needs that an in-process call
doesn't.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from cks_runtime.gossip.adapter import GossipAdapter
from cks_runtime.gossip.envelope import GossipEnvelope
from cks_runtime.gossip.filter import GossipFilter

logger = logging.getLogger(__name__)


class GossipTransportError(Exception):
    """
    Raised by a ``GossipTransport`` implementation when it cannot
    complete an exchange with a peer (connection refused, timeout,
    malformed response, ...).

    Deliberately a single exception type across every transport
    implementation -- callers doing peer scheduling (``scheduling.py``)
    only need to know "this peer failed", not which of the many
    lower-level exceptions (``aiohttp.ClientError`` and friends, for
    the HTTP transport) caused it. Implementations should chain the
    original exception via ``raise ... from original`` so it's still
    inspectable in logs/tracebacks.
    """


class GossipTransport(ABC):
    """
    Moves signed ``GossipEnvelope``\\ s between replicas.

    A single method, deliberately: transport implementations own
    *only* getting bytes to a peer and a response back, matching the
    Adapter pattern (ADR-006) split between mechanism (this class,
    ``http_transport.HTTPGossipTransport``) and policy (which peer,
    when, what to do with the result -- ``scheduling.py``,
    ``gossip_exchange_over_transport`` below).
    """

    @abstractmethod
    async def exchange(self, peer: str, envelope: GossipEnvelope) -> GossipEnvelope | None:
        """
        Send ``envelope`` to ``peer``, returning the peer's own
        current snapshot of ``envelope.session_id`` in reply.

        Returns ``None`` when the peer is reachable but doesn't track
        this session -- a normal, expected outcome (``GossipAdapter``
        only reconciles sessions both replicas already track; see its
        module docstring), not an error.

        Raises ``GossipTransportError`` when the peer could not be
        reached or returned something the transport couldn't
        interpret at all -- distinct from "reachable, no session",
        which is a successful ``None`` return, not an exception.
        """
        raise NotImplementedError


async def gossip_exchange_over_transport(
    session_id: str,
    adapter: GossipAdapter,
    transport: GossipTransport,
    peer: str,
    *,
    secret: bytes,
    seq_no: int,
    gossip_filter: GossipFilter | None = None,
) -> bool:
    """
    One push-pull gossip round for ``session_id`` against a single
    ``peer``, over ``transport``.

    Builds and signs a ``GossipEnvelope`` from this replica's current
    state of ``session_id``, sends it to ``peer`` via
    ``transport.exchange``, and -- if the peer replies with its own
    envelope -- verifies the signature, runs it through
    ``gossip_filter`` (when supplied; omitting it is only sensible for
    tests or a transport that already filters upstream, e.g. inside
    the HTTP server handler on the *other* end), and applies it
    through ``adapter.apply_remote_session``, exactly as
    ``exchange.gossip_exchange`` does in-process.

    ``seq_no`` must be a value this replica has never sent before, to
    any peer -- ``GossipFilter._check_sequence`` tracks it per sender
    only (not per sender+session), so it is a single counter a caller
    running gossip for several sessions must share across all of
    them; see ``scheduling.PeerScheduler`` / ``service.GossipService``
    for where that counter lives.

    Returns ``False`` when this replica has no local copy of
    ``session_id`` to gossip (nothing was sent), when the peer's reply
    fails signature verification or the replay filter, or when
    applying it produced a merge conflict (escalated by
    ``apply_remote_session`` via ``GossipConflictDetected``, not
    raised here). Returns ``True`` when either side had nothing new
    to contribute, or the exchange completed (fast-forward or
    successful merge) cleanly.
    """
    local_session = adapter._runtime.get_session(session_id)
    if local_session is None:
        logger.debug(
            "gossip_exchange_over_transport: no local session %s to gossip to %s",
            session_id,
            peer,
        )
        return False

    local_envelope = GossipEnvelope.from_session(
        local_session,
        sender_replica_id=adapter.replica_id,
        seq_no=seq_no,
        secret=secret,
    )

    remote_envelope = await transport.exchange(peer, local_envelope)
    if remote_envelope is None:
        return True

    if not remote_envelope.verify(secret):
        logger.warning(
            "gossip_exchange_over_transport: signature verification failed "
            "for reply from peer=%s claiming sender_replica_id=%s",
            peer,
            remote_envelope.sender_replica_id,
        )
        return False

    if gossip_filter is not None and not gossip_filter.check(
        remote_envelope.sender_replica_id,
        remote_envelope.nonce,
        remote_envelope.seq_no,
        remote_envelope.timestamp_ms,
    ):
        logger.warning(
            "gossip_exchange_over_transport: reply from peer=%s "
            "sender_replica_id=%s rejected by GossipFilter",
            peer,
            remote_envelope.sender_replica_id,
        )
        return False

    remote_session = remote_envelope.to_session()
    return await adapter.apply_remote_session(remote_session)