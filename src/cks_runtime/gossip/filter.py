"""
``GossipFilter`` -- replay, sequence, and clock-skew protection for
incoming ``GossipEnvelope``\\ s (ADR-008).

Adapted near-verbatim from a gossip-filter implementation used for a
different (evolutionary-algorithm P2P) project: the validator has no
dependency on that project's domain (genomes/fitness) or on this
one's (``RuntimeSession``/``KnowledgeStructure``) -- it only checks
sender id, nonce, sequence number, and timestamp, so it carries over
unchanged in behaviour. Renamed ``sender_node_id`` -> ``sender_replica_id``
throughout to match this repo's terminology (a gossip peer here is a
``replica_id``, ADR-008; ``node_id`` already means something different
and narrower -- per-session disambiguation, ADR-007).

Checked *before* ``GossipEnvelope.verify()`` in the HTTP transport's
request handler would be equally valid ordering-wise, but this repo
checks the HMAC signature first: a forged envelope should never get
to occupy a slot in the nonce cache or advance a sequence counter at
all, forged or not.

Sequence checking: a bounded reorder window, not a strict cursor
------------------------------------------------------------------
The original port from that other project rejected any ``seq_no`` not
*strictly greater* than the highest one seen so far. That is too
strict for how ``seq_no`` is actually produced and delivered here.

``SeqNoCounter`` (``seq_no.py``) deliberately hands out one shared
``seq_no`` stream per ``replica_id`` across *every* role that signs an
envelope under that identity -- both a ``GossipService`` initiating
its own rounds and a ``GossipServer`` replying to a peer-initiated
round share one counter (see that module's docstring for why: two
independent per-role counters were bug #1 it fixed). In a bidirectional
mesh -- replica A gossiping outbound to peer B while B's own
``GossipService`` is, at the same moment, gossiping inbound to A's
``GossipServer`` and receiving a reply -- both of A's outgoing streams
to B are in flight concurrently, over independent HTTP requests, with
no ordering relationship between when each was *allocated* its
``seq_no`` and when it actually *arrives* at B. A slower request
carrying a lower ``seq_no`` can legitimately land after a faster one
carrying a higher ``seq_no`` -- this is exactly the "ordinary shape of
a mesh under load, not an edge case" that ``GossipAdapter._lock_for``
already documents needing to handle for the merge side; the filter
needs the equivalent tolerance for delivery order.

A strict cursor treats that reordering identically to an actual
replay and drops the slower message permanently -- there is no retry
path anywhere in this package that would recover it. So instead:
every accepted ``seq_no`` is remembered (bounded, LRU-evicted, same
shape as the nonce cache) within a window behind the highest
``seq_no`` seen so far (``max_seq_reorder_window``). A ``seq_no``
already in that window is a genuine replay (rejected); one within the
window but not yet seen is accepted regardless of whether it is above
or below the current high-water mark; one far enough behind the
high-water mark to have fallen out of the window is rejected as too
old to plausibly be legitimate reordering. This keeps the same replay
guarantee (no ``seq_no`` is ever accepted twice) while no longer
mistaking "arrived a little late" for "replayed".
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from typing import Final

logger: Final = logging.getLogger(__name__)

#: How far behind the highest seq_no seen so far an incoming seq_no
#: may still land and be accepted, to absorb ordinary network/async
#: reordering between concurrent senders sharing one seq_no stream
#: (see module docstring). Not meant to be large -- realistic
#: reordering here is "a handful of concurrent in-flight requests",
#: not thousands -- so this also bounds how far a genuinely late
#: replay of an old seq_no could still land within the window (it
#: still has to guess an exact, not-yet-seen value to get through,
#: same as the nonce cache's own protection).
DEFAULT_SEQ_REORDER_WINDOW = 64


class GossipFilter:
    """Validate incoming gossip envelope metadata before it is applied."""

    __slots__ = (
        "_last_seq",
        "_seen_nonces",
        "_seen_seq",
        "max_clock_skew_ms",
        "max_nonce_cache",
        "max_seq_reorder_window",
    )

    def __init__(
        self,
        max_clock_skew_ms: int = 10_000,
        max_nonce_cache: int = 10_000,
        max_seq_reorder_window: int = DEFAULT_SEQ_REORDER_WINDOW,
    ) -> None:
        self.max_clock_skew_ms = max(0, int(max_clock_skew_ms))
        self.max_nonce_cache = max(1, int(max_nonce_cache))
        self.max_seq_reorder_window = max(1, int(max_seq_reorder_window))
        self._seen_nonces: dict[str, OrderedDict[str, None]] = {}
        # sender -> highest seq_no ever accepted (the high-water mark
        # the reorder window is measured back from).
        self._last_seq: dict[str, int] = {}
        # sender -> individual seq_no values accepted within the
        # current reorder window, for exact-duplicate detection.
        # Bounded by *value* relative to the high-water mark (see
        # _check_sequence), not by insertion-order LRU -- a value only
        # ever leaves this set once it has genuinely fallen behind the
        # window, never because other values happened to arrive after
        # it.
        self._seen_seq: dict[str, OrderedDict[int, None]] = {}

    def check(
        self,
        sender_replica_id: str,
        nonce: str | None,
        seq_no: int | None,
        timestamp_ms: int | None,
        ttl_ms: int | None = None,
    ) -> bool:
        """Return True when a gossip envelope passes replay/order/time checks."""
        sender = str(sender_replica_id or "").strip()
        if not sender:
            logger.debug("Gossip rejection: missing sender_replica_id")
            return False

        now_ms = int(time.time() * 1000)

        if timestamp_ms is not None and not self._check_timestamp(
            sender=sender,
            timestamp_ms=timestamp_ms,
            ttl_ms=ttl_ms,
            now_ms=now_ms,
        ):
            return False

        if nonce is not None and not self._check_nonce(sender=sender, nonce=nonce):
            return False

        return seq_no is None or self._check_sequence(sender=sender, seq_no=seq_no)

    def reset(self, sender_replica_id: str) -> None:
        """Clear cached replay/order state for one sender."""
        sender = str(sender_replica_id or "").strip()
        if not sender:
            return

        self._seen_nonces.pop(sender, None)
        self._last_seq.pop(sender, None)
        self._seen_seq.pop(sender, None)
        logger.info("GossipFilter state reset for sender=%s", sender)

    def clear(self) -> None:
        """Clear all cached replay/order state."""
        self._seen_nonces.clear()
        self._last_seq.clear()
        self._seen_seq.clear()
        logger.info("GossipFilter state cleared.")

    def stats(self) -> dict[str, int]:
        """Return lightweight cache statistics."""
        return {
            "senders_with_nonces": len(self._seen_nonces),
            "senders_with_sequences": len(self._last_seq),
            "nonce_count": sum(len(items) for items in self._seen_nonces.values()),
            "max_nonce_cache": self.max_nonce_cache,
            "max_clock_skew_ms": self.max_clock_skew_ms,
            "max_seq_reorder_window": self.max_seq_reorder_window,
        }

    def _check_timestamp(
        self,
        *,
        sender: str,
        timestamp_ms: int,
        ttl_ms: int | None,
        now_ms: int,
    ) -> bool:
        try:
            ts = int(timestamp_ms)
        except (TypeError, ValueError):
            logger.debug("Gossip rejection: invalid timestamp from %s: %r", sender, timestamp_ms)
            return False

        skew = abs(now_ms - ts)
        if skew > self.max_clock_skew_ms:
            logger.debug(
                "Gossip rejection: clock skew %sms exceeds limit %sms for %s",
                skew,
                self.max_clock_skew_ms,
                sender,
            )
            return False

        if ttl_ms is not None:
            try:
                ttl = int(ttl_ms)
            except (TypeError, ValueError):
                logger.debug("Gossip rejection: invalid ttl from %s: %r", sender, ttl_ms)
                return False

            if ttl < 0:
                logger.debug("Gossip rejection: negative ttl from %s: %s", sender, ttl)
                return False

            if now_ms > ts + ttl:
                logger.debug("Gossip rejection: expired message from %s", sender)
                return False

        return True

    def _check_nonce(self, *, sender: str, nonce: str) -> bool:
        clean_nonce = str(nonce or "").strip()
        if not clean_nonce:
            logger.debug("Gossip rejection: empty nonce from %s", sender)
            return False

        seen = self._seen_nonces.setdefault(sender, OrderedDict())
        if clean_nonce in seen:
            logger.debug("Gossip rejection: replay from %s nonce=%s", sender, clean_nonce)
            return False

        seen[clean_nonce] = None
        seen.move_to_end(clean_nonce)

        while len(seen) > self.max_nonce_cache:
            seen.popitem(last=False)

        return True

    def _check_sequence(self, *, sender: str, seq_no: int) -> bool:
        try:
            seq = int(seq_no)
        except (TypeError, ValueError):
            logger.debug("Gossip rejection: invalid seq_no from %s: %r", sender, seq_no)
            return False

        if seq < 0:
            logger.debug("Gossip rejection: negative seq_no from %s: %s", sender, seq)
            return False

        last_seq = self._last_seq.get(sender, -1)
        floor = last_seq - self.max_seq_reorder_window
        if seq <= floor:
            logger.debug(
                "Gossip rejection: seq_no from %s too far behind window "
                "seq=%s high_water=%s window=%s",
                sender,
                seq,
                last_seq,
                self.max_seq_reorder_window,
            )
            return False

        seen = self._seen_seq.setdefault(sender, OrderedDict())
        if seq in seen:
            logger.debug(
                "Gossip rejection: replayed seq_no from %s seq=%s", sender, seq
            )
            return False

        seen[seq] = None

        if seq > last_seq:
            self._last_seq[sender] = seq

        # Evict by *value* relative to the (possibly just-advanced)
        # high-water mark, not by insertion order. An insertion-order
        # LRU (the previous `popitem(last=False)` policy) can evict a
        # low seq_no that is still inside the reorder window -- e.g.
        # it arrived first but many higher, out-of-order values landed
        # right after it and pushed it out of a fixed-size LRU purely
        # because of *when* it arrived, not because it fell behind the
        # window. Once evicted, a replay of that exact value would
        # pass both the `seq in seen` check (no longer present) and
        # the `seq <= floor` check (still within window), silently
        # breaking the "no seq_no is ever accepted twice" guarantee
        # this module documents. Pruning by value keeps `seen` bounded
        # to the same window (a rejected-at-entry `seq <= floor` check
        # above already caps how far behind a value can be admitted in
        # the first place) while guaranteeing every value still inside
        # the window stays tracked for exact-duplicate detection.
        new_floor = self._last_seq[sender] - self.max_seq_reorder_window
        for stale_seq in [s for s in seen if s <= new_floor]:
            del seen[stale_seq]

        return True